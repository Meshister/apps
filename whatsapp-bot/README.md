# WhatsApp menu bot

A rule-based WhatsApp chatbot (menus, buttons, keyword answers, hand-off to a human) built on the
official **WhatsApp Cloud API** from Meta, with a small **FastAPI** webhook server.

Everything the bot says is in [`flows.yaml`](flows.yaml), so you can change the conversation without touching code.

```
User on WhatsApp ──► Meta ──POST /webhook──► this server ──► flows.yaml rules
                     ◄────── Graph API /messages ◄──────────── replies
```

## Features

- Main menu and sub-menus shown as WhatsApp **reply buttons** (≤3 options) or **list pickers** (≤10)
- Users can tap, type the option number (`2`) or type the option title
- Keyword answers anywhere in a message (`"what are your hours?"` → opening hours)
- `menu` / `hi` resets, `back` goes to the previous menu, `{name}` personalizes messages
- "Talk to a person" hand-off: the bot goes silent for that user until they type `menu` (or a timeout)
- Webhook signature check, duplicate-delivery protection, flow validation at startup
  (e.g. a button title that's too long fails on boot, not when a customer taps it)

## 1. Try it locally (no WhatsApp needed)

```bash
cd whatsapp-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python chat.py      # chat with the bot in your terminal
pytest              # run the tests
```

## 2. Set up WhatsApp on Meta

1. Go to <https://developers.facebook.com/apps>, **Create app** → use case *Other* → type **Business**.
2. Add the **WhatsApp** product. On **WhatsApp → API Setup** you get:
   - a free **test phone number** and its **Phone number ID**
   - a **temporary access token** (valid 24h)
   - a "To" field: add your own phone number as a test recipient (only verified numbers can chat with a test number)
3. **App settings → Basic** → copy the **App Secret**.
4. Create your config:
   ```bash
   cp .env.example .env    # then fill in the 4 values
   ```

## 3. Run the server and connect the webhook

```bash
uvicorn app.main:app --port 8000
```

Meta must reach your server over public HTTPS. While developing, use a tunnel:

```bash
ngrok http 8000          # or: cloudflared tunnel --url http://localhost:8000
```

In Meta: **WhatsApp → Configuration → Webhook → Edit**
- Callback URL: `https://<your-tunnel-domain>/webhook`
- Verify token: the `WHATSAPP_VERIFY_TOKEN` from your `.env`
- Click **Verify and save**, then under **Webhook fields** subscribe to **messages**.

Now send "hi" from your phone to the test number. 🎉

## 4. Customize the conversation

Edit `flows.yaml` (it's commented) and restart the server. The building blocks:

```yaml
menus:
  main:
    body: "Hi {name}! How can we help?"
    options:
      - id: hours                  # unique id
        title: "Opening hours"     # button/row text
        reply: "We're open 9–18"   # text to send...
      - id: faq
        title: "FAQ"
        goto: faq                  # ...and/or another menu to show
      - id: human
        title: "Talk to a person"
        reply: "Someone will reply soon"
        handoff: true              # bot goes quiet for HANDOFF_MINUTES

keywords:
  - words: [price, cost]
    goto: products
```

Run `pytest` or `python chat.py` after editing — invalid flows are reported with a clear list of what's wrong.

## 5. Going to production

- **Permanent token:** the 24h token expires. Create a *System User* in Meta Business Settings,
  give it the app and WhatsApp account, and generate a token with
  `whatsapp_business_messaging` and `whatsapp_business_management` permissions.
- **Real phone number:** add your business number in WhatsApp Manager and verify your business.
  A number registered on the Cloud API can't also be used in the regular WhatsApp app.
- **Hosting:** any host that runs a container works (Render, Railway, Fly.io, Cloud Run, a VPS):
  ```bash
  docker build -t whatsapp-bot . && docker run -p 8000:8000 --env-file .env whatsapp-bot
  ```
- **Run a single instance** (or one uvicorn worker): conversation state is kept in memory, so
  multiple workers would each have their own state, and a restart resets everyone to the main menu.
  To scale out, move `Bot.sessions` and `SeenMessages` to Redis.
- **Human hand-off:** the bot only stops replying; your team still needs a way to answer, for
  example an inbox tool connected to the same number, or extend `process_message` in
  `app/main.py` to forward the conversation to email/Slack.
- **24-hour rule:** WhatsApp only allows free-form replies within 24h of the user's last message.
  This bot only ever replies to incoming messages, so that's fine. Messaging users first
  requires pre-approved *template messages*.

## Project layout

```
app/
  main.py       FastAPI app: webhook verification, signature check, dedupe, dispatch
  bot.py        conversation engine + flows.yaml loading/validation (pure logic, no I/O)
  whatsapp.py   Cloud API client, webhook parsing, message payloads
  config.py     settings from environment / .env
flows.yaml      the conversation
chat.py         terminal simulator
tests/          pytest suite
```
