import hashlib
import hmac
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .config import get_settings
from .journal import Journal, load_config
from .store import Store
from .whatsapp import IncomingMessage, WhatsAppClient, parse_webhook

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("whatsapp-bot")


class SeenMessages:
    """Meta may deliver the same webhook more than once; remember recent message ids to reply only once."""

    def __init__(self, size: int = 5000):
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def add(self, message_id: str) -> bool:
        """Returns False if the id was already seen."""
        if message_id in self._ids:
            return False
        self._ids[message_id] = None
        if len(self._ids) > self._size:
            self._ids.popitem(last=False)
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    store = Store(settings.database_file)
    app.state.journal = Journal(
        load_config(settings.journal_file),
        store,
        tz=ZoneInfo(settings.timezone),
        draft_ttl=timedelta(hours=settings.draft_ttl_hours),
    )
    app.state.client = WhatsAppClient(
        settings.whatsapp_token, settings.whatsapp_phone_number_id, settings.graph_api_version
    )
    app.state.seen = SeenMessages()
    if not settings.whatsapp_app_secret:
        log.warning("WHATSAPP_APP_SECRET is not set: webhook signatures will NOT be verified")
    if not settings.owners:
        log.warning("OWNER_NUMBERS is not set: anyone who messages this number can use the journal")
    yield
    await app.state.client.aclose()
    store.close()


app = FastAPI(title="WhatsApp emotion journal", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(
    request: Request,
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    """Meta calls this once when you register the webhook URL."""
    expected = request.app.state.settings.whatsapp_verify_token
    if mode == "subscribe" and hmac.compare_digest(token, expected):
        return challenge
    raise HTTPException(status_code=403, detail="Verification failed")


def signature_ok(secret: str, body: bytes, header: str | None) -> bool:
    if not secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), digest)


@app.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not signature_ok(
        request.app.state.settings.whatsapp_app_secret, body, request.headers.get("X-Hub-Signature-256")
    ):
        raise HTTPException(status_code=401, detail="Bad signature")

    for msg in parse_webhook(await request.json()):
        if request.app.state.seen.add(msg.message_id):
            # Reply after returning 200: Meta retries webhooks that are slow to answer
            background.add_task(process_message, request.app, msg)
    return {"status": "ok"}


async def process_message(app: FastAPI, msg: IncomingMessage) -> None:
    try:
        owners = app.state.settings.owners
        if owners and msg.sender not in owners:
            log.info("Ignoring message from %s (not in OWNER_NUMBERS)", msg.sender)
            return
        # Journal text is private, so only the message type is logged
        log.info("Message from %s (%s)", msg.sender, msg.kind)
        replies = app.state.journal.handle(msg, datetime.now(timezone.utc))
        if replies:
            await app.state.client.mark_read(msg.message_id)
        for reply in replies:
            await app.state.client.send(msg.sender, reply)
    except Exception:
        log.exception("Failed to handle message %s", msg.message_id)
