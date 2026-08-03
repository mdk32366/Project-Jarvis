# TDD — Location Ping Freshness & Absence Alert

**Status:** Draft, ready to build (written 2026-08-01 from the live diagnosis of
the 6-hour silent gap; grounded against the shipped health checks, not memory.
**Revised 2026-08-01** after the pull-silence diagnostic ran and returned a fault
mode the first draft didn't model — the phone *answering late* rather than not
answering. Added the `answering_late` fault code, its distinct power-management
runbook, a fourth attribution row, the fulfilment+latency signal (§4.5), and the
late-answer ingest precondition. See §4.3, §4.5, §5, §10, §11.)
**Depends on:** location pull inversion (`TDD-location-pull-inversion.md`, live),
the health substrate (`check_location_scheduler`, `check_location_responsiveness`),
the watches machinery (`Watch` model, `check_watch`/`due_watches`), morning brief

---

## 1. Problem

A location fix went **six hours stale in production and nothing said a word.**
The owner discovered it by looking, not by being told. The outage itself is
secondary; **the silence is the defect.** JARVIS has two location health checks
and neither fired.

This is the same class as the latch failures and the fabricated-green audit rows:
a monitor that is structurally incapable of detecting the fault it exists for
looks identical to a healthy system until the moment you need it.

### 1.1 Why the existing checks were blind — read from the live code, not inferred

Both shipped checks watch the **request** side of the loop, and both have an
escape hatch this outage fell through:

- **`check_location_scheduler`** returns `ok` when `not in_active_hours` —
  literally `"outside active hours; last pull Nm ago"`. If the pull loop stops and
  the current time is outside the active window, this reads green no matter how
  stale the data is.
- **`check_location_responsiveness`** returns `unknown` ("no_evidence") when fewer
  than `degraded_min` completed requests sit in its trailing window. When the
  scheduler stops minting requests, completed requests stop accumulating, the
  window starves, and the check degrades to `unknown` — which, correctly, never
  maps to a fault. Correct rule, wrong consequence here: the check disarms itself
  exactly when the scheduler dies.

**The gap, stated once:** *nothing watches the freshness of the newest actual
`LocationPing`.* Both checks reason about requests (the asking); neither reasons
about the fix on hand (the answer). "How old is the newest position I actually
have" is the one question that would have caught this, and no check asks it.

## 2. Goals

1. **Close the blindness:** a check whose sole job is the age of the newest
   `LocationPing`, which cannot be silenced by active-hours or by request-window
   starvation. If the freshest fix is older than a threshold during hours the
   owner expects movement, that is a fault, surfaced.
2. **Proactive absence alert:** a permanent watch that rings the owner when no fix
   has registered for a configurable interval (default 30 min) during active
   hours — the dead-man's-switch the owner asked for.
3. **Layer-attributed, never root-caused:** when the alert fires, it names *which
   layer* failed (drawn from the request record), never a guessed within-layer
   cause.

## 3. Non-goals

- **Diagnosing the current outage.** That is the tabled diagnostic build order,
  not this TDD. This builds the machinery that makes the *next* one loud.
- **A richer movement/history log** for secondary uses (where-was-I-Tuesday,
  geofences). Real want, explicitly deferred — §9. Folding it in is how this build
  slips.
- **Root-cause announcement.** §5.3. JARVIS reports the layer; the owner (with the
  recovery doc) finds the cause within it.
- **Changing the pull loop, the phone, or any setting.** This is detection, not
  remediation.

---

## 4. Design

### 4.1 The freshness check — the structural fix

A new health check, `check_location_freshness`, reasoning about **`LocationPing`,
not `LocationRequest`**:

- Read `latest()` — the newest ping (this helper already exists in
  `handlers/location.py`).
- Compute `age_minutes(latest)` (also already exists).
- **`unknown`** only if there has never been a ping at all — a genuine no-evidence
  state, not a starved one.
- Otherwise judge age against `location_stale_after_minutes` (new setting, default
  30), three-tier consistent with the other checks: fresh → `ok`, late →
  `degraded`, well past → `down`.

**The active-hours interaction is the whole subtlety, and it is deliberately NOT
the scheduler check's version.** The scheduler check goes *green* outside active
hours. This one must not — a stale fix is still stale at 2am. But firing a *fault*
at 2am, when the owner is asleep and not moving and no pull is even scheduled,
is noise. The resolution:

- Outside active hours: the check still reports the true age (never green-washed),
  but caps its severity at `degraded` — the staleness is real and visible on the
  status page, but does not escalate to `down` and does not drive a 2am alert.
- Inside active hours: full three-tier. A 30-minute-stale fix during the commute
  is a `down`, and that is what the watch (§4.2) rings on.

This is the honest middle between the scheduler check's over-forgiveness (green
when blind) and a naive check's over-eagerness (down at 2am). Staleness is always
*reported*; it only *escalates* when the owner is meant to be moving.

### 4.2 The absence watch — the dead-man's-switch

A **permanent** `Watch` row, distinct from user-created watches: fires on
*absence*, not presence.

- `tool` = `check_location_freshness` (or a thin wrapper exposing it as a
  watchable tool), `condition` = plain English ("no location fix has registered
  in over 30 minutes during active hours"), judged the same LLM-against-prose way
  every watch is.
- `every_minutes` default 15 (checks twice within the 30-min window).
- `recurring=false` semantics adapted: it should tell you *once* when the ping
  goes silent, not every 15 minutes thereafter — a watch that nags is one you
  disable, and a disabled location-alarm is how you end up six hours stale again.
  Re-arms when a fresh ping restores health, so the *next* outage still alerts.
- `created_by` marks it system-owned so it is not casually deleted with a "stop
  watching X."

**Why a watch and not only a health check:** the health check makes the status
page and brief honest (passive). The watch makes JARVIS *ring you within 30
minutes* (proactive). The owner asked for the proactive alert by name; the health
fix is the structural prerequisite that makes the watch's judgment trustworthy.
Both, layered — the check is what the watch reads.

### 4.3 Layer attribution — fact, never root cause

When the watch fires, the alert names the layer, read from `LocationRequest`
state (the split already proven in the diagnostic order and the two existing
checks):

| Observed in `location_request` | Layer named in the alert |
|---|---|
| No `scheduled` rows recently | "JARVIS isn't sending location requests" (scheduler/setting) |
| Recent rows, `relay_accepted=false` | "requests are going out but the relay is rejecting them" (dispatch) |
| Recent rows, `relay_accepted=true`, none answered even late | "requests are being accepted but the phone isn't answering" (phone — silent) |
| Recent rows answered, but `responded_at` lands *after* the timeout — late answers, collapsed fulfilment | "your phone is answering location requests late — fixes arrive but too stale to use" (phone — late) |

**The last two rows are different faults and must not be collapsed.** This is the
finding from the 2026-08-01 live diagnosis: the phone was answering ~50 minutes
late against a 120-second timeout, every request swept to `timeout` before its
answer landed, fulfilment at 26%, latency bimodal (a fast cluster and a slow
cluster). That is **not** silence — dispatch works, FCM delivers, Tasker responds.
It is *answering outside the window we are willing to wait*. (Bimodal latency is
consistent with Android doze, but the phone was answering on-cadence the previous
day, so doze is the leading hypothesis for the alert to point *toward*, not a
cause for it to assert — see §11.) Silence and lateness point at different
phone-side causes and therefore route different runbooks (§5): `not_answering` →
Tasker config (profile, filter, permission); `answering_late` → power management
(doze, battery optimization, exact-alarm scheduling). A phone that is
answering-but-late sent down the `not_answering` runbook wastes the diagnosis
re-checking a Tasker config that is already correct — the "chasing ghosts"
failure, one layer deeper.

**Why not just raise the timeout.** Because a 50-minute-old fix *is* useless for
"where am I now" — that is the entire staleness premise the location module was
built on. Widening the window to catch the late answers converts a check that is
*correctly red* into a green while the data stays 55 minutes stale. That is
tuning the instrument to stop reporting the fault instead of fixing the fault —
the fabricated-green class, in latency form. The timeout is measuring something
true; keep it, and name the lateness as its own fault.

**The alert states the layer and stops.** "The phone isn't answering" and "the
phone is answering late" are both observable and true. "Your Tasker profile is
disabled" or "turn off battery optimization" is a guess wearing a fact's clothes
— the cause within the phone layer might be doze, battery-opt, exact-alarm
permission, the filter, or a tunnel, and announcing one is the netstatus-stub
defect in alert form. The owner has `docs/tasker-setup-and-recovery.md` and the
`answering_late` runbook; the alert's job is to point at the right layer — and
now the right *sub-layer* (silent vs. late) — not to pretend certainty about the
cause within it.

### 4.4 Reuse, don't rebuild

- **No new log table.** `LocationPing` is already durable (last
  `location_keep_pings` rows) and `LocationRequest` already records every ask with
  status + `relay_accepted`. The freshness check and layer attribution read
  existing state. **Open question (§10):** is `location_keep_pings` retention deep
  enough for the freshness check's needs (it is — the check only needs the newest
  row), and separately for the deferred history feature (probably not — but that
  is §9, not now).
- The watch rides the existing `due_watches`/`check_watch` machinery on the worker
  tick — the same loop the pull already rides. No second scheduler.

### 4.5 The latency signal — what distinguishes late from silent

The freshness check (§4.1) reads *newest-ping-age* and catches staleness. But
staleness alone cannot tell `not_answering` from `answering_late` — a phone
answering 50 minutes late and a phone not answering at all both produce a
permanently-stale newest ping. **The distinguisher is in `LocationRequest`, not
`LocationPing`:** the attribution helper must read the trailing window's
fulfilment rate *and* the response latency of any answered rows, including late
answers whose `responded_at` lands after the row was swept to `timeout`.

- **Silent** (`not_answering`): recent accepted requests, none answered at all —
  no `responded_at`, ever, in the window.
- **Late** (`answering_late`): recent accepted requests answered, but
  `responded_at − requested_at` exceeds the timeout for a meaningful fraction —
  the answers exist, they arrive too late, fulfilment collapses.

This means the diagnosis depends on the system **recording late answers rather
than discarding them.** A late POST to `/api/location` that arrives after its
request timed out must still be persisted and correlated (by nonce) to its
originating request, marked as a late arrival — because a late answer thrown away
is the evidence that would have distinguished doze from silence, thrown away.
**Open question (§11):** confirm whether the ingest path currently drops or keeps
a ping whose request already swept to `timeout`. If it drops it, that is a small
ingest change this TDD depends on, and it is the difference between a diagnosable
late-answer fault and an indistinguishable-from-silence one.

---

## 5. Data model

Minimal — reuse over addition.

- **New setting** `location_stale_after_minutes` (default 30) via the runtime
  overlay, tunable without redeploy.
- **New component row** `location_freshness` in the health `Component` table, with
  its check_config (thresholds) and a **runbook per fault code** it can emit —
  the runbook-join guard requires every fault code ship a runbook and no orphan
  runbooks, so this must land complete. The fault codes and their **distinct**
  runbooks:
  - `stale_during_active` — newest fix too old inside active hours. Runbook: run
    the tabled pull-silence diagnostic (which layer stopped).
  - `never_pinged` — no ping has ever been recorded. Runbook: first-run / phone
    never enrolled.
  - `not_answering` — accepted requests, no answers at all. Runbook: **Tasker
    config** — profile enabled, filter regex, AutoRemote receiving.
  - `answering_late` — accepted requests answered but past the timeout. Runbook:
    **power management** — Android doze exemption, battery-optimization exemption,
    exact-alarm permission for Tasker. **Explicitly NOT the Tasker-config
    runbook** — the config is correct or nothing would answer at all; sending a
    late-answer fault to the config runbook is the mis-route §4.3 warns against.
  The two phone-side runbooks pointing at different settings is the entire reason
  the fault codes are split. A single `phone` fault with one runbook would send
  every phone problem to the same checklist, half of which is wrong for lateness.
- **One permanent `Watch` row**, system-created (seeded, idempotently — creating
  it twice on redeploy is a bug; guard on a stable identifier).

Likely **no migration** — settings, components, and watches are existing tables.
Confirm at build: if the permanent watch needs a marker column to distinguish
system-owned from user-owned, that is a small `add_column` on `watches`
(`created_by` already exists and may suffice — read it first).

---

## 6. Brief integration

The freshness check feeds the brief's exception-first systems section like every
other component: **silent when fresh, a line when stale.** The line reports the
age as fact — "location fix 41 minutes old" — never a judgment. Outside active
hours it may appear as a `degraded` note but never as an alarm. Same discipline
as every other brief line: the fact, the owner draws the conclusion.

---

## 7. Tools

| Tool | Gated | Notes |
|---|---|---|
| `check_location_freshness` | no | Read-only; age of newest ping. Watchable + health-check-backing. Registry-routed like all tools (audit discipline). |
| `location_ping_log(n=20)` | no | Read the recent ping history — the log the owner asked to be able to look at. Read-only over existing `LocationPing`. |

`location_ping_log` is the small, honest version of "JARVIS can look at a log of
past pings": it reads what already exists. The *richer* log (long retention,
movement analysis) is §9.

---

## 8. Build order

| # | Work | Testable |
|---|---|---|
| 1 | `check_location_freshness` + `location_stale_after_minutes` setting | ✅ |
| 2 | `location_freshness` component + runbooks (all four fault codes, distinct) | ✅ |
| 3 | Active-hours severity cap (report always, escalate only in-hours) | ✅ |
| 4 | Layer attribution helper — reads `LocationRequest` fulfilment + latency; distinguishes silent / late / dispatch / scheduler; names layer, no root cause | ✅ |
| 4a | Ingest: persist + correlate a late-arriving ping whose request already timed out (§4.5) — precondition for distinguishing `answering_late` from `not_answering` | ✅ |
| 5 | Permanent absence watch, seeded idempotently, fire-once-then-rearm | ✅ |
| 6 | `location_ping_log` tool | ✅ |
| 7 | Brief integration (exception-first, fact-only) | ✅ |

Step 1 (the freshness check) is the structural fix and ships value alone — it
closes the blindness even before the watch exists. Build it first; the watch (5)
reads it.

---

## 9. Deferred — recorded so it isn't re-specced

- **Richer movement/history log.** Longer retention, "where was I Tuesday,"
  geofence-style triggers. Real want, genuinely separate feature — different
  retention, different table likely, different privacy surface. The primary use
  (failure alert) needs none of it. Build when a concrete second use arrives.
- **Root-cause diagnosis within a layer.** Permanently out of scope by §5.3 — not
  deferred, refused. The layer is the honest ceiling.

---

## 10. Test plan

- **Freshness catches what the others missed** — the exact regression: newest
  `LocationPing` 6h old, current time *outside* active hours → `check_location_
  scheduler` may read `ok`, but `check_location_freshness` reports the true age
  and is not green. The scenario that started this, as a named test.
- **Stale during active hours is `down`** — newest ping older than
  `location_stale_after_minutes` inside active hours → `down`, fault
  `stale_during_active`.
- **Stale outside active hours is capped at `degraded`** — same age, outside the
  window → `degraded`, not `down`, no alert-driving escalation, age still reported.
- **Never-pinged is `unknown`, not `down`** — no ping ever → `unknown`
  `never_pinged`, no fabricated fault on a fresh system.
- **Watch fires within the window** — no fix for > 30 min during active hours →
  the watch fires once.
- **Watch does not nag** — after firing, it does not fire again every 15 min while
  the condition persists; it re-arms only after a fresh ping.
- **Alert names the layer, not the cause** — assert the fired alert text contains
  the layer phrase (per §4.3 table) and contains **no** within-layer root-cause
  claim (no "Tasker", no "profile", no "permission", no "doze", no "battery").
  Guards §5.3 in test — and note the late-answer alert must not name doze either,
  even though doze is what the diagnosis found; the alert says "answering late,"
  the runbook says doze.
- **Layer attribution is correct per request state** — four cases: no scheduled
  rows → scheduler phrasing; `relay_accepted=false` → dispatch phrasing;
  `relay_accepted=true` with no `responded_at` anywhere in the window → phone-
  silent phrasing; `relay_accepted=true` with answers whose latency exceeds the
  timeout → phone-late phrasing.
- **Late is not silent** — the 2026-08-01 scenario, as a named test: a window of
  accepted requests, most swept to `timeout`, but with late `responded_at` values
  (~50 min) present → attribution returns `answering_late`, **not**
  `not_answering`. The two must not collapse.
- **`answering_late` routes the power-management runbook, not the config one** —
  assert the runbook returned for `answering_late` is the doze/battery/exact-alarm
  runbook and specifically is **not** the Tasker-config runbook. This is the
  mis-route §4.3 exists to prevent, asserted.
- **A late answer is persisted, not dropped** (§4.5, step 4a) — a ping POST whose
  request already swept to `timeout` is still recorded and correlated by nonce,
  marked late. Without this the two phone faults are indistinguishable; assert the
  row survives.
- **Raising the timeout is not the fix** — a design-guard test, if expressible:
  assert that `answering_late` fires on late-but-present answers rather than the
  check being satisfiable by widening the timeout window. At minimum, a comment in
  the test naming why the timeout is held fixed (the fabricated-green argument,
  §4.3).
- **Permanent watch is seeded idempotently** — running the seed twice (redeploy)
  yields one watch, not two.
- **`location_ping_log` reads existing rows** — returns the recent pings, no new
  table, read-only.
- **Runbook join** — every fault code the freshness check emits has a runbook; no
  orphan runbook. (The existing guard; this must pass to build.)

---

## 11. Open questions

- **Is `location_keep_pings` retention adequate?** For the freshness check, yes —
  it needs only the newest row. For `location_ping_log` at `n=20`, confirm the
  retention default is ≥ 20 or the log silently truncates. Cheap to bump; confirm
  the live value at build.
- **Fire-once-then-rearm semantics.** The `Watch` model has `recurring` (false =
  tell once and stop). "Tell once, then re-arm when health returns" is a *third*
  behavior — neither nag-forever nor tell-once-permanently. Whether that needs a
  new field or can be expressed by resetting the watch on recovery is a build
  decision; the requirement (one alert per outage, but the *next* outage still
  alerts) is pinned in §10.
- **Does the freshness watch belong to the owner or the system?** Seeded as
  system-owned (`created_by`), so "stop watching" doesn't silently disarm the
  location alarm. Confirm `created_by` is checked by the delete path, or the guard
  is decoration.
- **Does the ingest path keep or drop a late answer?** (§4.5, step 4a.) The whole
  ability to distinguish `answering_late` from `not_answering` depends on a ping
  that arrives after its request timed out being persisted and correlated by
  nonce, not discarded as unmatched. This is a live-code question — it goes to the
  Builder to read `/api/location` + `record_ping` and report whether a
  post-timeout ping is currently kept, dropped, or kept-but-uncorrelated. If it's
  dropped, step 4a is a real (small) ingest change, not just a read.
- **Interaction with the outage — diagnosed, but the cause is not settled.** The
  2026-08-01 diagnostic *ran* (departing from tabled) and returned `answering_late`
  as the observed fault: phone answering ~50 min late, 120 s timeout, 26%
  fulfilment, bimodal latency. Bimodal latency is *consistent with* Android doze —
  **but the phone was verified answering cleanly every 15 minutes the day before**,
  by all parties. A doze/battery-optimization exemption that was in place and
  working does not spontaneously lapse, so "re-apply the doze exemption" assumes a
  regression the working-yesterday evidence makes less likely, not more. The fault
  *code* is solid — the phone is observably answering late. The *cause within the
  phone layer* is exactly what §5.3 says the alert must not assert: doze is the
  leading hypothesis, not a finding. **Confirm what changed between yesterday's
  on-cadence pings and today's bimodal latency** rather than assuming the exemption
  fell off — a phone/OS update, a battery-saver mode toggle, a Tasker update, or an
  actual doze-exemption reset are all live candidates. This is phone-side owner
  investigation, independent of this TDD and of the arc. When the detector ships it
  should classify the live outage as `answering_late` and route the
  power-management runbook — which points the *investigation* at the right layer
  without pretending to name the cause. If the owner resolves it before this ships,
  keep a synthetic `answering_late` fixture as the regression.


---

## AMENDMENT — Step 7's component line is unreachable, and redundant (2026-08-03)

Settled by a live read after PR #71. **No code changes; recorded so it is not
re-derived or built by someone reading step 7 at face value.**

### Unreachable by construction

Effective values, read from the runtime overlay rather than the config defaults:

| Setting | Effective |
|---|---|
| `briefing_hour` / `briefing_minute` | **04:00** |
| `location_active_start_hour` | **07:00** |
| `calendar_timezone` | `America/Los_Angeles` |

The brief composes **three hours before active hours open** — wider than the
30-minute gap the defaults imply, because of the 4 AM call setup.
`check_location_freshness` returns `ok` outside active hours by design, so at the
moment the brief is composed, freshness is **always** `ok`. Step 7's per-component
line can never render. Not a bug in step 7 — a consequence of when the brief runs.

### Redundant anyway

The capability rollup already carries the signal, and carried it correctly through
the 08-03 outage: **"Location amber"** fired off `location_responsiveness`, which
is **not** hour-suppressed and caught the silences the freshness check was quiet
about. The working line was already there before step 7 was specified.

That is the non-primary decision (#70) paying off in the direction it was chosen
for: responsiveness leads, freshness lags, and the leading indicator is what
reaches the brief.

### The component-detail path is deferred, not needed

The brief has **no per-component detail path at all** — `brief_line` is capability
granularity by design. Surfacing "location fix 41 minutes old" would mean building
one, for a single consumer.

**Trigger for building it, named so it is not re-derived:** a **second** component
wanting per-component detail in the brief. Same discipline as `rearm_on_clear` —
generalising to a mechanism before a second case exists is speculative, and the
first case is already served by the capability line.
