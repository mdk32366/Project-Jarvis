# JARVIS — Phase 0

A personal majordomo you can **email**. Built on the standard Vite + FastAPI +
Postgres template. Phase 0 delivers the core loop end-to-end:

> dedicated Gmail (IMAP poll) → whitelist check → orchestrator (Claude + tools,
> with your seeded persona/preferences) → in-thread SMTP reply

…plus a React dashboard (chat, memory, status), an enforced confirmation gate
for financial actions, and an audit trail.

## Architecture (this phase)

```
backend/app/
  main.py            FastAPI: serves API + React build, lifespan bootstrap
  config.py          settings (LLM, Gmail, Alpaca, whitelist, threshold)
  models.py          users, conversations, messages, persona_profile,
                     preferences, memories, contacts_whitelist,
                     actions_audit, pending_confirmations
  memory.py          builds the persona+preferences system preamble ("think like me")
  seed_memory.py     hand-seed your persona/style + standing rules
  llm.py             Anthropic client wrapper
  orchestrator.py    the core loop: route → tools → confirmation gate → reply
  handlers/          finance (Alpaca), general (remember_fact)
  channels/
    email_pipeline.py  IMAP poll + threaded SMTP reply  (Fly `ingest` process)
  notifier.py        outbound Gmail SMTP
  routes.py          /api/chat, /api/memory*, /api/conversations, /api/health
ui/src/pages/        ChatPage, MemoryPage, DashboardPage (status)
fly.toml             two processes: api (HTTP) + ingest (email watcher)
```

The intent "router" is the orchestrator handing Claude the full tool set plus
your persona/preferences and letting it choose tools (or answer directly).
Phase 1 adds explicit multi-agent delegation, a job queue, SMS/voice, and the
pgvector reflector that auto-learns facts.

## Prerequisites: the dedicated Gmail account

JARVIS reads and replies from its **own** Gmail account (not your personal one):

1. Create the account and enable **2-Step Verification** (required for app passwords).
2. Create an **App Password** (Google Account → Security → App passwords).
3. Put the account + 16-char app password in `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD`.
4. Add **your** real address(es) to `ALLOWED_SENDERS` so only you can command it.

## Local development

```bash
docker compose up -d                      # Postgres

cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.template .env                   # fill in ANTHROPIC_API_KEY, Gmail, etc.
uvicorn app.main:app --reload             # seeds the admin user + tables
python -m app.seed_memory                  # seed your persona + preferences (edit it first!)

# Frontend
cd ../ui && npm install && npm run dev     # http://localhost:5173

# Email loop (separate terminal, from backend/)
python -m app.channels.email_pipeline --once     # one pass
python -m app.channels.email_pipeline --watch    # continuous
```

Then email the JARVIS account from a whitelisted address — e.g. *"What's AAPL
trading at?"* or *"Buy 1 share of AAPL"* (you'll get a confirmation request
first). You can also use the **Chat** tab in the dashboard, which runs the
identical orchestrator.

## Deploy to Fly.io

```bash