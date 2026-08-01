# Session Close-out — 2026-07-31 (Afternoon: Capability Rollup & Health-Topology Audit)

**Focus:** Shipped the capability status rollup, and in seeding it, surfaced and
closed a series of health-model gaps that were larger than the feature.

**Companion to:** `SESSION-closeout-2026-07-31.md` (morning: the location pull
loop closed after four nested faults). This is the same day's second arc.

**Resume point:** new chat stream for the Project Management arc (see
`PREWORK-project-management-arc.md`). `/clear` for the Builder once PR #51 is
merged (#50 is already in).

---

## 1. What shipped

| PR | Contents | State |
|---|---|---|
| **#47** | `location_log_nonce` gated to `unmatched` only, default off; + the morning close-out and `backfill_projects.py` | Merged |
| **#48** | `location_log_nonce` **widened** to all three cases (empty/unresolved/unmatched), still default off | Merged |
| **#49** | Capability rollup: `capability` + `capability_member` tables, evaluator, `GET /api/status/capabilities`, brief line, `capability_rollup` meta-check, F1 `health_evaluator`, F2 `google_oauth` liveness | Merged, deployed `72f2be9` |
| **#50** | Runbook-gap enforcement: 8 `call_failed` runbooks added, 6 dead runbooks retired via `_RETIRED_REMEDIATIONS`, two enforcement guards | Merged |
| **#51** | Audit-starvation fix: 12 bypass sites routed through `Registry.run_tool`, `record_tool_audit`, + a bypass guard | Green |

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
- **`anthropic_api` and `nws` have no audited tool at all** (found by the PR #51
  sweep, and the one routing cannot fix). Every LLM call goes through
  `app/llm.py`; every forecast through `_nws_weather`. Neither is a **registered
  tool**, so no `actions_audit` row can ever map to them and `component_for_tool`
  returns nothing. Their liveness checks are therefore structurally incapable of a
  verdict: **never green, never able to detect a fault** — the same class as the
  `published_expiry` gap F2 closed.

  **More serious than the vectorstore blind spot, because `anthropic_api` is
  TRUNK**: `blast_radius=multi`, a member of the Memory capability, and the one
  component whose failure takes down many limbs at once. It cannot currently be
  seen failing, and a Memory member sits permanently `unknown`.

  **Carried as a decision, not a snap call** (see the pre-work). The fork is real:
  give them a *legitimately synthetic* liveness probe, or accept them as
  permanently unknown and exclude them from green-eligibility with that stated as
  design. A synthetic probe is defensible **here** in a way it was not for the
  calendar — the calendar had real daily traffic that merely needed routing, so a
  prober would have been reading its own noise; the LLM has no registerable tool
  path at all, so there is no real traffic to route and a health-ping is the only
  honest signal available.

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

1. ~~**Merge PR #50**~~ — **DONE**, merged (`e45aa91`).
2. ~~**Audit-starvation fix**~~ — **DONE**, PR #51, green.

   The sweep was the high-value part and it earned that weighting: **12 bypass
   sites, 5 on liveness components.** The calendar latch was not a bug, it was an
   *instance* — `list_trips` → `duffel` and `get_traffic` → `google_maps` were
   latent latches of the identical shape, waiting to read red on a resolved fault.
   And `routes.py`'s own **calendar diagnostic** was itself unaudited: a probe that
   exercised the calendar without recording it, keeping the very check it was built
   to debug exactly as starved as before you looked.

   `infra` and `tailscale` were converted too despite having no liveness check.
   Uniformity is what makes the sweep durable rather than a one-time cleanup — in a
   codebase where some tool calls route through the registry and some don't, the
   next bypass looks normal.

   **Two invariants were nearly traded away, and both would have shipped looking
   like clean refactors:**
   - *Thread-safety.* The brief's workers share one Session and the `gather_context`
     docstring pins "HTTP-bound handlers do not exercise ctx.db". Writing audit rows
     inside the threads buys observability with thread-safety — the kind of
     constraint that stays invisible until it corrupts something under load.
     Outcomes are collected; rows are written on the main thread after the join.
   - *The brief is read aloud.* Section guards key off `_safe`'s `"("` prefix.
     `run_tool`'s raw error string lacks it, so a naive swap would have narrated an
     error dump into a spoken brief — **the PR #44 defect reintroduced inside the
     same diff as the audit-routing change**, where it would have passed review as
     a pure refactor.

   **Guard added and negative-validated:** a test walks non-handler app code for
   direct calls to any liveness-backed tool and fails with `file:line`. Planted a
   bypass, watched it fail; removed it, watched it pass. Third instrument
   deliberately broken today.

---

## 5. Owner threads still open (unchanged, none blocking)

- **Calendar OAuth** — credential works; **no re-mint needed** (that premise was
  stale). The latch is cleared, and with #51 merged the daily brief now feeds the
  substrate, so it cannot re-latch the same way.
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
- Health topology: **four instrumentation gaps found — three closed (self-health,
  contacts, runbook join), one owed a decision (`anthropic_api` / `nws`, trunk) —
  plus a latch CLASS swept (12 sites, not one bug) and the runbook join enforced.**
- Project arc (5 TDDs): **drafted, TDD #1 merged and live**; the rest queued as
  pre-work.
