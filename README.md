# JARVIS

A personal AI majordomo — email, SMS, voice, and web chat. Built on FastAPI +
Postgres + Claude. Runs on Fly.io; fully testable offline with stubs.

> email / SMS / phone call / web chat → whitelist check → orchestrator (Claude +
> tools + persona/preferences + memory) → delegated sub-agents → reply in kind

---

## What it can do today

**Channels**
- **Email** — IMAP poll on a dedicated Gmail account; threaded SMTP replies
- **SMS** — Twilio inbound webhook; read/write in the same thread
- **Voice** — Twilio inbound calls; TwiML gather → background orchestration → poll
  reply. Outbound callbacks when a request outlasts the call.
- **Web chat** — authenticated React SPA at `/login`

**Core loop**
- Persona + standing preferences injected into every system prompt
- Conversation history (per channel × thread), auto-summarized for long threads
- pgvector reflector auto-learns durable facts from conversations out-of-band
- Multi-agent delegation: orchestrator delegates to specialist sub-agents
  (finance, archivist, researcher, scheduling, secretary, travel, navigator,
  netstatus, infra) and synthesizes results
- Durable job queue (APScheduler-backed) for async work and the morning brief

**Capabilities (handlers)**
| Domain | Tools |
|---|---|
| Finance | stock quotes, portfolio (Alpaca read-only); `place_stock_order` gated + confirmable |
| Calendar | read events, create events (gated) — Google Calendar via service account |
| Tasks & ideas | add / list / complete / cancel tasks; capture / list ideas |
| Email | draft (secretary agent) + send (orchestrator only, gated) |
| Contacts | lookup / save / list; sync from Google Contacts |
| Travel | list trips, search flights (Duffel); `book_flight` gated + TOTP second factor |
| Google Docs/Sheets | create / append to documents; provenance-tagged |
| Navigation | traffic (Google Maps), find place, current location from phone (server-initiated pull → AutoRemote → Tasker) |
| Research | web search + page fetch (Tavily / httpx) |
| Tailscale | tailnet status |
| Infrastructure | Fly fleet health + spend |
| Network status | node status, service health |
| Memory | remember / recall / forget / audit facts |
| Watches | set condition-triggered callbacks |
| Callbacks | schedule outbound calls; cancel |
| Datetime | current time in any timezone; relative-date resolution; stale-content flagging |

**Morning brief** (job queue, daily)
Composes a spoken/emailed briefing from: calendar, tasks, trips, portfolio,
hosted-app health, fleet spend, memory facts — plus weather (NWS), marine
forecast + advisories (NWS, PZZ133/PZZ132), traffic (Google Maps), and top news
(Tavily). All nine network sources fetched in parallel; any single failure is
silently omitted.

**Confirmation gate**
High-stakes actions (`send_email`, `create_event`, `place_stock_order`,
`book_flight`) require an explicit confirmation reply before execution. Voice
uses a narrowed vocabulary (no "ok" / "yeah" as filler triggers). `book_flight`
additionally requires a TOTP code from an enrolled authenticator app — a
spoofed caller ID cannot produce it.

---

## Architecture

```
backend/app/
  main.py               FastAPI: serves API + React build, lifespan bootstrap
  config.py             settings (all channels, thresholds, feature flags)
  models.py             all ORM models
  memory.py             persona+preferences preamble; pgvector fact store
  orchestrator.py       core loop: route → tools → confirmation gate → reply
  agents.py             sub-agent registry and delegate tool
  jobs.py               durable job queue (enqueue / process)
  reflector.py          out-of-band memory learner (reflect job)
  briefing.py           morning brief composition (parallel, graceful degradation)
  llm.py                Anthropic client wrapper
  notifier.py           outbound email (SMTP)
  totp.py               TOTP second factor for book_flight
  seed_memory.py        hand-seed your persona + preferences (edit first)

  handlers/             one file per domain — finance, scheduling, travel,
                        maps, secretary, tasks, ideas, contacts, googledocs,
                        datetime_tools, websearch, location, callback,
                        tailscale, watches, infra, netstatus, general, audit

  channels/
    email_pipeline.py   IMAP poll + SMTP reply  (Fly `ingest` process)
    sms_pipeline.py     Twilio SMS webhook
    voice_pipeline.py   Twilio voice (inbound) + poll + transcript email
    outbound_voice.py   Fly Machine callback calls

fly.toml                three Fly processes: api + ingest + worker
```

**Fly processes**

| Process | Runs |
|---|---|
| `api` | FastAPI (HTTP, webhooks, web chat) |
| `ingest` | email_pipeline — polls Gmail continuously |
| `worker` | job_worker — morning brief, reflect, email_copy, callbacks |

---

## Prerequisites

**Dedicated Gmail account**
JARVIS reads and replies from its own Gmail (not your personal one):

1. Create the account; enable 2-Step Verification (required for app passwords).
2. Generate an **App Password** (Account → Security → App passwords, 16 chars).
3. Set `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD`.
4. Set `ALLOWED_SENDERS` to your real address(es) — whitelist is enforced.

**Google Calendar** — service-account JSON, base64-encoded, in
`GOOGLE_SERVICE_ACCOUNT_JSON`; `GOOGLE_CALENDAR_ID` set to your calendar.

**Google Docs/Sheets** — same service account; the OAuth scopes include
Drive + Docs + Sheets.

**Twilio** — `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
for SMS; add a voice-capable number and set `TWILIO_VOICE_NUMBER` for calls.

**Alpaca** — `ALPACA_API_KEY` + `ALPACA_SECRET_KEY`. Trading is off by default
(`ENABLE_TRADING=false`).

**Flight booking** — `DUFFEL_API_KEY` (test/search mode) and
`DUFFEL_LIVE_API_KEY` (live booking — separate key, only used when
`BOOKING_ENABLED=true`). Booking is off by default; enable only after verifying
your Duffel live access. Second factor requires `TOTP_SECRET` (a base32 secret
enrolled in your authenticator app).

**Maps / traffic** — `GOOGLE_MAPS_API_KEY`.

**News** — `TAVILY_API_KEY`.

---

## Local development

**Prereqs:** Docker, Python 3.11, Node 20.

```bash
# 1) Local Postgres
docker compose up -d

# 2) Backend
cd backend
python -m venv .venv
# Windows PowerShell: py -3.11 -m venv .venv ; .\.venv\Scripts\Activate.ps1
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.template .env        # fill in keys (see below)
uvicorn app.main:app --reload   # API + built UI at http://localhost:8000

# 3) (optional) live-reloading frontend
cd ../ui && npm install && npm run dev   # http://localhost:5173 (proxies /api)

# 4) (optional) background workers, each in its own terminal
python -m app.channels.email_pipeline --watch     # email intake
python -m app.workers.job_worker --watch          # job queue, morning brief
```

**Minimum `.env` for web chat:** `ANTHROPIC_API_KEY`, `JWT_SECRET`,
`DATABASE_URL` (docker-compose default works), `SEED_PASSWORD`.

**What works locally vs. what needs external services:**

- **Web chat, Alpaca, Google Calendar/Docs** — work if you supply the keys.
- **Email intake** — works; polls IMAP outward.
- **SMS / voice inbound** — Twilio can't reach `localhost`. Use an ngrok tunnel
  (`ngrok http 8000`) and point the Twilio webhook at the ngrok URL.
- **Morning brief** — NWS and Tavily calls go outbound; works if keys present.
  Traffic requires `GOOGLE_MAPS_API_KEY` + home/work addresses in settings.

---

## Tests

```bash
cd backend && pytest
```

Fully offline — stubs the LLM, SMS, and all external APIs. No keys required.

282 tests covering the orchestrator, channels, handlers, confirmation gate,
TOTP second factor, morning brief, and every sub-agent.

---

## Deploy to Fly.io

```bash
fly launch           # first time: creates app + Postgres
fly secrets set ANTHROPIC_API_KEY=... JWT_SECRET=... # (and the rest)
fly deploy
```

The `fly.toml` runs three processes. Scale them independently:

```bash
fly scale count api=2 ingest=1 worker=1
```

Feature flags in secrets (all default off in production):
- `ENABLE_TRADING=true` — enables `place_stock_order`
- `BOOKING_ENABLED=true` — enables `book_flight` (requires `DUFFEL_LIVE_API_KEY` + `TOTP_SECRET`)
