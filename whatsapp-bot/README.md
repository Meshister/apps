# WhatsApp emotion journal

A private WhatsApp bot for journaling your feelings with the **Emotion & Feeling Wheel**
(Junto Institute). It runs on the official **WhatsApp Cloud API** from Meta.

## How an entry works

1. **Opening questions** (optional, set in `journal.yaml`)
2. **Inner circle → middle ring → outer ring** of the wheel. Each outer word shows similar words under it.
3. The bot **explains** the word, gives a **tip to try instead**, and a **question to reflect on**
4. **Check-in questions** you answer with a tap: strength 1–10, where in the body, where you are,
   who you're with, what it's connected to, what your body wants
5. **Free writing**
6. **Saved** with date and time

From the main menu: **📝 New entry**, **📖 My journal** (last 5 entries) and **📤 Export journal**
(the bot sends your whole journal as a `.txt` file in the chat).

Type `menu` anytime to cancel, `back` to go up a ring, `skip` to skip a question.
Only the phone numbers in `OWNER_NUMBERS` can use the bot; messages from anyone else are ignored.

## Changing what the bot says

Everything is in [`journal.yaml`](journal.yaml): the questions, the wheel words, the similar words,
explanations, tips and reflection questions. You can edit it directly on GitHub (click the file → ✏️).
The server checks the file when it starts and reports anything that WhatsApp wouldn't accept
(for example a choice longer than 24 characters).

## Setup

### 1. Meta
1. Create an app at <https://developers.facebook.com/apps> (use case: *Connect with customers through WhatsApp*).
2. On **WhatsApp → API Setup** note the **Phone number ID** and **access token**, and add your own
   phone number as a recipient.
3. **App settings → Basic** → copy the **App Secret**.

### 2. Configuration
Copy `.env.example` to `.env` (or enter the same values as environment variables on your host):

| Setting | What to put |
|---|---|
| `WHATSAPP_TOKEN` | access token |
| `WHATSAPP_PHONE_NUMBER_ID` | phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | any secret word you make up |
| `WHATSAPP_APP_SECRET` | app secret |
| `OWNER_NUMBERS` | your number with country code, digits only, e.g. `972501234567` |
| `TIMEZONE` | e.g. `Asia/Jerusalem`, `Europe/London` |

### 3. Run it
```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```
or with Docker: `docker build -t journal . && docker run -p 8000:8000 --env-file .env -v journal-data:/app/data journal`

Then in Meta: **WhatsApp → Configuration → Webhook**: callback URL `https://<your-server>/webhook`,
verify token = your `WHATSAPP_VERIFY_TOKEN`, and subscribe to the **messages** field.

### ⚠️ Keep your journal safe
Entries are stored in a SQLite file, `data/journal.db`. Your host must keep that folder between restarts
(a "persistent disk" or "volume"); many free hosts wipe files on every restart. Export your journal now
and then as a backup.

## For developers
```bash
python chat.py   # try the conversation in a terminal, no WhatsApp needed
pytest           # tests
```
- `app/journal.py`: conversation logic and `journal.yaml` loading/validation (no network)
- `app/store.py`: SQLite storage of entries and unfinished entries
- `app/whatsapp.py`: Cloud API client (messages, lists, buttons, document upload) and webhook parsing
- `app/main.py`: FastAPI webhook: verification, signature check, owner filter, duplicate protection
