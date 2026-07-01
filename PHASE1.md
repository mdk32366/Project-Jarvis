# JARVIS — Phase 1 changes

Builds on Phase 0 (email + web chat, persona/memory, confirmation gate, audit).
This round adds: **runnable tests**, **trading disabled**, **SMS texting**, a
**durable job queue + worker**, and a **pgvector auto-memory reflector**.

## 1. Trading disabled (safety)
`place_stock_order` is now hard-disabled behind `ENABLE_TRADING` (default `false`).
While off, the tool is still registered so JARVIS answers honestly ("trading is
turned off"), but **no order is ever placed**. Read-only finance tools
(`get_stock_price`, `get_portfolio`) are unchanged. Flip `ENABLE_TRADING=true`
(after the dashboard has real auth) to restore the gated order flow — the
confirmation gate and threshold logic are intact and tested.

## 2. SMS texting (Twilio)
Text JARVIS from a whitelisted phone number.

- Inbound webhook: `POST /api/sms/inbound` (Twilio posts here). Protected by
  Twilio **signature validation** + a **phone-number whitelist**; replies as TwiML.
- Provider abstraction (`app/providers/sms.py`): `TwilioProvider` for prod,
  `StubProvider` for dev/tests (no account needed). Switching vendors later
  (e.g. Telnyx) is one class.
- Whitelist via `ALLOWED_NUMBERS` (E.164, comma-separated) or the
  `contacts_whitelist` table with `channel="sms"`.

**Twilio setup:** buy a number → complete US A2P 10DLC registration → set
`SMS_PROVIDER=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM_NUMBER`, `SMS_PUBLIC_URL=https://<app>.fly.dev/api/sms/inbound`,
and point the number's "A message comes in" webhook at that URL (HTTP POST).

## 3. Durable job queue + worker
`jobs` table + `app/jobs.py` (enqueue / claim / retry) + a new Fly **`worker`**
process (`python -m app.workers.job_worker --watch`). Jobs survive restarts,
retry up to `JOB_MAX_ATTEMPTS`, and failed jobs are deferred to a later poll
(no hot-loop). Observable at `GET /api/jobs`.

## 4. pgvector auto-memory reflector
After every turn the orchestrator enqueues a `reflect` job. The worker asks the
router model to extract durable facts, embeds them, and stores them — deduping
against semantically-similar existing memories. Recall is now **semantic**: the
system preamble pulls the facts most relevant to the current message.

- Embeddings (`app/embeddings.py`): `local` (offline, free, default) or `voyage`.
- Store (`app/vectorstore.py`): **pgvector** on Postgres (real `vector` column,
  `<=>` search); portable JSON + in-Python cosine on SQLite (dev/tests).
- Tunables: `ENABLE_REFLECTOR`, `EMBEDDING_PROVIDER`, `EMBEDDING_DIM` (default
  1024 — matches the migration's `vector(1024)`), `MEMORY_DEDUP_THRESHOLD`,
  `MEMORY_RECALL_K`.

## New Fly processes
`api` (HTTP), `ingest` (email watcher), **`worker`** (job queue). See `fly.toml`.

## Schema
Migration `alembic/versions/0001_phase1_schema.py` adds `memories.embedding`, the
`jobs` table, the pgvector extension, and `memory_embeddings` — written
defensively (safe on fresh or existing DBs). `alembic upgrade head` runs in the
Fly release command.

## Running the tests
```
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                 # 41 tests, no external services required
```
Tests use SQLite + a stubbed LLM and SMS provider, so they run fully offline.
