# TDD — Scheduler Hardening, Runtime Settings & Admin Settings UI

**Author:** JARVIS design session (Claude Project)
**For implementation by:** Claude Code (CLI, against live repo)
**Status:** Draft for review
**Date:** 2026-07-15

---

## 1. Problem statement

Three failures surfaced together and share one root cause: **reliability-critical
state lives in ephemeral or invisible places with no self-check.**

1. **The daily brief/call never fires on its own.** It depends on an in-memory
   APScheduler cron started inside the `worker` process, gated behind three
   env-var flags (`BRIEFING_ENABLED`, `OUTBOUND_CALLS_ENABLED`,
   `BRIEFING_BY_PHONE`) that all default `False`. The only briefs the owner has
   received were *manually* triggered by email — masking that the scheduled path
   has likely never run.

2. **A 4 AM briefing call is currently impossible even fully configured.**
   Quiet hours (`21:00–07:00`) suppress any non-`callback` call in `due_calls`,
   and the owner's intended window is `21:00–03:30`, which the config **cannot
   represent** — `quiet_hours_end` is an integer hour, minutes hardcoded to `0`.

3. **No visibility.** Every one of the above was discovered by *experiencing an
   absence* (no call, no email) and then reading logs by hand. There is no
   surface that shows what's enabled, what time things run, or whether the
   scheduler is even alive.

### Design principle

Settings that govern autonomous behavior must be (a) **visible**, (b) **runtime-
mutable without a redeploy**, and (c) **safety-bounded server-side**. The
safety-critical guardrails (outbound-enabled, quiet hours, rate cap) must remain
guardrails even when editable.

---

## 2. Current-state facts (verified against repo @ this session)

- `app/config.py`: `Settings(BaseSettings)`, exposed as a **module-level
  `@lru_cache` singleton** `settings = get_settings()`. Read once per process.
- `app/workers/job_worker.py::_start_briefing_scheduler()` builds a
  `BackgroundScheduler`, binds `hour=settings.briefing_hour,
  minute=settings.briefing_minute` **once at worker startup**, and never re-reads.
- `app/channels/outbound_voice.py::in_quiet_hours()` uses
  `time(settings.quiet_hours_start, 0)` / `time(settings.quiet_hours_end, 0)` —
  **minutes hardcoded**. `due_calls()` exempts only `kind == "callback"`.
- Defaults: `briefing_enabled=False`, `briefing_hour=6`, `briefing_minute=30`,
  `outbound_calls_enabled=False`, `briefing_by_phone=False`,
  `quiet_hours_start=21`, `quiet_hours_end=7`, `max_outbound_calls_per_hour=6`.
- Admin surface exists: `GET/POST/PUT/DELETE /api/agents`, `/api/audit`,
  `/api/calendar/health`, `/api/infra/health`, all auth-gated (`auth_headers`).
- `actions_audit` table exists and is the right place to log setting changes.
- No `settings`/KV table exists yet.

---

## 3. Scope

**In scope**
- A runtime settings overlay (DB table) that supersedes env defaults for a
  bounded, explicit allow-list of keys.
- Minute-granularity quiet hours.
- Briefing call exempt from quiet hours (opt-in, since the owner scheduled it).
- Scheduler: reschedule on settings change; startup heartbeat; missed-run
  catch-up.
- Admin API: read settings (with source + effective value), update with
  validation + audit.
- Admin UI: a Settings panel — **status/read-only first**, then guarded edits.

**Out of scope (explicitly)**
- Secrets (tokens, API keys) are NEVER surfaced or editable in the settings
  overlay or UI. Those stay in Fly secrets. The overlay is for *behavioral*
  settings only.
- Per-user settings. Single-owner system; global settings only.

---

## 4. Design

### 4.1 Runtime settings overlay

New table `runtime_settings` (Alembic migration, Postgres dialect guard per the
`0001` convention; never rely on `create_all`):

| column      | type      | notes                                  |
|-------------|-----------|----------------------------------------|
| `key`       | str, PK   | must be in `SETTINGS_ALLOWLIST`        |
| `value`     | str       | stored as string; typed on read        |
| `updated_at`| datetime  |                                        |
| `updated_by`| str       | actor for audit                        |

**Allow-list (the ONLY keys the overlay/UI may touch):**
```
briefing_enabled           (bool)
briefing_by_phone          (bool)
briefing_hour              (int, 0–23)
briefing_minute            (int, 0–59)
outbound_calls_enabled     (bool)   # SAFETY-CRITICAL
quiet_hours_start          (int, 0–23)
quiet_hours_start_minute   (int, 0–59)   # NEW
quiet_hours_end            (int, 0–23)
quiet_hours_end_minute     (int, 0–59)   # NEW
max_outbound_calls_per_hour(int, 1–20)  # SAFETY-CRITICAL, bounded
```

**Effective-value resolution.** Add `effective(key)` accessor used by all
runtime code paths that read these keys:
1. If `key` present in `runtime_settings` → typed overlay value.
2. Else → the env/`Settings` default.

Do **not** mutate the `@lru_cache` singleton. Add a thin
`app/runtime_settings.py` module with `get_effective(db, key)` and a cached
snapshot invalidated on write. Callers in scope: `in_quiet_hours`, `due_calls`,
the briefing scheduler enqueuer, `place_calls` rate cap.

> **Rationale.** This is the crux. A UI that writes a row nobody reads is the
> same silent-no-op fragility we're removing. Every setting in the allow-list
> must have its runtime reader switched from `settings.X` to
> `get_effective(db, "X")` in the same PR that exposes it.

### 4.2 Minute-granularity quiet hours

- Add `quiet_hours_start_minute: int = 0`, `quiet_hours_end_minute: int = 0` to
  `Settings`.
- `in_quiet_hours()` builds
  `time(get_effective(db,"quiet_hours_start"), get_effective(db,"quiet_hours_start_minute"))`
  and the matching end. Preserve the existing wrap-midnight branch.
- **Note:** `in_quiet_hours` currently takes no `db`. Thread a `db` through, or
  give `runtime_settings` a process-cached snapshot refreshed on write so
  `in_quiet_hours` can stay dependency-light. Implementer's choice; test both
  the wrap and non-wrap cases.

### 4.3 Briefing call exempt from quiet hours

In `due_calls`, change the guard so a scheduled briefing the owner explicitly
configured is treated like a callback:
```
if r.kind not in ("callback", "briefing") and in_quiet_hours(now):
    continue  # suppress
```
This makes a 4 AM brief fire regardless of the quiet window — the owner set the
time on purpose. Quiet hours still suppress *alerts* and ad-hoc calls.

> With this exemption, the owner's 21:00–03:30 window and a 04:00 brief coexist
> cleanly, AND the brief would fire even if they later moved it to 02:00.

### 4.4 Scheduler hardening

**(a) Reschedule on change.** The scheduler must not bind `briefing_hour` once
forever. Options (implementer's choice, test the behavior not the mechanism):
- Keep an APScheduler job with a stable id and `reschedule_job()` when briefing
  time settings change, or
- Have the enqueuer read `get_effective` and run the cron every minute, firing
  only when now matches — simpler, no reschedule plumbing.

**(b) Startup heartbeat.** On worker start, log a single explicit line stating
whether the scheduler is enabled and the effective time, e.g.
`briefing scheduler ACTIVE — 04:00 America/Los_Angeles` or
`briefing scheduler DISABLED (briefing_enabled=false)`. Also write a
`scheduler_heartbeat` row/among audit so "is it alive?" is queryable, not
inferred.

**(c) Missed-run catch-up.** Persist `last_briefing_date` (date in owner tz).
On worker startup AND on each tick, if today's scheduled brief time has passed
and `last_briefing_date != today` and briefing is enabled → enqueue it now and
set the date. This closes the "worker restarted across 04:00 → run silently
lost" gap. Guard against double-fire with the date check.

**(d) Empty-brief visibility.** `briefing_call` currently returns
`"nothing to brief"` silently when compose yields empty. Escalate: if a
*scheduled* brief composes empty, notify the owner (email) rather than vanish.

### 4.5 Admin API

- `GET /api/settings` → list of `{key, effective_value, source: "override"|"default",
  type, safety_critical: bool, bounds}`. Auth-gated. **Never** includes secrets.
- `PUT /api/settings/{key}` → validate against allow-list + type + bounds;
  reject unknown keys (404) and out-of-bounds (422). On success: upsert row,
  invalidate snapshot, write an `actions_audit` entry
  (`tool="settings_update"`, before/after), and if the key affects briefing
  timing, trigger reschedule. Safety-critical keys (`outbound_calls_enabled`,
  `max_outbound_calls_per_hour`) require an explicit `confirm: true` in the body.
- `DELETE /api/settings/{key}` → remove override (revert to default), audited.

### 4.6 Admin UI (React SPA)

Phase the UI to get the observability win with zero risk first:
- **Read-only Settings status panel** (ship first): table of every allow-list
  key, effective value, source badge (Override/Default), and a live
  scheduler-status line (from heartbeat). This alone would have made today's
  triage a glance.
- **Editable fields** (ship second): inline edit with client + server bounds.
  Safety-critical keys visually flagged and require a confirm dialog. Each save
  shows the resulting audit entry id.

---

## 5. Test table

| # | Area | Test | Expected |
|---|------|------|----------|
| 1 | overlay | `get_effective(db,"briefing_hour")` with no row | returns env default (6) |
| 2 | overlay | set override `briefing_hour=4`, read | returns 4 |
| 3 | overlay | typed read of bool `briefing_enabled="true"` | Python `True` |
| 4 | overlay | unknown key rejected on write | error / 404 |
| 5 | quiet | end 03:30, now 03:15 (start 21) | `in_quiet_hours` True |
| 6 | quiet | end 03:30, now 03:45 | False |
| 7 | quiet | non-wrap window 13:00–14:00, now 13:30 | True |
| 8 | quiet | minutes default 0 preserves old behavior | unchanged vs pre-change |
| 9 | due_calls | `kind="briefing"` at 04:00 with quiet 21–03:30 | NOT suppressed |
| 10 | due_calls | `kind="alert"` at 02:00 in quiet | suppressed |
| 11 | due_calls | `kind="callback"` at 02:00 in quiet | NOT suppressed (unchanged) |
| 12 | scheduler | briefing time override → reschedule | next fire reflects new time |
| 13 | scheduler | startup with briefing enabled | logs ACTIVE + effective time |
| 14 | scheduler | startup disabled | logs DISABLED, no job registered |
| 15 | catch-up | worker starts 04:05, no brief today, enabled | enqueues brief once |
| 16 | catch-up | same tick runs again | does NOT double-enqueue (date guard) |
| 17 | catch-up | brief already sent today | no catch-up enqueue |
| 18 | empty-brief | scheduled brief composes empty | owner notified, not silent |
| 19 | api | `GET /api/settings` unauth | 401 |
| 20 | api | `GET /api/settings` | no secret keys present |
| 21 | api | `PUT briefing_hour=25` | 422 (out of bounds) |
| 22 | api | `PUT outbound_calls_enabled=false` without confirm | rejected |
| 23 | api | `PUT outbound_calls_enabled=false` confirm:true | applied + audited |
| 24 | api | `PUT max_outbound_calls_per_hour=999` | 422 (bound 1–20) |
| 25 | api | successful PUT | writes actions_audit row |
| 26 | api | `DELETE` override | reverts to default, audited |
| 27 | migration | Postgres dialect guard applies cleanly | no create_all reliance |
| 28 | regression | existing outbound rate-cap still enforced via effective | cap holds |

---

## 6. Rollout / sequencing

1. **Immediate (no code):** set Fly secrets to get tomorrow's 4 AM call — see
   companion `jarvis-4am-call-setup.md`. Interim `QUIET_HOURS_END=3` until §4.2
   lands.
2. **PR-1:** minute-granularity quiet hours (§4.2) + briefing exemption (§4.3) +
   tests 5–11. Small, unblocks a *correct* 21:00–03:30 window.
3. **PR-2:** runtime overlay (§4.1) + effective readers + migration + tests
   1–4, 27–28.
4. **PR-3:** scheduler hardening (§4.4) + tests 12–18.
5. **PR-4:** settings API (§4.5) + tests 19–26.
6. **PR-5:** Admin UI read-only status, then editable.

Each PR is independently shippable and leaves the system working.

---

## 7. Risks / notes

- **Threading `db` into `in_quiet_hours`** touches several call sites. The
  process-cached snapshot alternative avoids that but must invalidate on write
  and on a short TTL so a worker sees UI changes within, say, 60s. Pick one and
  be consistent.
- **Reschedule vs run-every-minute:** the every-minute enqueuer is simpler and
  removes an entire class of "stale bound cron" bugs; slight cost is a 1/min
  tick. Given this system's scale, prefer simplicity unless a reason emerges.
- **Do not** let the settings overlay grow to cover secrets or arbitrary keys.
  The allow-list is the safety boundary; enforce it server-side, not just in UI.
