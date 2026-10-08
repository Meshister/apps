"""Thin client for the WhatsApp Cloud API (Graph API /messages endpoint)."""

import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)


@dataclass
class Text:
    body: str


@dataclass
class Buttons:
    """Up to 3 reply buttons. Each item is (id, title); titles max 20 chars."""

    body: str
    buttons: list[tuple[str, str]]
    header: str | None = None
    footer: str | None = None


@dataclass
class ListMenu:
    """A list picker with up to 10 rows. Each row is (id, title, description)."""

    body: str
    button: str
    rows: list[tuple[str, str, str]]
    header: str | None = None
    footer: str | None = None
    section_title: str = "Options"


@dataclass
class Document:
    """A file sent as a WhatsApp document (uploaded first, then sent)."""

    filename: str
    content: bytes
    caption: str = ""
    mime_type: str = "text/plain"


Reply = Text | Buttons | ListMenu | Document


@dataclass
class IncomingMessage:
    message_id: str
    sender: str  # the user's phone number (wa_id)
    name: str
    text: str = ""  # free text typed by the user
    choice_id: str | None = None  # id of a tapped button / list row
    kind: str = "text"
    raw: dict = field(default_factory=dict, repr=False)


def parse_webhook(payload: dict) -> list[IncomingMessage]:
    """Extract user messages from a webhook payload. Status updates (sent/delivered/read) are ignored."""
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                kind = msg.get("type", "")
                incoming = IncomingMessage(
                    message_id=msg.get("id", ""),
                    sender=msg.get("from", ""),
                    name=names.get(msg.get("from"), ""),
                    kind=kind,
                    raw=msg,
                )
                if kind == "text":
                    incoming.text = msg.get("text", {}).get("body", "")
                elif kind == "interactive":
                    interactive = msg.get("interactive", {})
                    reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
                    incoming.choice_id = reply.get("id")
                    incoming.text = reply.get("title", "")
                elif kind == "button":  # quick-reply button on a template message
                    incoming.text = msg.get("button", {}).get("text", "")
                    incoming.choice_id = msg.get("button", {}).get("payload")
                messages.append(incoming)
    return messages


def to_payload(to: str, reply: Reply) -> dict:
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to}
    if isinstance(reply, Text):
        return {**base, "type": "text", "text": {"body": reply.body, "preview_url": True}}

    if isinstance(reply, Buttons):
        action = {
            "buttons": [{"type": "reply", "reply": {"id": bid, "title": title}} for bid, title in reply.buttons]
        }
    else:
        action = {
            "button": reply.button,
            "sections": [
                {
                    "title": reply.section_title,
                    "rows": [
                        {"id": rid, "title": title, **({"description": desc} if desc else {})}
                        for rid, title, desc in reply.rows
                    ],
                }
            ],
        }
    interactive: dict = {
        "type": "button" if isinstance(reply, Buttons) else "list",
        "body": {"text": reply.body},
        "action": action,
    }
    if reply.header:
        interactive["header"] = {"type": "text", "text": reply.header}
    if reply.footer:
        interactive["footer"] = {"text": reply.footer}
    return {**base, "type": "interactive", "interactive": interactive}


class WhatsAppClient:
    def __init__(self, token: str, phone_number_id: str, api_version: str):
        self._http = httpx.AsyncClient(
            base_url=f"https://graph.facebook.com/{api_version}/{phone_number_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )

    async def send(self, to: str, reply: Reply) -> None:
        if isinstance(reply, Document):
            payload = await self._document_payload(to, reply)
            if payload is None:
                return
        else:
            payload = to_payload(to, reply)
        resp = await self._http.post("/messages", json=payload)
        if resp.is_error:
            log.error("Send to %s failed (%s): %s", to, resp.status_code, resp.text)

    async def _document_payload(self, to: str, doc: Document) -> dict | None:
        resp = await self._http.post(
            "/media",
            data={"messaging_product": "whatsapp", "type": doc.mime_type},
            files={"file": (doc.filename, doc.content, doc.mime_type)},
        )
        if resp.is_error:
            log.error("Upload of %s failed (%s): %s", doc.filename, resp.status_code, resp.text)
            return None
        document = {"id": resp.json()["id"], "filename": doc.filename}
        if doc.caption:
            document["caption"] = doc.caption
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                "type": "document", "document": document}

    async def mark_read(self, message_id: str) -> None:
        resp = await self._http.post(
            "/messages", json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        )
        if resp.is_error:
            log.warning("mark_read failed (%s): %s", resp.status_code, resp.text)

    async def aclose(self) -> None:
        await self._http.aclose()
