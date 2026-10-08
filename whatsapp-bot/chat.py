"""Try the journal in your terminal without WhatsApp: python chat.py

Type what you would send on WhatsApp. To "tap" an option, type its number or its word.
Entries are saved in data/chat-test.db. Ctrl+C to quit.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import BASE_DIR
from app.journal import Journal, load_config
from app.store import Store
from app.whatsapp import Buttons, Document, IncomingMessage, ListMenu, Text


def show(reply):
    if isinstance(reply, Text):
        print(f"bot: {reply.body}")
        return
    if isinstance(reply, Document):
        path = BASE_DIR / "data" / reply.filename
        path.write_bytes(reply.content)
        print(f"bot: [file {reply.filename}] {reply.caption}\n     (saved to {path})")
        return
    print(f"bot: {reply.body}")
    items = [t for _, t in reply.buttons] if isinstance(reply, Buttons) else [
        f"{t}  ({d})" if d else t for _, t, d in reply.rows
    ]
    if isinstance(reply, ListMenu):
        print(f"     [{reply.button}]")
    for i, title in enumerate(items, 1):
        print(f"     {i}. {title}")


def main():
    journal = Journal(
        load_config(BASE_DIR / "journal.yaml"),
        Store(BASE_DIR / "data" / "chat-test.db"),
        ZoneInfo("UTC"),
        timedelta(hours=12),
    )
    print("Chatting with the journal bot. Ctrl+C to quit.\n")
    n = 0
    while True:
        try:
            text = input("you: ")
        except (KeyboardInterrupt, EOFError):
            print()
            return
        n += 1
        msg = IncomingMessage(message_id=str(n), sender="local", name="Me", text=text)
        for reply in journal.handle(msg, datetime.now(timezone.utc)):
            show(reply)
        print()


if __name__ == "__main__":
    main()
