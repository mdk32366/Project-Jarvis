# Build Order — Self-Health Loop + True Status Page

**Date:** 2026-07-19
**For:** Claude Code (CLI, against live repo)
**Spec:** `docs/TDD-jarvis-self-health-loop.md` — that document is authoritative.
This is a session-scoped *build order*, not a restatement. Where this doc and the
TDD disagree, the TDD wins; flag the conflict rather than choosing silently.
**Also read:** `For 7/19 Work` (network + app-status extensions),
`docs/JARVIS-Admin-Guide.md` Part IV roadmap (R1–R8).

---

## 0. Why now

The Status page at `/status` currently renders three lines — API status,
environment, DB connected — all green, always. That is the pre-existing
`/api/health`, not the health loop. The TDD for the health loop has been written
and reviewed but **never implemented**. This build implements it and makes the
Status page a true render of real check state.

Owner ask, in his words: if he asks JARVIS how she's feeling, she should give a
detailed, accurate answer. Same state, two surfaces — chat and page must not be
able to disagree.

---

## STEP 0 — Ground truth before any code (report back, do not proceed)

Three questions. Answer all three in a written report before writing any
implementation code. The answers change what gets built.

### 0.1 Which prerequisites actually landed?

The Admin Guide roadmap lists R1–R3 as prerequisites for this work:

| ID | Item | Roadmap status |
|----|------|----------------|
| R1 | Minute-granularity quiet hours + briefing exemption | In progress (PR-1) |
| R2 | Runtime settings overlay + `get_effective` | Planned (PR-2) |
| R3 | Scheduler hardening (heartbeat, catch-up) | Planned (PR-3) |

Roadmap status is a claim, not evidence. Verify against the repo:

- Does `app/runtime_settings.py` (or equivalent) exist with `get_effective`?
- Does a `runtime_settings` table exist, with an Alembic migration?
- Is there a `scheduler_heartbeat` write on worker start?
- Are `in_quiet_hours`, `due_calls`, the briefing enqueuer, and the `place_calls`
  rate cap actually *reading* `get_effective`, or still reading `settings.X`?

**Why this gates everything:** the scheduler heartbeat check (§5.2) reads a
heartbeat record. If R3 never shipped, that check has nothing to read and will
report `unknown` forever. Likewise the morning-brief-time Admin defect is
*caused* by R2 being incomplete — the UI reads the env default because nothing
reads the overlay.

If R2/R3 are missing, they come first in this build. Report and confirm before
proceeding.

### 0.2 What do the existing `netstatus:*` tools do?

`actions_audit` shows these firing in production on Jul 19 00:28:
`netstatus:health`, `netstatus:node`, `netstatus:service`, `netstatus:tailscale`.

Network status was previously treated as unresolved design (`For 7/19 Work`
§1.1, "Pull or push? Kuma or Tailscale API?"). These tools existing suggests
that question may already be answered in code.

Report: what does each tool query, what does it return, does it hit Uptime Kuma
or the Tailscale API directly, and is node inventory hardcoded or discovered?

If they already provide node liveness, network status is not new architecture —
it's wiring existing tools into the `component` model, and the only open decision
left is which nodes are load-bearing (§2.3 below).

### 0.3 Can liveness detect a real failure?

`actions_audit` is populated and current — confirmed. Every tool shows `ok`,
with two intentional exceptions:

- `send_email` (6) and `create_event` (4) → `confirmed`
- `book_flight` (1) → `refused`

**These are gates working correctly and MUST map to `ok`, not fault.** A refused
gated call is a healthy system. Getting this backwards makes the page cry wolf on
its own safety machinery.

The consequence of an all-green audit: liveness will render everything green on
day one with no evidence it can detect a failure. Prove the negative path — a
test that feeds the liveness check synthetic failure rows and asserts `down`.
If the Calendar auth failures from last week are still inside the audit
retention window, use them as a real fixture. If they have aged out, that itself
is a finding: record the effective retention window in the report, because it
bounds how far back liveness can see.

---

## 1. Scope

### In

| Item | TDD ref |
|---|---|
| `component`, `remediation`, `health_result` tables + Alembic (PG dialect guard, no `create_all`) | §4.1 |
| Tool/agent → component lookup (join key for evidence) | §4A |
| `HealthCheck` protocol + `HealthResult` dataclass + check registry | §4 |
| Credential liveness, derived from `actions_audit` (read-only, no new write path) | §5.1 |
| Secret age from `fly secrets list` metadata, cached hourly | §5.1 |
| Published expiry — OAuth refresh **only** | §5.1 |
| Scheduler heartbeat check | §5.2 |
| Location ping freshness check | §5.3 |
| Application up-status | `For 7/19 Work` §1.2 |
| Network / tailnet status via existing `netstatus:*` tools | `For 7/19 Work` §1.1 |
| `GET /api/status/full` — parallelized, single payload | new |
| `self_whoami` tool reading the same check state | §1, R8 |
| Status page: exception-first, 30s poll | §3, R6 |

### Out — do not build, do not drift toward

- **Self-repair.** Checks detect and print a stored runbook. They never fix.
- **Runtime-generated fix advice.** Remediation text is stored data, joined from
  the DB. Never improvised by the LLM during an outage.
- **Push alerting.** Pull-only in v1 (R19, deferred until false-positive rate is
  known).
- **Test-results-in-Postgres.** Real, designed in `design-note-test-architecture`,
  separate build.
- **An endpoint that runs the test suite.** Explicitly rejected — arbitrary code
  execution on an internet-facing app that can book flights and send email.
- **Fabricated expiry countdowns.** Three honest tiers only: liveness, Fly secret
  age, true published expiry. A service that doesn't publish an expiry doesn't
  get an invented one.
- **Cross-repo / multi-app reach.** This system's health only.

---

## 2. Build sequence

Each step is a PR through the CI gate. Do not batch.

### 2.1 PR-A — Relational health model (roadmap R4)

Tables per TDD §4.1: `component`, `remediation`, `health_result`, plus the
tool→component lookup from §4A. Alembic migration with the Postgres dialect
guard per the `0001` convention.

**Seeding `component` is the important part.** Seed from the orchestration
topology: every agent, every external API, every subsystem.

> **Reconciliation, not append.** Per the `seed_agents()` lesson — seeding must
> reconcile existing rows (including descriptions and type), not only insert
> missing ones. Stale reference data is how the Ideas-agent defect happened.

Seeding the full component inventory is what surfaces the **Ideas agent Admin
defect**: if the agent exists in code but not in the DB roster, it appears as a
discrepancy between the seeded inventory and the live registry instead of a
silent absence.

Remediation rows are seeded here too — `(component, fault_code)` → runbook.
Author them at build time. Known runbooks:

- Google OAuth → `cd backend && python -m app.google_oauth --client-secrets <path>`,
  then `fly secrets set GOOGLE_OAUTH_REFRESH_TOKEN=<new>`
- Scheduler down → `fly apps restart jarvis-mdk`; confirm log line
  `briefing scheduled daily at HH:MM`
- Location ping stale → phone-side recovery; point at
  `docs/tasker-setup-and-recovery.md` and the version-controlled Tasker project
  export. **The server cannot see or fix Tasker** — this reports the symptom only.
- Twilio → A2P registration status: check console; re-register brand under EIN
  if rejected

### 2.2 PR-B — Check protocol, registry, and the v1 check set (R5)

`HealthResult` / `HealthCheck` exactly as TDD §4 defines them. Registry in the
same data-driven spirit as the agent registry: new check = new class, registered
once.

**A check must never raise into its caller.** A check that blows up returns
`unknown` with the exception in `detail`. One broken check must not take down the
status page — that would be the health system reproducing the exact failure mode
it exists to catch.

Checks: credential liveness (§5.1), secret age (§5.1), OAuth published expiry
(§5.1), scheduler heartbeat (§5.2), location ping freshness (§5.3), app
up-status (7/19 §1.2).

Status mapping rules, non-negotiable:

- `confirmed` and `refused` audit rows → `ok`. Gates working.
- No evidence → `unknown`. Never `ok`. Absence of failure is not health.
- Location ping: `ok` under 20 min, `degraded` under 60 min, `down` beyond.
  Thresholds configurable, not hardcoded.
- Scheduler: alive + enabled → `ok` with next-run; disabled → `ok`, clearly
  labeled "disabled"; heartbeat stale → `down`.

> **Freshness beats binary.** A 15-minute-cadence job that last succeeded six
> hours ago is failing. A naive "did it ever succeed" check calls that green.

### 2.3 PR-C — Network / tailnet status

Depends on the STEP 0.2 report.

- If `netstatus:*` already returns per-node liveness → wire those tools into the
  `component` model as a check. No new integration.
- If not → Uptime Kuma is the preferred source. Kuma already polls and holds
  history; duplicating that inside JARVIS is redundant work.

**Load-bearing vs informational is a `component` seeding decision.** Nine nodes
reporting equally is noise. Proxmox host and any node JARVIS actually depends on:
`down` = real fault. Everything else: informational. An iPad being asleep is not
a degraded system, and a check that says otherwise trains the owner to ignore the
page.

### 2.4 PR-D — `GET /api/status/full`

One endpoint, one payload, auth-gated.

Run checks in parallel via `ThreadPoolExecutor` — per the briefing-parallelization
lesson, sequential async calls with additive timeouts are what produced the ~105s
brief. **DB-bound checks stay on the main thread** for SQLAlchemy session safety.

Per check, return: `{name, component, status, detail, checked_at, expires_at?,
age_days?, last_success_at?, last_failure_at?, remediation?, evidence?}`.

`remediation` is populated only when `status != ok`, joined from the DB.
`evidence` is the recent non-ok `actions_audit` rows for that component (§4A) —
this is what turns "scheduling: down" into "scheduling: down — last 3
calendar_lookup calls failed with auth_invalid at 04:00, 06:30, 07:15."

Endpoint-level timeout with a per-check budget. A hung external call must
degrade that one check, not the request.

### 2.5 PR-E — Status page (R6)

Replace the current three-line page. Exception-first, per the owner's standing
principle: **surface what's wrong, not what's fine.**

- Top band: only non-`ok` items, with detail, evidence, and runbook.
- Everything healthy collapses to one line — "14 checks OK" — expanding on click.
- `unknown` renders as its own visual state. Not green. Never quietly green.
- Poll `/api/status/full` every 30s. Show `checked_at` and a stale indicator if
  a poll fails, so a frozen page is visibly frozen rather than confidently wrong.
- Fix `Signed in as ....` — currently truncated or unrendered.

### 2.6 PR-F — `self_whoami` (R8)

Reads the same registry and the same check state as the endpoint. Chat and page
must be incapable of disagreeing — one source, two renderers.

Answers: what am I running (provenance/commit), what have I done recently
(request log / audit), am I healthy (check state). "How are you feeling" returns
detail, not a summary adjective.

---

## 3. Admin defects — expected disposition

Three open Admin defects. Two should close as a consequence of this build; the
third needs explicit verification.

| Defect | Expected outcome |
|---|---|
| Ideas agent missing from Admin roster | Surfaces as a component/registry discrepancy once `component` is seeded and reconciled (2.1). Confirm the underlying `seed_agents()` reconciliation is fixed, not just made visible. |
| Morning brief preferred time displays wrong | Root cause is R2 — UI reads env default because the runtime-settings reader was never switched. Verify in STEP 0.1; fix as prerequisite if R2 is incomplete. |
| Network + app status missing from morning brief | In scope via 2.3 / 2.4. Brief health section itself is R7, exception-only — next build. |

---

## 4. Definition of done

- [ ] STEP 0 report delivered and reviewed before implementation began
- [ ] R2/R3 verified present, or built as prerequisites
- [ ] Migration applies cleanly to `jarvis_mdk`; no `create_all` reliance
- [ ] Component seeding reconciles on every startup — tools, descriptions, type
- [ ] Every check returns `unknown` rather than raising, proven by test
- [ ] `confirmed` / `refused` audit statuses map to `ok`, proven by test
- [ ] Liveness proven to detect failure via synthetic or historical failure rows
- [ ] `/api/status/full` auth-gated; returns full payload well inside timeout
- [ ] Status page shows exceptions first; healthy state collapsed; `unknown`
      visually distinct from `ok`
- [ ] Poll failure visibly marks the page stale
- [ ] `self_whoami` and the page return the same state for the same components
- [ ] No secrets in any payload — the status endpoint is not a secret surface
- [ ] All PRs through the CI gate before merge

---

## 5. Queued, explicitly not in this build

- R7 — morning brief health section (exception-only)
- R19 — push alerting, deferred until false-positive rate is known
- Test results in Postgres (`design-note-test-architecture`)
- R17 — multi-repo provenance, parked
