## Vault Sync (Required)
The knowledge base for this project lives in the Obsidian vault at ~/my-second-brain/.
At the END of every session, before your final response, update:
  ~/my-second-brain/projects/wellpass-bot.md

Write or update these sections in that file:
- **Last session**: Date + 1-line summary of what was done
- **Status**: Current state of the project
- **Decisions**: Any architecture or implementation choices made this session
- **Next steps**: What to pick up next time

---

# wellpass-bot — Claude Code Project Reference

## Project Overview
Wellpass Gym Bot: monitors gym class availability on Wellpass and auto-books spots as soon as they open.
Telegram UI, Python backend on Railway, Supabase (PostgreSQL) database.
API reverse-engineered from the Wellpass iOS app — full reference in `docs/WELLPASS_API.md`.

## Tech Stack
- Python 3.11+, python-telegram-bot 21, httpx, supabase-py 2, APScheduler, cryptography (Fernet)

---

## Commands
```bash
# Install dependencies
pip install -r requirements.txt

# Generate Fernet encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Run locally
python main.py

# Deploy to Railway
railway up
```

---

## Source Architecture
```
wellpass-bot/
├── main.py              # Entry point — Application + post_init hooks
├── bot/
│   ├── handlers.py      # All Telegram command/callback/ConversationHandlers
│   ├── keyboards.py     # InlineKeyboardMarkup builders
│   └── messages.py      # Message text formatters
├── wellpass/
│   ├── auth.py          # Login, FLS token fetch, auto-refresh via get_valid_token()
│   ├── api.py           # All API calls (schedule, book, cancel, gym search)
│   └── models.py        # Pydantic models for API responses
├── db/
│   └── supabase.py      # All DB operations (sync supabase-py wrapped in asyncio.to_thread)
├── core/
│   ├── poller.py        # Polling loop every 15s — watches active bookings, auto-books
│   ├── scheduler.py     # APScheduler weekly schedule sender (Sun 18:00 Europe/Berlin)
│   └── crypto.py        # Fernet encrypt/decrypt for stored passwords
├── docs/
│   └── WELLPASS_API.md  # Full API reverse-engineering reference
├── .env.example
├── requirements.txt
├── Procfile
└── railway.toml
```

---

## Environment Variables
```
TELEGRAM_BOT_TOKEN=
SUPABASE_URL=
SUPABASE_SERVICE_KEY= # Supabase Settings → API → service_role key
ENCRYPTION_KEY=   # Fernet key — generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ADMIN_TELEGRAM_ID=
```

---

## DB Schema
Run in Supabase SQL editor — see full schema at top of `db/supabase.py`.
Tables: `users`, `user_studios`, `watches`, `bookings`, `class_filters`

---

## Current Status

### ✅ Complete
- Full auth flow (Netpulse login → FLS bearer token, 3h session + 60min token auto-refresh)
- Poller: 15s loop checks active watches, auto-books when spot opens
- Cancel window warning: notifies user 1h before 12h late-cancel fee window
- Telegram bot: /start setup, /studios, /schedule, /watching, /bookings, /filter, /stop, /help
- Fernet-encrypted password storage
- Weekly schedule push every Sunday 18:00 Europe/Berlin (APScheduler)
- Multi-studio support per user
- Class filters (only show certain class types in schedule)
- Daily booking limit check (1 booking per studio per day)

### ⚠️ Needs Work / Known Gaps
- No .env file yet — needs real credentials before running
- Supabase schema must be applied manually before first run
- No rate limiting / backoff on API errors
- `get_bookings_cancel_warning_due()` uses a joined select — verify Supabase RLS permits it

---

## Coding Conventions
- All API calls use httpx async client
- DB operations are sync supabase-py wrapped with asyncio.to_thread
- No plaintext passwords — always encrypt with core.crypto before storing
- Token refresh happens automatically in wellpass.auth.get_valid_token()
- All datetimes are UTC internally; display in Europe/Berlin for users
