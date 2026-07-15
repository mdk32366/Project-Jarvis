# TDD — JARVIS Self-Health Loop

**Supersedes & unifies:** `TDD-jarvis-self-whoami.md` and
`TDD-scheduler-hardening-and-settings.md`. Both are folded in here; treat this
as the single source of truth for self-diagnosis, scheduler reliability, runtime
settings, and the Admin status surface. The two prior docs remain as historical
context but are no longer the build spec.

**Author:** JARVIS design session (Claude Project)
**For implementation by:** Claude Code (CLI, against live repo)
**Status:** Draft for review
**Prereq:** TDD #11 (datetime awareness) — everything here timestamps via
`get_current_datetime`. Build #11 first if not already done.
**Date:** 2026-07-15

---

## 0. The idea in one paragraph

Every failure this week — Calendar auth dead, the briefing scheduler switched
off, Tasker silently stopped reporting location — shared one property:
**nothing surfaced the problem until Matt hit it.** This TDD builds a single
closed loop: **detect** (health checks over credentials, internal state, and
data freshness) → **diagnose** (each check knows what a failure means) →
**remediate** (each check carries a stored, human-followable runbook) →
**surface** (state-driven, through the Morning Brief and, as the always-on
fallback, the Admin status page). The loop reuses surfaces that already exist
rather than inventing new ones.

---

## 1. What this IS

- A **relational model in Postgres** for the deterministic topology: a
  `component` inventory (every agent, API, and subsystem), a `remediation`
  table mapping `(component, fault_code)` → runbook, and a transient
  `health_result` for current status. Reference data lives in the DB, editable
  at runtime — not in code constants.
- **Explicit logging layers** (§4A): stdout (ephemeral debug), `actions_audit`
  (what JARVIS did), request log (what JARVIS was asked), `health_result` (can
  JARVIS do it) — four mechanisms, defined boundaries, with health reading audit
  as its evidence substrate rather than adding a fifth.
- A set of **health checks**, each producing a status + optional expiry/age
  signal; the **remediation runbook is joined from the DB**, not carried in code.
- A **state-driven surfacing rule**: the brief and any calendar reminders reflect
  *live check results*, not static schedules. A warning appears because a real
  condition is true and disappears when it's fixed.
- A **scheduler that can't silently die**: heartbeat, missed-run catch-up,
  reschedule-on-change.
- A **runtime settings overlay** so behavior (briefing time, quiet hours,
  outbound toggles) is visible and changeable without a redeploy.
- An **Admin status page** that renders all of the above — the fallback Matt can
  open any time, especially when the brief itself fails.
- A **self_whoami** tool + provenance + a coarse request log, so JARVIS can
  answer "what am I running, what have I done, and am I healthy" in conversation.

## 2. What this IS NOT (read this before building)

Explicit non-goals. If a change starts drifting toward any of these, stop.

- **NOT self-repair.** Health checks detect and *tell Matt how to fix*. They do
  not attempt to fix credentials, re-consent OAuth, or restart services
  themselves. The output of a tripped check is a runbook for a human, never an
  autonomous remediation action.
- **NOT runtime-generated fix advice.** Remediation text is *stored at build
  time* alongside each check (a lookup), never improvised by the LLM during an
  outage. A check that trips returns its pre-written runbook verbatim. We do not
  ask the model to reason out a fix live — that's exactly the "infer instead of
  verify" failure mode we're eliminating.
- **NOT static calendar reminders.** We do NOT set perpetual "re-consent every 85
  days" calendar events that fire whether or not they're needed and rot when the
  real window changes. Any calendar item is *written by a health check from live
  state* ("token expires Aug 12") and is an OUTPUT of the loop, never a hardcoded
  input.
- **NOT proactive paging in v1.** JARVIS does not text/call Matt unprompted when
  something looks degraded — not until passive data has been observed long enough
  to know the false-positive rate. v1 surfaces via the brief (pull) and Admin
  (pull). Push alerting is a later, separate decision.
- **NOT a full trace / APM / observability platform.** The request log is ONE
  coarse row per top-level request, queryable in conversation. It does not
  replace application logging and must not grow into a span tracer.
- **NOT a fabricated expiry countdown.** Services that don't publish an expiry
  don't get an invented one. Allowed signals are: liveness (did the last real
  call succeed), Fly-sourced secret age (real data Fly already holds), and true
  published expiry where it genuinely exists (OAuth refresh). Three honest tiers,
  no guessing.
- **NOT cross-repo / multi-app control.** Watching *this* system's health is in
  scope. Reaching into other repos/hosts, and anything beyond read-only, is
  explicitly parked (see §9) and must not sneak in under "self-awareness."
- **NOT "provably effective" in the sense of proving a runbook is eternally
  correct.** We prove the *outcome*: a check re-runs after remediation and either
  goes green (proof the fix worked) or stays red (signal the runbook went stale).
  The proof is the post-fix check result, not the runbook's existence.

---

## 3. Architecture — the loop

```
                 ┌─────────────────────────────────────────────┐
                 │  HEALTH CHECKS (each: status + signal + runbook) │
                 │                                             │
   credentials ──┤  • credential liveness (passive)            │
   internal   ───┤  • secret age (Fly metadata)                │
   freshness  ───┤  • published expiry (OAuth)                 │
                 │  • scheduler heartbeat                       │
                 │  • data freshness (location pings, etc.)    │
                 └───────────────┬─────────────────────────────┘
                                 │  live results
                 ┌───────────────┴───────────────┐
                 │        STATE (queryable)        │
                 └───┬───────────────┬─────────────┘
                     │               │
         pull        │               │        pull (always available)
   ┌─────────────────┴──┐      ┌─────┴──────────────────┐
   │  Morning Brief     │      │  Admin status page     │
   │  (exception-only:  │      │  (full current state — │
   │   silent if green) │      │   THE fallback)        │
   └────────────────────┘      └────────────────────────┘
```

**Key property:** the checks are the single source of truth. Both the brief and
Admin read the same check results. If the brief fails to send, Admin still shows
the truth. If Admin is down, the brief still reports. Neither is authoritative
over the other; the check state is.

---

## 4. The relational data model — deterministic topology lives in Postgres

**Principle (Matt's, adopted):** everything deterministic about the system —
what components exist, what type each is, and what to do when a given fault
fires — is stable reference data and belongs in Postgres, not in code constants.
The health *result* (is it green right now) is transient; the *topology and
remediations* are permanent. Two different concerns, two different homes.

This replaces the earlier "remediation as a check-class constant" design. A
fault is a **join**, not a code lookup: a tripped check emits a `fault_code`
against a `component`, and the surfacing layer joins to the matching
`remediation` row to tell Matt where to start.

### 4.1 `component` — the system inventory (seeded from the topology diagram)

The nodes from the orchestration topology, one row each. Stable reference data;
seeded via Alembic migration (Postgres dialect guard, `0001` convention),
editable at runtime like the agent roster.

| column | type | notes |
|---|---|---|
| `name` | str, PK | `researcher`, `scheduling`, `duffel`, `worker_scheduler`, ... |
| `kind` | str | `agent` \| `external_api` \| `internal_subsystem` \| `data_feed` |
| `depends_on` | str | comma/JSON list — e.g. agent→its API(s); API→secret name |
| `check_type` | str | which check applies: `liveness` \| `secret_age` \| `published_expiry` \| `heartbeat` \| `freshness` \| `none` |
| `blast_radius` | str | `single` \| `multi` — trunk subsystems are `multi` |
| `enabled` | bool | |

**Seed rows (from the verified topology):**
- Agents (`kind=agent`): researcher, finance, archivist, infra, secretary,
  travel, navigator, netstatus, scheduling.
- External APIs (`kind=external_api`): tavily, alpaca, gmail, google_oauth
  (Contacts/Tasks/Docs/Sheets), google_calendar_svcacct, duffel, google_maps,
  twilio, proxmox, uptime_kuma, tailscale, nws.
- Internal subsystems (`kind=internal_subsystem`, `blast_radius=multi`):
  anthropic_api (every agent's LLM), postgres, worker_scheduler, email_ingest.
- Data feeds (`kind=data_feed`): location_pings.

> The trunk (`anthropic_api`, `postgres`, `worker_scheduler`, `email_ingest`)
> is `blast_radius=multi` — a failure there takes down many limbs at once, so
> these check first and surface most prominently.

### 4.2 `remediation` — the fault→fix mapping (the "place to start")

Keyed to `(component, fault_code)`. This is the table that satisfies "when
there's a fault code, there should be a remediation path communicated to me."
Seeded, runtime-editable (so when Google changes a consent flow, Matt edits a
row — no redeploy).

| column | type | notes |
|---|---|---|
| `component` | str, FK→component.name | |
| `fault_code` | str | `auth_invalid`, `token_expired`, `401`, `stale`, `down` |
| `runbook` | text | human-followable steps; the "place to start" |
| `severity` | str | `info` \| `warn` \| `critical` |
| `updated_at` | datetime | |

**Example seed rows:**
- (`google_oauth`, `token_missing_scope`) → "Docs/Sheets scope missing. `cd
  backend && python -m app.google_oauth --client-secrets <path>`, then `fly
  secrets set GOOGLE_OAUTH_REFRESH_TOKEN=<new>`."
- (`google_calendar_svcacct`, `auth_invalid`) → "Service account lost calendar
  scope or the calendar isn't shared with it. Re-share the calendar with the
  service-account email; verify scope in `scheduling.py`."
- (`duffel`, `401`) → "Duffel rejected the key. Check `DUFFEL_API_KEY`; if
  live-mode, confirm activation and prepaid balance."
- (`worker_scheduler`, `heartbeat_stale`) → "Worker not reporting. `fly apps
  restart jarvis-mdk`; confirm log line `briefing scheduled daily at HH:MM`."
- (`location_pings`, `stale`) → "No pings in N hours. Phone-side — see
  `tasker-setup-and-recovery.md`; confirm the Tasker project is enabled and has
  background-location + battery-unrestricted."
- (`twilio`, `a2p_rejected`) → "SMS blocked by A2P. Re-register brand under EIN
  as a business; resubmit campaign with business framing. Voice is unaffected."

### 4.3 `health_result` — transient current state (NOT reference data)

The one piece that isn't deterministic. Latest status per component; overwritten
each check. Kept deliberately separate from the two reference tables above.

| column | type | notes |
|---|---|---|
| `component` | str, FK→component.name | |
| `status` | str | `ok` \| `degraded` \| `down` \| `unknown` |
| `fault_code` | str \| null | null when ok; else joins to `remediation` |
| `detail` | str | one-line summary |
| `checked_at` | datetime | via `get_current_datetime` |
| `expires_at` | datetime \| null | ONLY where a service publishes one |
| `age_days` | int \| null | from Fly secret metadata |
| `last_success_at` / `last_failure_at` | datetime \| null | liveness |

### 4.4 The check interface (thin — reads/writes the tables above)

```python
@dataclass
class HealthResult:
    component: str
    status: str
    fault_code: str | None
    detail: str
    checked_at: datetime
    expires_at: datetime | None = None
    age_days: int | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None

class HealthCheck(Protocol):
    component: str            # FK into component table
    def run(self, db: Session) -> HealthResult: ...
```

- Checks are **registered per component**, driven by `component.check_type`.
- A check produces a `HealthResult` (→ upserts `health_result`); it does **not**
  carry the runbook. The runbook is fetched by joining `(component, fault_code)`
  to `remediation` at surface time. Detection and remediation are decoupled:
  checks detect, the DB holds the fix.
- **A check must never raise into its caller.** A failing check returns
  `status="unknown"` with detail, never a 500.

---

## 4A. Logging & record-keeping — four layers, explicit boundaries

**Matt raised this and it was a real gap.** Once this TDD lands there will be up
to four record-keeping mechanisms. Left implicit they blur into a confusing mess
where nobody knows which to query. This section fixes their boundaries. Each
answers a *different question*; none replaces another.

| Layer | Question it answers | Home | Lifespan | Exists? |
|---|---|---|---|---|
| **stdout logs** | "what's happening right now, in detail" | Fly log stream | ephemeral | ✅ exists |
| **`actions_audit`** | "what did JARVIS *do*" (per tool execution) | Postgres | durable | ✅ exists |
| **request log** | "what was JARVIS *asked*" (per top-level request) | Postgres | durable (retention §11) | planned (§9 Phase 2) |
| **`health_result`** | "can JARVIS *do it* — is each limb alive" | Postgres | transient (latest only) | planned (this TDD) |

### Boundaries (the discipline)

- **stdout** = raw debug for live tailing during development. Plain-text, level
  configurable. NOT queryable historically; do not build features that depend on
  grepping it (that's the failure mode all week). If something needs to be
  queryable later, it goes in a table, not a log line.
- **`actions_audit`** = the durable record of *actions* — every tool execution,
  already captured (channel, actor, tool, args, result, status, ts). This is
  ground truth for "did the booking go through," "was the email sent."
- **request log** = coarser than audit — ONE row per top-level request (channel,
  trigger, disposition, duration, error). Audit is per-*tool*; request log is
  per-*request*. A single request ("book me a flight") produces one request-log
  row and several audit rows (search, gate, book). Don't merge them; they're
  different grains.
- **`health_result`** = current capability state, not history. Overwritten each
  check. "Is Calendar auth working *now*," not "every time it failed."

### How they connect — the bridge from "check tripped" to "what happened"

This is the part that was missing. A red health badge alone is a dead end; the
value is being able to see the *evidence*. So:

- When a check writes `health_result.status != ok` for a component, the
  **diagnosis view joins to the durable logs** for that component — the recent
  `actions_audit` rows (and request-log rows) whose tool/agent maps to that
  component, filtered to `status != ok`. That turns "scheduling: down" into
  "scheduling: down — last 3 calendar_lookup calls failed with auth_invalid at
  04:00, 06:30, 07:15" plus the remediation runbook.
- **Mapping requires a link from tool/agent → component.** The `component` table
  already names agents and APIs; add a lightweight lookup (tool name → owning
  component) so audit rows can be grouped by component. This is the join key
  between the action record and the health/topology model.
- **Passive health checks READ audit, they don't duplicate it.** The credential
  liveness check (§5.1) derives `last_success_at`/`last_failure_at` by reading
  recent `actions_audit` for that component's tools — no separate per-call
  bookkeeping. Audit already records every execution's `status`; liveness is a
  query over it, not a parallel write path. (Where a component is called too
  rarely for audit to be fresh, a low-frequency active ping supplements — §5.1.)

> Net: `actions_audit` becomes the evidence substrate the health layer reads
> from. This avoids a fifth mechanism and means "why is this red" always traces
> to real recorded actions, never to a guess.

### What this is NOT

- NOT a new logging framework. stdout stays as-is; we're not adding structured
  JSON logging or an APM in this TDD.
- NOT moving stdout into Postgres wholesale. Only the deterministic/queryable
  records live in tables; debug spew stays ephemeral.
- NOT a per-tool-call health write. Liveness reads audit; it doesn't add a write
  to every handler.

---

## 5. The checks (v1 set)

Bounded. These three categories close the three failure classes seen this week.

### 5.1 Credential health
Passive-first, and crucially **derived from `actions_audit`, not a new write
path** (see §4A). The liveness check reads recent audit rows for the tools owned
by a component and computes `last_success_at` / `last_failure_at` from their
`status`. No decorator on every call site, no latency added to user-facing calls
— audit already records the outcome; liveness is a query over it. Where a
component is called too rarely for audit to stay fresh, a low-frequency active
ping (daily) supplements — never a tight loop.

Signals per the three honest tiers:
- **Liveness** — from passive call outcomes; a daily low-frequency active ping
  only for services called too rarely to stay fresh.
- **Secret age** — from `fly secrets list` metadata (real, free). Flag past a
  configurable threshold. Cache with short TTL (hourly); never per-request.
- **Published expiry** — OAuth refresh only. Do not fabricate for others.

Runbooks: OAuth → re-consent command (above). Twilio → "A2P registration status:
check console; re-register brand under EIN if rejected." Etc.

### 5.2 Internal state — scheduler heartbeat
The briefing scheduler must be provably alive, not inferred from log-grepping.
- On worker start, log explicitly AND write a `scheduler_heartbeat` record with
  effective next-run time.
- Health check reads the heartbeat: if the worker's alive and briefing enabled →
  `ok` with next-run; if disabled → `ok` but clearly "disabled"; if the heartbeat
  is stale (worker died) → `down`.
- Runbook: "Worker not reporting. `fly apps restart jarvis-mdk`; confirm log line
  `briefing scheduled daily at HH:MM`."

### 5.3 Data freshness — location pings (and future feeds)
Catches the Tasker-silence class from the *server* side — the one thing the
server can actually see.
- Check: time since last `LocationPing`. If > threshold (configurable, e.g. 2h
  during expected active hours) → `degraded` "no location ping in N hours."
- **Cannot see or fix Tasker** — this reports the *symptom* (no data arriving),
  and its runbook points at the phone-side recovery (`tasker-setup-and-recovery`)
  and the version-controlled project export.
- Explicitly generalizable: any future inbound feed gets a freshness check of the
  same shape.

---

## 6. Scheduler hardening (folded in from the prior TDD)

Unchanged in intent from `TDD-scheduler-hardening-and-settings.md`; restated so
this is the single spec.

- **Reschedule on change** — do not bind `briefing_hour` once at startup forever.
  Prefer the simpler "tick every minute, fire when now matches effective time"
  enqueuer over stateful reschedule plumbing, unless a reason emerges.
- **Startup heartbeat** — §5.2.
- **Missed-run catch-up** — persist `last_briefing_date` (owner tz). On worker
  start and each tick, if today's scheduled time has passed, briefing enabled, and
  no brief sent today → enqueue once, guard against double-fire with the date.
- **Empty-brief visibility** — a scheduled brief that composes empty notifies
  rather than silently returning "nothing to brief."

---

## 7. Runtime settings overlay + minute-granularity quiet hours (folded in)

Also unchanged from the prior TDD; restated.

- New `runtime_settings` table (Alembic, Postgres dialect guard per the `0001`
  convention; never rely on `create_all`). Bounded **allow-list** of behavioral
  keys only — **NEVER secrets**.
- Allow-list: `briefing_enabled`, `briefing_by_phone`, `briefing_hour`,
  `briefing_minute`, `outbound_calls_enabled` (safety-critical),
  `quiet_hours_start`, `quiet_hours_start_minute` (NEW), `quiet_hours_end`,
  `quiet_hours_end_minute` (NEW), `max_outbound_calls_per_hour` (safety-critical,
  bounded 1–20).
- **`get_effective(db, key)`** overlay accessor: DB override if present, else the
  env/`Settings` default. **Do not mutate the `@lru_cache` singleton.** Every
  runtime reader of an allow-list key switches from `settings.X` to
  `get_effective(db, "X")` in the same PR that exposes it — a UI that writes a row
  nobody reads is the exact silent-no-op fragility we're removing.
- **Minute-granular quiet hours**: add the `*_minute` fields; `in_quiet_hours`
  builds `time(hour, minute)` from effective values; preserve the wrap-midnight
  branch. Unblocks Matt's real 21:00–03:30 intent.
- **Briefing exempt from quiet hours**: in `due_calls`, change the guard to
  `if r.kind not in ("callback", "briefing") and in_quiet_hours(now): continue`.
  A brief Matt scheduled at 4 AM fires regardless of the window; alerts still
  suppress.

---

## 8. Surfacing

### 8.1 Morning Brief — exception-only
One new section reads all check results and includes **only** what's `degraded` /
`down` / expiring-soon / stale. Silent when everything's green (matches Matt's
exception-reporting preference). Each surfaced item shows its one-line detail and,
if not ok, its stored runbook. Where a real expiry exists and is near, the brief
may note it *and* (optionally) write a dated calendar item as an OUTPUT (§2: never
a static reminder).

### 8.2 Admin status page — the always-available fallback
**This is the backstop for everything.** If the brief fails to send, is empty,
or Matt just wants to look, the Admin page renders the full current state on
demand — a live view of the topology with each limb's status:
- Every component: name, kind, status badge (from `health_result`), detail,
  timestamps.
- When not ok: the **runbook joined from `remediation`** shown inline (the
  "place to start"), PLUS the **evidence** — the recent failing `actions_audit`
  rows for that component (§4A bridge), so "scheduling: down" comes with the
  actual failed calls, not just a red dot.
- Trunk components (`blast_radius=multi`) surface first/most prominently.
- Scheduler: alive/next-run/last-run, from the heartbeat.
- Settings: effective values + source (override/default) — from §7.
- Note: **Admin and Status tabs already exist** in the UI. EXTEND the existing
  surface (`/api/infra/health`, `/api/calendar/health` are already there); do not
  build a parallel one.
- Ship **read-only status first** (pure observability, zero risk), editable
  settings second (safety-critical keys gated + audited).

---

## 9. self_whoami, provenance, request log (folded in from self-whoami TDD)

Retained as specified in the original self-whoami TDD; summarized here.

- **Phase 1 — Git provenance**: version baked at build time (not a live
  `git` shell-out), Fly deploy metadata, `days_in_service`. Cheap, ships first.
- **Phase 2 — Request log**: one coarse `RequestLog` row per top-level request
  (received_at, channel, trigger, disposition, duration, error_detail). Receipt
  write commits independently so a crashed request still leaves a row. This is
  the piece that makes "what happened with my 4 AM call" a query, not a memory
  exercise. Retention policy required (§11).
- **`self_whoami` tool**: composes provenance + request-log rollup + health
  summary. Read-only, ungated, available everywhere like `get_current_datetime`.

### Parked — multi-repo provenance ("head watching the limbs")
Explicitly NOT in this build. Requires an inventory of other hosted apps and a
real access-model decision (read-only, scoped, GitHub API at most). Anything
beyond read gets booking-gate-level scrutiny. Do not build speculatively.

---

## 10. Test table (new/changed; folded TDDs keep their own tables)

| # | Area | Test | Expected |
|---|------|------|----------|
| 1 | check iface | a check that errors internally | returns `status="unknown"`, no raise |
| 2 | check iface | remediation shown only when not ok | `ok` result omits runbook |
| 3 | cred health | real call success updates last_success_at | via wrapper, not per-site |
| 4 | cred health | health side-effect never blocks the call | no added latency/failure path |
| 5 | cred health | expiry only where published | OAuth yes; Duffel/Twilio no fabricated countdown |
| 6 | secret age | age from Fly metadata, not invented | traces to real `fly secrets list` timestamp |
| 7 | secret age | cached short TTL | Fly not queried per-request |
| 8 | secret age | past threshold flagged | stale flagged, fresh not |
| 9 | scheduler | heartbeat written on start | record + effective next-run present |
| 10 | scheduler | stale heartbeat | check reports `down` |
| 11 | scheduler | disabled | check `ok` + "disabled", not down |
| 12 | freshness | no ping past threshold | `degraded` "no ping in N hours" |
| 13 | freshness | runbook points to phone-side recovery | not an autonomous action |
| 14 | brief | all green | health section silent |
| 15 | brief | one degraded | section shows only that item + runbook |
| 16 | brief | calendar item is output of live state | never a static recurring reminder |
| 17 | admin | read-only status renders all checks | badges + inline runbooks |
| 18 | admin | brief-fail scenario | Admin still shows current truth |
| 19 | remediation | post-fix re-check goes green | proves outcome, not runbook existence |
| 20 | remediation | fix doesn't clear condition | stays red, flags stale runbook |
| 21 | overlay | get_effective override vs default | (from §7) |
| 22 | quiet | 21:00–03:30, now 03:15 / 03:45 | True / False |
| 23 | due_calls | briefing at 04:00 in old window | NOT suppressed |
| 24 | scheduler | missed-run catch-up | enqueues once, no double-fire |
| 25 | settings api | safety-critical key needs confirm | rejected without, audited with |
| 26 | whoami | self_whoami rolls up health summary | identity + activity + health |
| 27 | request log | crashed request logged as error | row exists, disposition=error |
| 28 | component tbl | seed inventory matches topology | all 9 agents + APIs + trunk present |
| 29 | remediation | tripped check joins to runbook | `(component, fault_code)` returns the right row |
| 30 | remediation | runbook editable at runtime | edited row surfaces without redeploy |
| 31 | remediation | missing runbook degrades gracefully | fault with no row → generic "no runbook; check logs", not crash |
| 32 | health_result | transient, overwritten not appended | latest status only, not history |
| 33 | logging bridge | liveness derived from actions_audit | no separate per-call write path |
| 34 | logging bridge | red status surfaces evidence rows | Admin shows the failing audit rows for that component |
| 35 | logging layers | audit vs request-log grain | one request → one request-log row + N audit rows |
| 36 | tool→component | audit rows group by component | mapping resolves tool name to owning component |

---

## 11. Open decisions — need Matt's call before build

1. **Request-log retention** — fixed count / time-based (90d?) / rollup. No
   strong default yet.
2. **Freshness threshold + active hours** — "no ping in N hours" only means
   something during hours Matt expects to be moving. Define the window (and
   whether it's a setting).
3. **Secret-age threshold** — default flag point (365d?), configurable.
4. **Active liveness ping location** — reuse the briefing scheduler's mechanism,
   or a separate low-frequency job? Decide against what's already there.
5. **The 4 AM wake-up-call crash** — track as its own bug ticket. Phase 2's
   request log makes it debuggable, but the crash itself is a handler bug, not a
   self-health gap. Recommend a separate ticket, done alongside Phase 2.
6. **When (if ever) push alerting** — v1 is pull-only (brief + Admin). Revisit
   after passive data shows a real false-positive rate.
7. **`actions_audit` retention** — now doubly load-bearing (audit AND the
   evidence substrate for health). If it's currently unbounded, liveness queries
   over it will slow as it grows. Decide retention/indexing (index on
   `created_at` + a component/tool column) alongside the request-log retention
   decision — they're the same class of problem.
8. **stdout → structured logging?** Explicitly deferred here (§4A "what this is
   NOT"). If Matt later wants queryable structured logs beyond audit, that's its
   own TDD, not this one. Flagged so it's a conscious deferral, not an oversight.

---

## 12. Build order

1. **PR-1** — minute-granularity quiet hours + briefing exemption (§7 subset).
   Smallest; lets Matt set the real 21:00–03:30 window and drop the interim
   `QUIET_HOURS_END=3`.
2. **PR-2** — runtime settings overlay + `get_effective` + migration (§7).
3. **PR-3** — scheduler hardening: heartbeat, catch-up, empty-brief visibility
   (§6, §5.2).
4. **PR-4** — relational model: `component` + `remediation` + `health_result`
   tables, seeded from the topology (§4), plus the tool→component mapping (§4A).
   Pure data + migration; no behavior yet. This is the foundation everything
   below reads from.
5. **PR-5** — health-check interface + registry + the v1 checks (§4.4, §5),
   writing `health_result` and (for liveness) reading `actions_audit` (§4A).
6. **PR-6** — Admin read-only status page: topology view, status badges,
   joined runbooks + evidence rows (§8.2). Then editable settings, gated+audited.
7. **PR-7** — brief health section, exception-only (§8.1).
8. **PR-8** — self_whoami Phase 1 (provenance) + Phase 2 (request log) (§9).

Each PR ships independently and leaves the system working. Phases 1–3 restore
reliability; 4–6 make state visible; 7 makes JARVIS able to talk about it.
Multi-repo (§9 parked) is not in this sequence.
