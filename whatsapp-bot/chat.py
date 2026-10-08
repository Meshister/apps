"""Try the bot in your terminal without WhatsApp: python chat.py

Type messages as a user would. To "tap" a button or list row, type its number or title,
or type !<option id> (e.g. !faq). Ctrl+C to quit.
"""

from datetime import datetime, timedelta, timezone

from app.bot import Bot, load_flows
from app.config import BASE_DIR
from app.whatsapp import Buttons, IncomingMessage, ListMenu, Text


def show(reply):
    if isinstance(reply, Text):
        print(f"bot: {reply.body}")
        return
    if reply.header:
        print(f"bot: [{reply.header}]")
    print(f"bot: {reply.body}")
    items = reply.buttons if isinstance(reply, Buttons) else [(r[0], f"{r[1]} - {r[2]}" if r[2] else r[1]) for r in reply.rows]
    if isinstance(reply, ListMenu):
        print(f"     ({reply.button})")
    for i, (oid, title) in enumerate(items, 1):
        print(f"     {i}. {title}   [!{oid}]")
    if reply.footer:
        print(f"     _{reply.footer}_")


def main():
    bot = Bot(load_flows(BASE_DIR / "flows.yaml"), timedelta(minutes=30), timedelta(minutes=60))
    print("Chatting with the bot. Ctrl+C to quit.\n")
    n = 0
    while True:
        try:
            text = input("you: ")
        except (KeyboardInterrupt, EOFError):
            print()
            return
        n += 1
        choice = text[1:] if text.startswith("!") else None
        msg = IncomingMessage(message_id=str(n), sender="local", name="Tester", text="" if choice else text, choice_id=choice)
        replies = bot.handle(msg, datetime.now(timezone.utc))
        if not replies:
            print("bot: (silent - handed off to a human; type 'menu' to resume)")
        for reply in replies:
            show(reply)
        print()


if __name__ == "__main__":
    main()
