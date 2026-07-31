# Session Close-out — 2026-07-31 (Afternoon: Capability Rollup & Health-Topology Audit)

**Focus:** Shipped the capability status rollup, and in seeding it, surfaced and
closed a series of health-model gaps that were larger than the feature.

**Companion to:** `SESSION-closeout-2026-07-31.md` (morning: the location pull
loop closed after four nested faults). This is the same day's second arc.

**Resume point:** new chat stream for the Project Management arc (see
`PREWORK-project-management-arc.md`). `/clear` for the Builder once PR #50 and the
audit-starvation fix are merged and pushed.

---

## 1. What shipped

| PR | Contents | State |
|---|---|---|
| **#47** | `location_log_nonce` widened to all three cases (empty/unresolved/unmatched), default off | Merged |
| **#48** | (base for #47) | Merged |
| **#49** | Capability rollup: `capability` + `capability_member` tables, evaluator, `GET /api/status/capabilities`, brief line, `capability_rollup` meta-check, F1 `health_evaluator`, F2 `google_oauth` liveness | Merged, deployed `72f2be9` |
| **#50** | Runbook-gap enforcement: 8 `call_failed` runbooks added, 6 dead runbooks retired via `_RETIRED_REMEDIATIONS`, two enforcement guards | Green, **merge pending** |

Plus one owner-visible outcome: the capability rollup is live and reading current
truth — **6 of 8 green**, Location red (climbing), Project tracking amber (ceiling
by design), Flight booking + Local network gated.

---

## 2. The capability rollup — and why its real value wasn't the feature

The rollup does what was asked: a daily one-line status over what JARVIS can
actually do, judged against production reality (`live` capabilities only; gated
and planned are invisible, not red). Six capabilities shipped; two (Self-health,
Contacts) were held out until instrumentation existed to make them honest, then
earned back via F1/F2.

But the feature was the smaller half. **Seeding it forced an audit of the health
topology against the live 28-row component table, and the audit is what paid off:**

### 2.1 Three instrumentation gaps (found at seed time)

- **Self-health had no component for itself.** "Self-health: ok" meant "the things
  health checks depend on are ok" — not "health checking is actually running." A
  check that silently stopped evaluating would have read green. The exact
  fabricated-`ok` failure of the pre-epoch `actions_audit`, one level up.
  **Fixed (F1):** `health_evaluator` component + heartbeat; `status_payload`
  deliberately does **not** stamp, so viewing the page cannot prove the evaluator
  alive by the act of asking. Verified end-to-end in production — the evaluator's
  own cycle recomputed a flipped verdict 37s after the triggering event, nobody
  touching the page.
- **Contacts could never be green.** `google_oauth` used `published_expiry`, which
  was never built (Google refresh tokens publish no expiry) → permanent `unknown`.
  **Fixed (F2):** moved to audit-derived liveness; verified against 7 post-epoch
  prod rows before claiming it unblocked.
- **Memory has no vectorstore component** — semantic-recall failure is invisible.
  Accepted as a known blind spot for v1, noted in the seed; follow-up component.

### 2.2 The latched-check class (found at merge time — the biggest one)

`google_calendar_svcacct` read `down` for a **credential that works**. The live
OAuth call succeeded; the `down` was pure monitoring artifact.

Mechanism: `check_liveness` is audit-derived — it reads `last_success` vs
`last_failure` from `actions_audit`. But the one thing that exercises Calendar
daily (the 4 AM brief) calls `_calendar_lookup` **directly, bypassing
`Registry.run_tool`, so it writes no audit row.** The path that proves Calendar
alive every morning is invisible to the check that judges it. A fault that
resolved days ago **latches red forever**, because only an audited success clears
it and nothing routine is audited.

This is the sixth instance of the recurring **proxy-signal** family (relay status
vs body, quiet-hours on kind vs provenance, "destructive" on notional value,
runbook keyed to codes nothing emits, and now liveness reading a starved audit
substrate). Named so it is findable a seventh time.

**Owner instinct that caught it:** "the fact that the status was stale is a red
flag." A latched-red check undercuts "truthful red on debut" — the red is a
fossil, not a fact, and shipping it would have been the cry-wolf failure the whole
design exists to prevent.

**Cleared** with one registry-routed `calendar_lookup`. **Real fix queued** (see
§4): route the brief's direct call through the registry so the daily traffic feeds
the substrate — passive check, complete substrate, no synthetic prober.

### 2.3 The runbook join was quietly empty for 8 components

The status page's runbook column has been silently blank whenever certain faults
fired. `check_liveness` emits `call_failed`; the calendar/oauth runbooks were
keyed to `auth_invalid`/`token_expired` — codes no check produces. Your four-day
calendar outage rendered with **no runbook at all** the whole time.

Audit (PR #50) found **8 components** that can emit `call_failed` with no runbook,
and **6 dead runbooks** keyed to codes nothing emits (`401`×2, `a2p_rejected`,
`auth_invalid`, `token_expired`, `token_missing_scope`). Both fixed; two
enforcement guards added (every emittable (component, code) has a runbook; no
runbook keyed to a dead code), and **both guards were deliberately broken to
confirm they fire** — an enforcement test that cannot fail is worse than none.

---

## 3. Lessons (closeout-grade, several generalize past this codebase)

**A test can encode a bug as a guarantee, and then the bug has a defender.**
`test_missing_runbook_degrades_gracefully` asserted `remediation is None` for a
failing Duffel and called it graceful — a docstring dressing a bug as design.
Every future fix would have tripped this test and been reverted "because it broke
something." **A green test asserts behavior; it does not prove correctness, and a
bug wrapped in a passing test is load-bearing.** This is the single most
transferable finding of the day and a KEEL-curriculum candidate.

**`check_type` is not a reliable index of what a component does.** `postgres`
declares `liveness` but `_APP_UP` overrides it to `check_app_up`; it can never
emit `call_failed`, and its real failure mode is the check *raising*
(`check_error`). Any audit reading the declared type alone gets it wrong — the
first pass did. Declaration ≠ routing; verify behavior programmatically.

**A passive audit-derived check is only as truthful as its audit substrate is
complete.** The fix for a latch is to route *real* traffic through the audit path,
never to manufacture synthetic prober traffic — a check reading its own synthetic
traffic can't tell "the calendar works" from "my prober works," which is the
proxy-signal failure one level up. Any tool call that exercises a component while
bypassing `Registry.run_tool` is a latent latch of the same shape.

**Retire, don't delete, seeded rows.** Dead runbooks went through
`_RETIRED_REMEDIATIONS`, not list-deletion, because reconcile never removes rows
on its own (the `seed_agents()` stale-registration lesson). Their guidance was
folded into the `call_failed` runbooks, not discarded.

**Break every guard once to confirm it fires.** Same discipline as the
bracket-Flash test that cracked the nonce hunt this morning: an instrument you
have not watched fail is one you do not yet trust.

---

## 4. Queued for the Builder (not yet done)

1. **Merge PR #50** (runbook-gap enforcement) — green, self-contained.
2. **Audit-starvation fix** — route `briefing.py:295`'s direct `_calendar_lookup`
   through `Registry.run_tool` so the daily brief feeds the audit substrate it
   currently bypasses. **Plus a sweep for other direct-call bypasses** that could
   latch the same way — this sweep is the high-value part, since any unaudited
   tool call is a latent latch, and the project arc is about to add tool calls.
   The design fork ("passive vs active probe") was resolved toward
   passive-plus-complete-substrate; this is a routing fix, not a new TDD.

---

## 5. Owner threads still open (unchanged, none blocking)

- **Calendar OAuth** — credential works; **no re-mint needed** (that premise was
  stale). The latch is cleared. If it re-latches before the §4 starvation fix
  lands, one registry-routed calendar read clears it again.
- **Phone export** — `devices/jarvis-location-pull.prj.xml`, scrubbed. Owed across
  three closeouts. The instrument that makes the phone-side config reviewable;
  every hour of this morning's nonce hunt was reasoning about a file no one could
  read.
- **`LOCATION_TOKEN`** — rotated 2026-07-31. Closed.

## 6. Verification signal to leave alone

`location_responsiveness` is the only red left and is climbing on its own toward
5-of-6 fulfilled since the 15:30 fix. It reading red now is correct and it will
green itself. Do not touch it — that self-clearing is the §7 proof the attribution
mechanism works end to end.

---

## 7. State of the board at session pause

- Location pull loop: **working end to end** (first `fulfilled` at 15:30:28).
- Capability rollup: **live, 6/8 green**, reading current truth, self-evaluating.
- Health topology: **three gaps closed, one latch class identified and cleared,
  runbook join repaired and enforced.**
- Project arc (5 TDDs): **drafted, TDD #1 merged and live**; the rest queued as
  pre-work.
