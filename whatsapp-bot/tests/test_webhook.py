import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

SECRET = "s3cret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    get_settings.cache_clear()
    from app.main import app

    sent = []
    with TestClient(app) as c:

        async def fake_send(to, reply):
            sent.append((to, reply))

        async def fake_read(message_id):
            pass

        app.state.client.send = fake_send
        app.state.client.mark_read = fake_read
        c.sent = sent
        yield c
    get_settings.cache_clear()


def signed(body: dict):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}


def text_payload(msg_id="w1", body="hi"):
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "4917", "profile": {"name": "Ana"}}],
                            "messages": [{"id": msg_id, "from": "4917", "type": "text", "text": {"body": body}}],
                        }
                    }
                ]
            }
        ]
    }


def test_verify(client):
    params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"}
    r = client.get("/webhook", params=params)
    assert r.status_code == 200 and r.text == "42"
    r = client.get("/webhook", params={**params, "hub.verify_token": "wrong"})
    assert r.status_code == 403


def test_rejects_bad_signature(client):
    raw, headers = signed(text_payload())
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    assert client.post("/webhook", content=raw, headers=headers).status_code == 401
    assert client.sent == []


def test_replies_once_per_message(client):
    raw, headers = signed(text_payload())
    assert client.post("/webhook", content=raw, headers=headers).status_code == 200
    assert client.post("/webhook", content=raw, headers=headers).status_code == 200  # Meta retry
    assert len(client.sent) == 1
    assert client.sent[0][0] == "4917"
