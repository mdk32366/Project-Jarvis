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
fly.toml             three processes: api (HTTP) + ingest (email) + worker (jobs)
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

## Phase 1

SMS texting, a durable job queue, and an auto-memory reflector have landed; real-money trading is disabled by default (`ENABLE_TRADING=false`). See **PHASE1.md** for details and **backend/tests/** for the runnable pytest suite (`cd backend && pytest`).

## Local development (detailed)

JARVIS runs fully on your machine against a local Postgres — a safe sandbox
separate from the Fly database.

**Prereqs:** Docker, Python 3.11 (match the deploy runtime), Node 20.

```bash
# 1) Local Postgres
docker compose up -d

# 2) Backend
cd backend
python -m venv .venv
# Windows PowerShell:  py -3.11 -m venv .venv ; .\.venv\Scripts\Activate.ps1
source .venv/bin/activate
pip install -r requirements-dev.txt        # runtime deps + pytest
cp ../.env.template .env                    # then fill in keys (see below)
uvicorn app.main:app --reload               # API + built UI at http://localhost:8000

# 3) (optional) live-reloading UI in a second terminal
cd ui && npm install && npm run dev          # http://localhost:5173 (proxies /api)

# 4) (optional) background processes, each in its own terminal
python -m app.channels.email_pipeline --watch     # email intake
python -m app.workers.job_worker --watch          # job queue + morning briefing
```

Run the tests any time: `cd backend && pytest` (fully offline; stubs the LLM/SMS).

**What works locally, and what doesn't:**

- **Web chat, calendar, Alpaca** — work locally (outbound API calls). For calendar,
  put the base64 service-account key in `GOOGLE_SERVICE_ACCOUNT_JSON` and set
  `GOOGLE_CALENDAR_ID` to your email in `backend/.env`.
- **Email intake** — works if you set the Gmail creds (it polls IMAP outward).
- **SMS inbound** — Twilio can't reach `localhost`. To test end-to-end, expose the
  port with a tunnel (`ngrok http 8000`) and point the Twilio webhook at the ngrok
  URL. Web chat needs none of this.
- **Database** — local Postgres from `docker compose`, independent of production.
  `ENABLE_TRADING` stays `false` unless you set it.

Minimum `.env` to boot with web chat: `ANTHROPIC_API_KEY`, `JWT_SECRET`,
`DATABASE_URL` (the docker-compose default works), `SEED_PASSWORD`.
