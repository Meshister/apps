from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.journal import OTHER, ConfigError, Journal, load_config, paginate
from app.store import Store
from app.whatsapp import Buttons, Document, IncomingMessage, ListMenu, Text, parse_webhook, to_payload

CONFIG = Path(__file__).resolve().parent.parent / "journal.yaml"
T0 = datetime(2026, 10, 8, 18, 30, tzinfo=timezone.utc)


@pytest.fixture
def journal():
    return Journal(load_config(CONFIG), Store(":memory:"), ZoneInfo("Asia/Jerusalem"), timedelta(hours=12))


def msg(text="", choice=None, sender="111", kind="text"):
    return IncomingMessage(message_id="m", sender=sender, name="Me", text=text, choice_id=choice, kind=kind)


def titles(reply):
    return [t for _, t, _ in reply.rows] if isinstance(reply, ListMenu) else [t for _, t in reply.buttons]


def tap(journal, reply, title, sender="111", when=T0):
    """Tap the row/button with this title on a reply."""
    items = reply.rows if isinstance(reply, ListMenu) else [(i, t, "") for i, t in reply.buttons]
    choice = next(rid for rid, t, _ in items if t == title)
    return journal.handle(msg(choice=choice, sender=sender), when)


def start_wheel(journal, sender="111", when=T0):
    """Start an entry and answer the opening questions; returns the inner-circle list."""
    [reply] = journal.handle(msg("new", sender=sender), when)
    for answer in ["Sweets / chocolate", "No", "Guilty", "Proud"]:
        [reply] = tap(journal, reply, answer, sender, when)
    return reply


def write_entry(journal, sender="111", writing="Big meeting tomorrow", when=T0):
    reply = start_wheel(journal, sender, when)
    [reply] = tap(journal, reply, "Fear", sender, when)
    [reply] = tap(journal, reply, "Nervous", sender, when)
    explain, reply = tap(journal, reply, "Anxious", sender, when)
    for answer in ["7", "Chest", "Home", "Close family", "Work", "Rest"]:
        [reply] = tap(journal, reply, answer, sender, when)
    return explain, journal.handle(msg(writing, sender=sender), when)


def test_config_is_valid():
    cfg = load_config(CONFIG)
    assert list(cfg.wheel) == ["Fear", "Anger", "Sadness", "Surprise", "Joy", "Love"]
    assert sum(len(m.words) for ms in cfg.wheel.values() for m in ms.values()) == 68
    assert all(m.similar for ms in cfg.wheel.values() for m in ms.values())


def test_any_message_shows_main_menu(journal):
    [reply] = journal.handle(msg("hey there"), T0)
    assert isinstance(reply, Buttons)
    assert titles(reply) == ["📝 New entry", "📖 My journal", "📤 Export journal"]


def test_opening_questions_then_wheel(journal):
    [q] = journal.handle(msg(choice="m:new"), T0)
    assert q.body.startswith("What food triggers the urge?")
    assert titles(q)[-1] == OTHER
    [q] = tap(journal, q, OTHER)
    assert "Type your answer" in q.body
    [q] = journal.handle(msg("Pizza"), T0)
    assert isinstance(q, Buttons) and titles(q) == ["Yes", "No", "Not sure"]
    [q] = tap(journal, q, "Not sure")
    assert q.body.startswith("How will I feel if I eat it?")
    [q] = tap(journal, q, "Guilty")
    assert q.body.startswith("How will I feel if I don't eat it?")
    [inner] = tap(journal, q, "Proud")
    assert inner.body == "What emotion is driving me to eat?" and len(inner.rows) == 6
    draft = journal.store.get_state("111")["draft"]
    assert [a["a"] for a in draft["opening"]] == ["Pizza", "Not sure", "Guilty", "Proud"]


def test_full_entry_goes_inner_to_outer_then_questions(journal):
    inner = start_wheel(journal)
    [middle] = tap(journal, inner, "Fear")
    assert titles(middle) == ["Scared", "Terror", "Insecure", "Nervous", "Horror", "⬅️ Back"]
    assert middle.rows[3][2] == "uneasy, jittery, tense"  # similar words on the middle ring
    [outer] = tap(journal, middle, "Nervous")
    assert outer.rows[1][1:] == ("Anxious", "tense, restless, on edge")

    explain, first_q = tap(journal, outer, "Anxious")
    assert "*Anxious*" in explain.body and "Try instead" in explain.body and "Reflect" in explain.body
    assert first_q.body == "How strong is the feeling?" and len(first_q.rows) == 10

    [body] = tap(journal, first_q, "7")
    assert "page 1 of 2" in body.body and len(body.rows) == 10 and titles(body)[-1] == "More ➡️"
    [body2] = tap(journal, body, "More ➡️")
    assert titles(body2) == [
        "⬅️ Previous", "Lower belly", "Back", "Arms", "Hands", "Legs", "Feet", "Whole body", "Nowhere"
    ]
    [body] = tap(journal, body2, "⬅️ Previous")
    assert titles(body)[0] == "Head"
    [place] = tap(journal, body, "Chest")
    assert titles(place) == ["Home", "Work", "Outside", "Car", OTHER]
    [typing] = tap(journal, place, OTHER)
    assert "Type your answer" in typing.body
    [company] = journal.handle(msg("Gym"), T0)
    assert titles(company) == ["Alone", "Partner", "Close family", "Wider family", "Friends", "Work people", OTHER]
    [reply] = tap(journal, company, "Close family")
    for answer in ["Work", "Rest"]:
        [reply] = tap(journal, reply, answer)
    assert isinstance(reply, Text) and "Free writing" in reply.body

    saved, menu = journal.handle(msg("Big meeting tomorrow, can't stop thinking about it"), T0)
    assert "Saved" in saved.body
    assert "What food triggers the urge? Sweets / chocolate" in saved.body
    assert "Fear → Nervous → Anxious" in saved.body
    assert "Thu 08 Oct 2026, 21:30" in saved.body  # shown in the configured time zone
    assert "Where are you? Gym" in saved.body
    assert "Try instead:* Take a 10-minute walk" in saved.body
    assert isinstance(menu, Buttons)
    assert journal.store.get_state("111") is None


def test_typed_numbers_words_back_skip_and_more(journal):
    start_wheel(journal)
    [middle] = journal.handle(msg("joy"), T0)  # word typed instead of tapped
    assert "Joy" in middle.body
    [inner] = journal.handle(msg("back"), T0)
    assert len(inner.rows) == 6
    journal.handle(msg("6"), T0)  # Love
    [outer] = journal.handle(msg("6"), T0)  # Peaceful
    assert titles(outer) == ["Relieved", "Satisfied", "⬅️ Back"]
    journal.handle(msg("1"), T0)  # Relieved
    [reply] = journal.handle(msg("skip"), T0)
    assert reply.body.startswith("Where do you feel it in your body?")
    [reply] = journal.handle(msg("more"), T0)
    assert "page 2 of 2" in reply.body
    [reply] = journal.handle(msg("what?"), T0)[1:]  # not an option: asked again
    assert reply.body.startswith("Where do you feel it in your body?")
    [reply] = journal.handle(msg("feet"), T0)  # words from any page work
    assert reply.body == "Where are you?"


def test_stale_tap_on_old_message_is_not_taken_as_answer(journal):
    inner = start_wheel(journal)
    tap(journal, inner, "Fear")
    hint, again = tap(journal, inner, "Joy")  # tapping the old inner list again
    assert "choose" in hint.body and "Fear" in again.body


def test_menu_cancels_unfinished_entry(journal):
    journal.handle(msg("new"), T0)
    journal.handle(msg("1"), T0)
    [reply] = journal.handle(msg("menu"), T0)
    assert "discarded" in reply.body
    assert journal.store.get_state("111") is None


def test_unfinished_entry_expires(journal):
    journal.handle(msg("new"), T0)
    [reply] = journal.handle(msg("anxious"), T0 + timedelta(hours=13))
    assert isinstance(reply, Buttons)  # back at the main menu


def test_free_writing_needs_text(journal):
    start_wheel(journal)
    for answer in ["1", "1", "1", "skip", "skip", "skip", "skip", "skip", "skip"]:
        journal.handle(msg(answer), T0)
    replies = journal.handle(msg("", kind="audio"), T0)
    assert "only save typed text" in replies[0].body


def test_my_journal_and_export(journal):
    [empty, _] = journal.handle(msg(choice="m:view"), T0)
    assert "empty" in empty.body
    write_entry(journal, writing="first")
    write_entry(journal, writing="second", when=T0 + timedelta(days=1))
    write_entry(journal, sender="222", writing="someone else")

    view = journal.handle(msg("my journal"), T0)
    assert "first" in view[0].body and "second" in view[0].body and "someone else" not in view[0].body

    [doc, menu] = journal.handle(msg(choice="m:export"), T0)
    assert isinstance(doc, Document) and doc.filename == "emotion-journal-2026-10-08.txt"
    text = doc.content.decode()
    assert "MY EMOTION JOURNAL" in text and "2 entries" in text
    assert text.index("first") < text.index("second")
    assert "*" not in text  # plain text, no WhatsApp formatting


def test_view_during_entry_returns_to_current_question(journal):
    journal.handle(msg("new"), T0)
    replies = journal.handle(msg(choice="m:view"), T0)
    assert replies[-1].body.startswith("What food triggers the urge?")


def test_main_menu_accepts_typed_numbers(journal):
    journal.handle(msg("hi"), T0)
    [reply] = journal.handle(msg("1"), T0)
    assert reply.body.startswith("What food triggers the urge?")


def test_paginate():
    assert paginate(7) == [(0, 7)]
    assert paginate(17) == [(0, 9), (9, 17)]
    pages = paginate(30)
    assert pages[0] == (0, 9) and pages[-1][1] == 30
    assert all(end - start <= 8 for start, end in pages[1:-1])


def test_invalid_config_is_rejected(tmp_path):
    bad = tmp_path / "journal.yaml"
    bad.write_text(
        """
checkin_questions:
  - {id: a, text: A, choices: [Yes, No]}
  - {id: a, text: B, choices: ["This choice is far too long to fit"]}
wheel:
  Fear:
    Scared:
      similar: x
      words:
        Frightened: {similar: x, meaning: x, tip: x, reflect: x}
"""
    )
    with pytest.raises(ConfigError) as e:
        load_config(bad)
    assert "unique" in str(e.value) and "quotes" in str(e.value) and "longer than 24" in str(e.value)


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
                                    "interactive": {"type": "list_reply", "list_reply": {"id": "x#1", "title": "Fear"}},
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
    assert (m2.choice_id, m2.text) == ("x#1", "Fear")


def test_payload_shapes():
    p = to_payload("1", Buttons(body="b", buttons=[("x", "X")]))
    assert p["interactive"]["action"]["buttons"][0]["reply"] == {"id": "x", "title": "X"}
    p = to_payload("1", ListMenu(body="b", button="Go", rows=[("x", "X", "")]))
    assert p["interactive"]["action"]["sections"][0]["rows"] == [{"id": "x", "title": "X"}]
