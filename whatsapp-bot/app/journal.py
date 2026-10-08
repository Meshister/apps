"""Emotion journal conversation.

One entry goes:  opening questions -> wheel (inner -> middle -> outer) -> explanation, tip and
reflection question -> check-in questions -> free writing -> saved.
Everything the bot says comes from journal.yaml. This module is pure logic (no network), so it is easy to test.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .store import Store
from .whatsapp import Buttons, Document, IncomingMessage, ListMenu, Reply, Text

# WhatsApp interactive message limits
MAX_BUTTONS, MAX_BUTTON_TITLE = 3, 20
MAX_ROWS, MAX_ROW_TITLE, MAX_ROW_DESC = 10, 24, 72

MENU_WORDS = {"menu", "cancel", "hi", "hello", "start"}
# Typed alternatives to the main-menu buttons (number = position of the button)
NEW_WORDS = {"1", "new", "new entry", "journal entry"}
VIEW_WORDS = {"2", "journal", "my journal", "view"}
EXPORT_WORDS = {"3", "export", "export journal"}
BACK, SKIP, SKIPPED = "back", "skip", "(skipped)"
RECENT_ENTRIES = 5
FOOTER = "menu = cancel · skip = skip"
OTHER = "✏️ Other (type it)"
MAX_CHOICES = 30


class ConfigError(ValueError):
    pass


@dataclass
class Question:
    id: str
    text: str
    choices: list[str]  # empty = free text
    other: bool = False  # adds an "Other" choice that lets you type the answer


@dataclass
class Word:
    title: str
    similar: str
    meaning: str
    tip: str
    reflect: str


@dataclass
class MiddleWord:
    title: str
    similar: str
    words: dict[str, Word]  # the outer-ring words


@dataclass
class JournalConfig:
    opening: list[Question]
    checkin: list[Question]
    wheel: dict[str, dict[str, MiddleWord]]  # inner -> middle -> MiddleWord
    free_writing: str
    wheel_question: str = "How are you feeling right now?"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def load_config(path: Path) -> JournalConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    def questions(key):
        return [
            Question(
                id=str(q["id"]),
                text=q["text"],
                choices=[str(c) for c in q.get("choices") or []],
                other=bool(q.get("other")),
            )
            for q in data.get(key) or []
        ]

    wheel = {
        str(inner): {
            str(middle): MiddleWord(
                title=str(middle),
                similar=str(m.get("similar", "")),
                words={str(outer): Word(title=str(outer), **info) for outer, info in (m.get("words") or {}).items()},
            )
            for middle, m in middles.items()
        }
        for inner, middles in (data.get("wheel") or {}).items()
    }
    cfg = JournalConfig(
        opening=questions("opening_questions"),
        checkin=questions("checkin_questions"),
        wheel=wheel,
        free_writing=data.get("free_writing", "✍️ Write whatever is on your mind."),
        **({"wheel_question": data["wheel_question"]} if data.get("wheel_question") else {}),
    )
    validate(cfg)
    return cfg


def validate(cfg: JournalConfig) -> None:
    """Fail at startup instead of having WhatsApp reject a message later."""
    errors = []
    ids = [q.id for q in cfg.opening + cfg.checkin]
    if len(ids) != len(set(ids)):
        errors.append("question ids must be unique")
    for q in cfg.opening + cfg.checkin:
        if len(q.choices) + q.other > MAX_CHOICES:
            errors.append(f"question '{q.id}': at most {MAX_CHOICES} choices")
        if any(c in ("True", "False") for c in q.choices):
            errors.append(f"question '{q.id}': put Yes / No choices in quotes")
        for c in q.choices:
            if len(c) > MAX_ROW_TITLE:
                errors.append(f"question '{q.id}': choice '{c}' longer than {MAX_ROW_TITLE} characters")
    if not cfg.wheel:
        errors.append("wheel is empty")
    if len(cfg.wheel) > MAX_ROWS:
        errors.append(f"wheel: at most {MAX_ROWS} inner emotions")
    for inner, middles in cfg.wheel.items():
        if len(middles) + 1 > MAX_ROWS:  # +1 for the Back row
            errors.append(f"wheel '{inner}': at most {MAX_ROWS - 1} middle-ring words")
        for middle, m in middles.items():
            if not m.words:
                errors.append(f"wheel '{inner}/{middle}': has no outer-ring words")
            if len(m.words) + 1 > MAX_ROWS:
                errors.append(f"wheel '{inner}/{middle}': at most {MAX_ROWS - 1} outer-ring words")
            if len(m.similar) > MAX_ROW_DESC:
                errors.append(f"'{middle}': similar words longer than {MAX_ROW_DESC} characters")
            for w in m.words.values():
                for title in (inner, middle, w.title):
                    if len(title) > MAX_ROW_TITLE:
                        errors.append(f"wheel word '{title}' longer than {MAX_ROW_TITLE} characters")
                if len(w.similar) > MAX_ROW_DESC:
                    errors.append(f"'{w.title}': similar words longer than {MAX_ROW_DESC} characters")
    if errors:
        raise ConfigError("Invalid journal.yaml:\n  - " + "\n  - ".join(dict.fromkeys(errors)))


def main_menu(body: str = "📓 *Emotion journal*\nWhat would you like to do?") -> Buttons:
    return Buttons(
        body=body,
        buttons=[("m:new", "📝 New entry"), ("m:view", "📖 My journal"), ("m:export", "📤 Export journal")],
    )


class Journal:
    def __init__(self, cfg: JournalConfig, store: Store, tz: ZoneInfo, draft_ttl: timedelta):
        self.cfg = cfg
        self.store = store
        self.tz = tz
        self.draft_ttl = draft_ttl

    # ---- entry point -------------------------------------------------------------------------

    def handle(self, msg: IncomingMessage, now: datetime) -> list[Reply]:
        phone = msg.sender
        text = normalize(msg.text)
        state = self.store.get_state(phone)
        if state and now - datetime.fromisoformat(state["updated"]) > self.draft_ttl:
            self.store.clear_state(phone)
            state = None

        # Main-menu buttons work at any time (also from older messages)
        if msg.choice_id == "m:new":
            return self._start(phone, now)
        if msg.choice_id in ("m:view", "m:export"):
            replies = self._recent(phone) if msg.choice_id == "m:view" else self._export(phone, now)
            return replies + ([self._ask(state)] if state else [main_menu("What next?")])

        if text in MENU_WORDS:
            self.store.clear_state(phone)
            note = "🗑️ The unfinished entry was discarded.\n\n" if state else ""
            return [main_menu(note + "📓 *Emotion journal*\nWhat would you like to do?")]

        if not state:
            if text in NEW_WORDS:
                return self._start(phone, now)
            if text in VIEW_WORDS:
                return self._recent(phone) + [main_menu("What next?")]
            if text in EXPORT_WORDS:
                return self._export(phone, now) + [main_menu("What next?")]
            return [main_menu()]

        replies = self._answer(state, msg, text, now)
        if state.get("done"):
            self.store.clear_state(phone)
        else:
            state["updated"] = now.isoformat()
            self.store.set_state(phone, state)
        return replies

    # ---- steps -------------------------------------------------------------------------------

    def _start(self, phone: str, now: datetime) -> list[Reply]:
        state = {
            "phone": phone,
            "step": "opening" if self.cfg.opening else "inner",
            "i": 0,
            "path": [],
            "draft": {"opening": [], "checkin": []},
            "updated": now.isoformat(),
        }
        self.store.set_state(phone, state)
        return [self._ask(state)]

    def _key(self, state: dict) -> str:
        """Identifies the question on screen, so taps on old messages aren't mistaken for answers."""
        step = state["step"]
        if step in ("opening", "checkin"):
            return f"{step}:{self._question(state).id}"
        return f"{step}:{'/'.join(state['path'])}"

    def _question(self, state: dict) -> Question:
        return (self.cfg.opening if state["step"] == "opening" else self.cfg.checkin)[state["i"]]

    def _options(self, state: dict) -> list[tuple[str, str]]:
        """(title, description) choices for the current step; empty for free-text steps."""
        step, path = state["step"], state["path"]
        if state.get("typing"):  # chose "Other": waiting for a typed answer
            return []
        if step == "inner":
            return [(name, "") for name in self.cfg.wheel]
        if step == "middle":
            return [(m.title, m.similar) for m in self.cfg.wheel[path[0]].values()]
        if step == "outer":
            return [(w.title, w.similar) for w in self.cfg.wheel[path[0]][path[1]].words.values()]
        if step in ("opening", "checkin"):
            q = self._question(state)
            return [(c, "") for c in q.choices] + ([(OTHER, "")] if q.other and q.choices else [])
        return []

    def _ask(self, state: dict) -> Reply:
        step, key = state["step"], self._key(state)
        rows = [(f"{key}#{i}", title, desc) for i, (title, desc) in enumerate(self._options(state))]
        if step == "inner":
            return ListMenu(body=self.cfg.wheel_question, button="Feelings", rows=rows, footer=FOOTER)
        if step in ("middle", "outer"):
            body = f"*{' → '.join(state['path'])}*\nWhich word fits best?"
            return ListMenu(
                body=body, button="Choose", rows=rows + [(f"{key}#back", "⬅️ Back", "")], footer=FOOTER
            )
        if step == "free":
            return Text(self.cfg.free_writing)
        q = self._question(state)
        if state.get("typing"):
            return Text(f"{q.text}\n\n✏️ Type your answer:")
        if not rows:
            return Text(f"{q.text}\n\n_(type your answer, or *skip*)_")
        if len(rows) <= MAX_BUTTONS and all(len(t) <= MAX_BUTTON_TITLE for _, t, _ in rows):
            return Buttons(body=q.text, buttons=[(rid, t) for rid, t, _ in rows], footer=FOOTER)
        pages = paginate(len(rows))
        page = min(state.get("page", 0), len(pages) - 1)
        start, end = pages[page]
        shown = rows[start:end]
        if page > 0:
            shown = [(f"{key}#prev", "⬅️ Previous", "")] + shown
        if page < len(pages) - 1:
            shown = shown + [(f"{key}#more", "More ➡️", f"{len(rows) - end} more choices")]
        body = q.text + (f"\n_(page {page + 1} of {len(pages)})_" if len(pages) > 1 else "")
        return ListMenu(body=body, button="Choose", rows=shown, footer=FOOTER)

    def _answer(self, state: dict, msg: IncomingMessage, text: str, now: datetime) -> list[Reply]:
        step = state["step"]
        options = self._options(state)

        if not options:  # free text
            if msg.kind != "text" or not msg.text.strip():
                return [Text("I can only save typed text for now. Please write your answer."), self._ask(state)]
            state.pop("typing", None)
            return self._record(state, SKIPPED if text == SKIP else msg.text.strip(), now)

        # Work out which option was picked: a tap, a number or the word itself
        key = self._key(state)
        picked: int | str | None = None
        if msg.choice_id:
            prefix, _, idx = msg.choice_id.rpartition("#")
            if prefix == key:
                picked = idx if idx in ("back", "more", "prev") else int(idx)
        elif text == BACK and step in ("middle", "outer"):
            picked = BACK
        elif text == SKIP and step in ("opening", "checkin"):
            picked = SKIP
        elif text in ("more", "next") and step in ("opening", "checkin"):
            picked = "more"
        elif text.isdigit() and 1 <= int(text) <= len(options):
            picked = int(text) - 1
        else:
            picked = next((i for i, (title, _) in enumerate(options) if normalize(title) == text), None)

        if picked is None:
            return [Text("Please choose one of the options 👇"), self._ask(state)]
        if picked == BACK:
            state["path"].pop()
            state["step"] = "inner" if step == "middle" else "middle"
            return [self._ask(state)]
        if picked in ("more", "prev"):
            state["page"] = max(0, state.get("page", 0) + (1 if picked == "more" else -1))
            return [self._ask(state)]
        if picked != SKIP and options[picked][0] == OTHER:
            state["typing"] = True
            return [self._ask(state)]
        answer = SKIPPED if picked == SKIP else options[picked][0]
        return self._record(state, answer, now)

    def _record(self, state: dict, answer: str, now: datetime) -> list[Reply]:
        """Store the answer for the current step and move to the next one."""
        step, draft = state["step"], state["draft"]
        replies: list[Reply] = []

        state.pop("page", None)
        if step in ("opening", "checkin"):
            draft[step].append({"q": self._question(state).text, "a": answer})
            questions = self.cfg.opening if step == "opening" else self.cfg.checkin
            if state["i"] + 1 < len(questions):
                state["i"] += 1
            elif step == "opening":
                state.update(step="inner", i=0)
            else:
                state.update(step="free", i=0)
        elif step == "inner":
            state["path"] = [answer]
            state["step"] = "middle"
        elif step == "middle":
            state["path"].append(answer)
            state["step"] = "outer"
        elif step == "outer":
            inner, middle = state["path"]
            word = self.cfg.wheel[inner][middle].words[answer]
            draft["feeling"] = [inner, middle, answer]
            draft["reflect"] = word.reflect
            replies.append(Text(self._explain(word)))
            state.update(step="checkin" if self.cfg.checkin else "free", i=0)
        elif step == "free":
            draft["writing"] = answer
            created = now.astimezone(self.tz)
            self.store.add_entry(state["phone"], created.isoformat(), draft)
            state["done"] = True
            summary = format_entry({"created_at": created.isoformat(), **draft}, rich=True)
            return [Text("✅ *Saved to your journal*\n\n" + summary), main_menu("What next?")]

        return replies + [self._ask(state)]

    @staticmethod
    def _explain(w: Word) -> str:
        return (
            f"*{w.title}*\n_{w.similar}_\n\n"
            f"💬 {w.meaning}\n\n"
            f"💡 *Try instead:* {w.tip}\n\n"
            f"🤔 *Reflect:* {w.reflect}"
        )

    # ---- reading the journal -----------------------------------------------------------------

    def _recent(self, phone: str) -> list[Reply]:
        entries = self.store.entries(phone, last=RECENT_ENTRIES)
        if not entries:
            return [Text("Your journal is empty. Tap *📝 New entry* to write the first one.")]
        texts = [f"📖 *Your last {len(entries)} entries*"] + [format_entry(e, rich=True) for e in entries]
        return [Text(t) for t in pack(texts)]

    def _export(self, phone: str, now: datetime) -> list[Reply]:
        entries = self.store.entries(phone)
        if not entries:
            return [Text("Your journal is empty, so there's nothing to export yet.")]
        today = now.astimezone(self.tz)
        count = f"{len(entries)} {'entry' if len(entries) == 1 else 'entries'}"
        header = f"MY EMOTION JOURNAL\nExported {today:%d %B %Y}  ·  {count}"
        body = "\n\n".join([header] + [format_entry(e, rich=False) for e in entries]) + "\n"
        return [
            Document(
                filename=f"emotion-journal-{today:%Y-%m-%d}.txt",
                content=body.encode("utf-8"),
                caption=f"📤 Your full journal ({count})",
            )
        ]



def paginate(n: int) -> list[tuple[int, int]]:
    """Split n list rows into pages that fit WhatsApp's 10-row limit, leaving room for More / Previous rows."""
    if n <= MAX_ROWS:
        return [(0, n)]
    pages, start = [], 0
    while start < n:
        remaining = n - start
        if start == 0:
            size = MAX_ROWS - 1  # + More
        elif remaining <= MAX_ROWS - 1:
            size = remaining  # + Previous
        else:
            size = MAX_ROWS - 2  # + Previous + More
        pages.append((start, start + size))
        start += size
    return pages


def format_entry(e: dict, rich: bool) -> str:
    b = (lambda s: f"*{s}*") if rich else (lambda s: s)
    when = datetime.fromisoformat(e["created_at"])
    lines = [("📅 " if rich else "") + b(f"{when:%a %d %b %Y, %H:%M}")]
    if not rich:
        lines.insert(0, "-" * 40)
    for qa in e.get("opening", []):
        lines.append(f"• {qa['q']} {qa['a']}")
    if e.get("feeling"):
        lines.append(f"{b('Feeling:')} {' → '.join(e['feeling'])}")
    for qa in e.get("checkin", []):
        lines.append(f"• {qa['q']} {qa['a']}")
    if e.get("reflect"):
        lines.append(f"{b('Reflect:')} {e['reflect']}")
    if e.get("writing"):
        lines.append(f"✍️ {e['writing']}")
    return "\n".join(lines)


def pack(texts: list[str], limit: int = 4000) -> list[str]:
    """Join short texts into as few WhatsApp messages as possible (max 4096 characters each)."""
    out: list[str] = []
    for t in texts:
        t = t[:limit]
        if out and len(out[-1]) + len(t) + 2 <= limit:
            out[-1] += "\n\n" + t
        else:
            out.append(t)
    return out
