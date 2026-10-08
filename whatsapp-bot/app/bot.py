"""Menu / keyword chatbot engine.

The conversation is described in flows.yaml (see the comments there). This module is pure logic:
it takes an incoming message and returns the replies to send, so it can be tested without WhatsApp.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .whatsapp import Buttons, IncomingMessage, ListMenu, Reply, Text

# WhatsApp interactive message limits
MAX_BUTTONS, MAX_BUTTON_TITLE = 3, 20
MAX_ROWS, MAX_ROW_TITLE, MAX_ROW_DESC = 10, 24, 72
MAX_BODY, MAX_HEADER, MAX_FOOTER = 1024, 60, 60


class FlowError(ValueError):
    pass


@dataclass
class Option:
    id: str
    title: str
    description: str = ""
    reply: str = ""
    goto: str | None = None
    handoff: bool = False


@dataclass
class Menu:
    name: str
    body: str
    options: list[Option]
    header: str | None = None
    footer: str | None = None
    button: str = "Choose"
    style: str = "auto"  # auto | buttons | list


@dataclass
class Keyword:
    words: list[str]
    reply: str = ""
    goto: str | None = None


@dataclass
class Flows:
    start_menu: str
    menus: dict[str, Menu]
    keywords: list[Keyword] = field(default_factory=list)
    fallback: str = "Sorry, I didn't understand that. Type *menu* to see the options."
    handoff_ended: str = "You're back with the bot."
    reset_words: list[str] = field(default_factory=lambda: ["menu", "start", "hi", "hello", "0"])
    back_words: list[str] = field(default_factory=lambda: ["back"])


@dataclass
class Session:
    stack: list[str]  # menu names, current menu last
    last_seen: datetime
    paused_until: datetime | None = None


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def load_flows(path: Path) -> Flows:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    menus = {}
    for name, m in (data.get("menus") or {}).items():
        options = [Option(**{**o, "id": str(o["id"])}) for o in m.get("options", [])]
        menus[name] = Menu(name=name, options=options, **{k: v for k, v in m.items() if k != "options"})
    flows = Flows(
        start_menu=data.get("start_menu", "main"),
        menus=menus,
        keywords=[Keyword(**k) for k in data.get("keywords", [])],
        **{k: data[k] for k in ("fallback", "handoff_ended", "reset_words", "back_words") if k in data},
    )
    flows.reset_words = [normalize(str(w)) for w in flows.reset_words]
    flows.back_words = [normalize(str(w)) for w in flows.back_words]
    for kw in flows.keywords:
        kw.words = [normalize(str(w)) for w in kw.words]
    validate(flows)
    return flows


def menu_style(menu: Menu) -> str:
    if menu.style != "auto":
        return menu.style
    fits_buttons = len(menu.options) <= MAX_BUTTONS and all(len(o.title) <= MAX_BUTTON_TITLE for o in menu.options)
    return "buttons" if fits_buttons else "list"


def validate(flows: Flows) -> None:
    """Fail fast at startup instead of having WhatsApp reject a message at runtime."""
    errors = []
    if flows.start_menu not in flows.menus:
        errors.append(f"start_menu '{flows.start_menu}' is not defined under menus")
    seen_ids: dict[str, str] = {}
    for menu in flows.menus.values():
        where = f"menu '{menu.name}'"
        style = menu_style(menu)
        if style not in ("buttons", "list"):
            errors.append(f"{where}: style must be auto, buttons or list")
        if not menu.options:
            errors.append(f"{where}: needs at least one option")
        if len(menu.body) > MAX_BODY:
            errors.append(f"{where}: body longer than {MAX_BODY} chars")
        if menu.header and len(menu.header) > MAX_HEADER:
            errors.append(f"{where}: header longer than {MAX_HEADER} chars")
        if menu.footer and len(menu.footer) > MAX_FOOTER:
            errors.append(f"{where}: footer longer than {MAX_FOOTER} chars")
        if len(menu.button) > MAX_BUTTON_TITLE:
            errors.append(f"{where}: button label longer than {MAX_BUTTON_TITLE} chars")
        max_opts, max_title = (MAX_BUTTONS, MAX_BUTTON_TITLE) if style == "buttons" else (MAX_ROWS, MAX_ROW_TITLE)
        if len(menu.options) > max_opts:
            errors.append(f"{where}: {style} style allows at most {max_opts} options")
        for opt in menu.options:
            if opt.id in seen_ids:
                errors.append(f"{where}: option id '{opt.id}' already used in menu '{seen_ids[opt.id]}'")
            seen_ids[opt.id] = menu.name
            if len(opt.title) > max_title:
                errors.append(f"{where}: option '{opt.id}' title longer than {max_title} chars")
            if len(opt.description) > MAX_ROW_DESC:
                errors.append(f"{where}: option '{opt.id}' description longer than {MAX_ROW_DESC} chars")
            if opt.goto and opt.goto not in flows.menus:
                errors.append(f"{where}: option '{opt.id}' goes to unknown menu '{opt.goto}'")
            if not (opt.reply or opt.goto):
                errors.append(f"{where}: option '{opt.id}' needs a reply and/or goto")
    for kw in flows.keywords:
        if kw.goto and kw.goto not in flows.menus:
            errors.append(f"keyword {kw.words}: goes to unknown menu '{kw.goto}'")
        if not (kw.reply or kw.goto):
            errors.append(f"keyword {kw.words}: needs a reply and/or goto")
    if errors:
        raise FlowError("Invalid flows file:\n  - " + "\n  - ".join(errors))


class Bot:
    def __init__(self, flows: Flows, session_ttl: timedelta, handoff_duration: timedelta):
        self.flows = flows
        self.session_ttl = session_ttl
        self.handoff_duration = handoff_duration
        self.sessions: dict[str, Session] = {}  # in memory: swap for Redis/DB if you run several instances
        self._options = {o.id: o for m in flows.menus.values() for o in m.options}

    def handle(self, msg: IncomingMessage, now: datetime) -> list[Reply]:
        self._expire(now)
        session = self.sessions.get(msg.sender)
        is_new = session is None
        if session is None:
            session = self.sessions[msg.sender] = Session(stack=[self.flows.start_menu], last_seen=now)
        session.last_seen = now
        text = normalize(msg.text)

        # Hand-off to a human: stay quiet unless the user asks for the menu again
        if session.paused_until:
            if now < session.paused_until and text not in self.flows.reset_words:
                return []
            session.paused_until = None
            if text in self.flows.reset_words:
                return [Text(self.flows.handoff_ended), *self._show(session, self.flows.start_menu, reset=True, msg=msg)]

        # 1. Tapped a button / list row (also works for buttons on older messages)
        if msg.choice_id and msg.choice_id in self._options:
            return self._choose(session, self._options[msg.choice_id], msg, now)

        # 2. Navigation words
        if text in self.flows.reset_words:
            return self._show(session, self.flows.start_menu, reset=True, msg=msg)
        if text in self.flows.back_words:
            if len(session.stack) > 1:
                session.stack.pop()
            return self._show(session, session.stack[-1], msg=msg, push=False)

        # 3. Typed a number or an option title of the current menu
        current = self.flows.menus[session.stack[-1]]
        if not is_new:
            if text.isdigit() and 1 <= int(text) <= len(current.options):
                return self._choose(session, current.options[int(text) - 1], msg, now)
            for opt in current.options:
                if text == normalize(opt.title):
                    return self._choose(session, opt, msg, now)

        # 4. Keywords anywhere in the message
        for kw in self.flows.keywords:
            if any(re.search(rf"\b{re.escape(w)}\b", text) for w in kw.words):
                replies: list[Reply] = [Text(self._fill(kw.reply, msg))] if kw.reply else []
                if kw.goto:
                    replies += self._show(session, kw.goto, msg=msg)
                return replies

        # 5. First contact: greet with the main menu. Otherwise: fallback.
        if is_new:
            return self._show(session, self.flows.start_menu, reset=True, msg=msg)
        return [Text(self._fill(self.flows.fallback, msg))]

    def _choose(self, session: Session, opt: Option, msg: IncomingMessage, now: datetime) -> list[Reply]:
        replies: list[Reply] = [Text(self._fill(opt.reply, msg))] if opt.reply else []
        if opt.handoff:
            session.paused_until = now + self.handoff_duration
        if opt.goto:
            replies += self._show(session, opt.goto, msg=msg)
        return replies

    def _show(self, session: Session, menu_name: str, msg: IncomingMessage, reset=False, push=True) -> list[Reply]:
        if reset:
            session.stack = [menu_name]
        elif push and session.stack[-1] != menu_name:
            session.stack.append(menu_name)
        return [self.render(self.flows.menus[menu_name], msg)]

    def render(self, menu: Menu, msg: IncomingMessage) -> Reply:
        body = self._fill(menu.body, msg)
        if menu_style(menu) == "buttons":
            return Buttons(
                body=body,
                buttons=[(o.id, o.title) for o in menu.options],
                header=menu.header,
                footer=menu.footer,
            )
        return ListMenu(
            body=body,
            button=menu.button,
            rows=[(o.id, o.title, o.description) for o in menu.options],
            header=menu.header,
            footer=menu.footer,
        )

    @staticmethod
    def _fill(template: str, msg: IncomingMessage) -> str:
        return template.replace("{name}", msg.name or "there").strip()

    def _expire(self, now: datetime) -> None:
        stale = [
            k
            for k, s in self.sessions.items()
            if now - s.last_seen > self.session_ttl and not (s.paused_until and now < s.paused_until)
        ]
        for k in stale:
            del self.sessions[k]
