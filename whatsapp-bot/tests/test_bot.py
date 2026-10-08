from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.bot import Bot, FlowError, load_flows
from app.whatsapp import Buttons, IncomingMessage, ListMenu, Text, parse_webhook, to_payload

FLOWS = Path(__file__).resolve().parent.parent / "flows.yaml"
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def bot():
    return Bot(load_flows(FLOWS), session_ttl=timedelta(minutes=30), handoff_duration=timedelta(minutes=60))


def msg(text="", choice=None, sender="111", name="Ana"):
    return IncomingMessage(message_id="m", sender=sender, name=name, text=text, choice_id=choice)


def test_example_flows_are_valid():
    load_flows(FLOWS)


def test_new_user_gets_main_menu(bot):
    [reply] = bot.handle(msg("yo"), T0)
    assert isinstance(reply, ListMenu)
    assert "Hi Ana" in reply.body
    assert [r[0] for r in reply.rows] == ["products", "order_status", "hours", "faq", "human"]


def test_new_user_keyword_answered_directly(bot):
    [reply] = bot.handle(msg("what are your opening hours?"), T0)
    assert isinstance(reply, Text) and "Mon–Fri" in reply.body


def test_tap_option_goes_to_submenu_and_back(bot):
    bot.handle(msg("hi"), T0)
    [reply] = bot.handle(msg(choice="products"), T0)
    assert isinstance(reply, Buttons)
    assert [b[0] for b in reply.buttons] == ["cat_clothing", "cat_shoes", "cat_back"]
    [reply] = bot.handle(msg("back"), T0)
    assert isinstance(reply, ListMenu)


def test_number_and_title_select_option_in_current_menu(bot):
    bot.handle(msg("menu"), T0)
    [reply] = bot.handle(msg("3"), T0)
    assert "Mon–Fri" in reply.body
    bot.handle(msg("FAQ"), T0)  # by title, case-insensitive
    [reply] = bot.handle(msg("2"), T0)
    assert "30 days" in reply.body


def test_unknown_text_gets_fallback(bot):
    bot.handle(msg("hi"), T0)
    [reply] = bot.handle(msg("asdfgh"), T0)
    assert isinstance(reply, Text) and "Sorry Ana" in reply.body


def test_handoff_silences_bot_until_menu_or_timeout(bot):
    bot.handle(msg("hi"), T0)
    [reply] = bot.handle(msg(choice="human"), T0)
    assert "team member" in reply.body
    assert bot.handle(msg("hello?"), T0 + timedelta(minutes=5)) == []
    assert bot.handle(msg("1"), T0 + timedelta(minutes=40)) == []  # paused session isn't expired
    replies = bot.handle(msg("menu"), T0 + timedelta(minutes=10))
    assert isinstance(replies[0], Text) and isinstance(replies[1], ListMenu)

    bot.handle(msg(choice="human"), T0)
    assert bot.handle(msg("thanks"), T0 + timedelta(minutes=61))  # pause expired: bot answers again


def test_session_expires(bot):
    bot.handle(msg("hi"), T0)
    bot.handle(msg(choice="faq"), T0)
    [reply] = bot.handle(msg("2"), T0 + timedelta(hours=2))  # treated as a new user again
    assert isinstance(reply, ListMenu) and reply.rows[0][0] == "products"


def test_users_are_independent(bot):
    bot.handle(msg("hi", sender="1"), T0)
    bot.handle(msg(choice="faq", sender="1"), T0)
    bot.handle(msg("hi", sender="2"), T0)
    assert "Mon–Fri" in bot.handle(msg("3", sender="2"), T0)[0].body
    assert "Cards" not in bot.handle(msg("3", sender="1"), T0)[0].body


def test_invalid_flows_are_rejected(tmp_path):
    bad = tmp_path / "flows.yaml"
    bad.write_text(
        """
start_menu: main
menus:
  main:
    body: hi
    style: buttons
    options:
      - {id: a, title: "This title is far too long for a button", reply: x}
      - {id: a, title: B, goto: nowhere}
"""
    )
    with pytest.raises(FlowError) as e:
        load_flows(bad)
    text = str(e.value)
    assert "title longer than 20" in text and "already used" in text and "unknown menu 'nowhere'" in text


def test_parse_webhook_text_and_interactive():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "4917", "profile": {"name": "Ana"}}],
                            "messages": [
                                {"id": "w1", "from": "4917", "type": "text", "text": {"body": "hi"}},
                                {
                                    "id": "w2",
                                    "from": "4917",
                                    "type": "interactive",
                                    "interactive": {"type": "list_reply", "list_reply": {"id": "faq", "title": "FAQ"}},
                                },
                            ],
                        }
                    },
                    {"value": {"statuses": [{"id": "w0", "status": "read"}]}},
                ]
            }
        ]
    }
    m1, m2 = parse_webhook(payload)
    assert (m1.sender, m1.name, m1.text, m1.choice_id) == ("4917", "Ana", "hi", None)
    assert (m2.choice_id, m2.text) == ("faq", "FAQ")


def test_payload_shapes():
    p = to_payload("1", Buttons(body="b", buttons=[("x", "X")], header="h"))
    assert p["interactive"]["type"] == "button"
    assert p["interactive"]["action"]["buttons"][0]["reply"] == {"id": "x", "title": "X"}
    p = to_payload("1", ListMenu(body="b", button="Go", rows=[("x", "X", "")]))
    assert p["interactive"]["action"]["sections"][0]["rows"] == [{"id": "x", "title": "X"}]
