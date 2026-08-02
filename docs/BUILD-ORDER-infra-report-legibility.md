# BUILD ORDER — Infra report legibility (Fly balance + fleet org scope)

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Status:** **QUEUED — do not start until TDD #3 step 7 is merged.** This is behind
the arc. It touches `handlers/infra.py` only; it does not touch any arc file.
**Type:** Code PR, two independent changes in one handler. Change A is
merge-on-green. Change B is diagnosis-first (a read), then a small fix whose shape
depends on what the read finds.
**Origin:** 2026-08-01. Two observations about the Fly fleet report: (1) the
`$0.00` credit balance surfaces in the morning brief daily despite being a normal
autopay state, and (2) a live app (`pharmfoldmdk.fly.dev`) is absent from the
fleet report, which enumerates org "Matt Kelly".

---

## Both are the same lesson, pointed at the infra report

A health surface should say **what it is measuring and under what assumptions**,
so a *normal* state does not read as alarming and a *real* gap does not read as
clean. The balance warning miscategorizes normal-as-noteworthy; the fleet report
silently omits an app it can't see. Same defect family as the location checks that
went blind and the fabricated-green audit rows — the instrument gives a confident
reading and stays quiet about its own boundaries.

---

## Change A — Fly balance is only a signal under a prepaid model

### The problem

`_fleet_spend` (`handlers/infra.py`) reports `creditBalanceFormatted` (currently
`$0.00`) and this feeds the morning brief daily (`briefing.py` ~line 360). Under
the owner's **autopay** tenancy — card billed automatically, no prepaid buffer —
`$0.00` is the *normal resting state* between charges, not a fault. Surfacing it
daily is noise, and noise in a health surface trains the reader to skim the panel,
which is the exact failure exception-first design exists to prevent.

The number is only a genuine signal under a **prepaid drawdown** model (the
PharmFoldMDK GPU-rental pattern: front cash, work through it, zero = service
stops). Same number, opposite meaning, entirely dependent on the billing model
behind it.

### The fix — a threshold setting, not a hardcoded suppression

**Do not** delete the balance line or hardcode-suppress the `$0.00` case. That is
brittle: if the owner ever runs a prepaid project under JARVIS's watch again, the
suppression silently hides a real cutoff warning.

Instead, make the billing model a **stated parameter** (deterministic state in the
settings overlay, not a code constant — standing doctrine):

- New setting `fly_balance_alert_threshold`, default **`None`** (the autopay case).
- **When `None`:** the balance is **not** surfaced as a warning/exception. Either
  omit it from the brief's exception-first surface entirely, or render it as plain
  informational context that never escalates. It is never a fault.
- **When set to a dollar figure** (the prepaid case): a balance at or below the
  threshold *is* a real fault worth surfacing — "Fly credit $12.40, below your
  $50 floor" — because under a drawdown model that is an approaching cutoff.

The run-rate estimate line is unaffected — it's useful context either way and
already carries its own honest "rough estimate, no stable API" caveat. This change
is only about whether the *balance* escalates.

### Tests

- **Autopay default surfaces no balance fault** — with `fly_balance_alert_threshold`
  unset (`None`), a `$0.00` balance produces no exception line in the brief and no
  fault. Assert the brief's systems/exception section is silent on balance.
- **Prepaid threshold fires** — threshold set to `$50`, balance `$12` → a fault
  line naming the balance and the floor.
- **Prepaid healthy is quiet** — threshold `$50`, balance `$300` → no fault.
- **Setting is runtime-tunable** — read via the overlay (`get_effective`), no
  redeploy to change the model.

---

## Change B — the fleet report doesn't say what org it's scoped to

### The problem (diagnosis-first — this half is a read before a fix)

`_fleet_spend` queries `personalOrganization` and `_fleet_health` lists that org's
apps. The report showed four apps under org "Matt Kelly" (`jarvis-mdk`,
`jarvis-db2`, `ffis-scrubber`, `sentinel-holy-rain-4562`) — but **`pharmfoldmdk`
is live at `pharmfoldmdk.fly.dev` and absent.** The owner correctly noticed a real
app missing and was left doubting his own memory, because the report gives nothing
to reconcile against — it doesn't state its own scope.

### Step B0 — the read that splits the tree (do this before any fix)

This is a "what does the code/live-state actually do" question:

- **What org(s) does the Fly token (`fly_api_token_read`) actually scope to?** The
  handler uses `personalOrganization` — a single org. Confirm whether `pharmfoldmdk`
  is under "Matt Kelly" or a different org (`fly apps list`, app org membership).
- **If `pharmfoldmdk` is in a *different* org:** the report is honest but
  single-org, and the fix is **legibility** (B1) — not enumeration.
- **If `pharmfoldmdk` is in the "Matt Kelly" org and still absent:** that is a
  **real enumeration bug** — the report is silently under-counting, which throws
  the whole report's completeness into question. Report this loudly; the fix is
  different and larger, and we scope it from what you find.

**Report B0 before writing B1.** The fix shape depends entirely on the answer.

### Step B1 — legibility (the likely fix, if B0 says cross-org)

Make the report state its own scope so an absence is never mistaken for a gap:

- The fleet report names the org it enumerated: "4 apps in org **Matt Kelly**".
- If the token can enumerate the owner's org memberships, note that other orgs
  exist and aren't shown ("other orgs not included in this view"). If the API
  makes cross-org listing cheap, listing them is better still — but a single
  honest scope line clears the "am I hallucinating" failure at minimum cost.

The bar: a live app the owner knows about should read as **"in a different org,
not shown here"**, never as a silent omission. That is the same anti-silent-gap
discipline as every other health surface in the system.

### Tests

- **Report names its org scope** — the fleet report output contains the org name
  it enumerated.
- **(If B0 = bug)** tests scoped from the actual finding — TBD after the read.

---

## Guardrails

- **Behind the arc.** Do not start until step 7 is merged. `handlers/infra.py`
  only; if this wants to touch an arc file, stop.
- **Change B is diagnosis-first.** No fix to the fleet enumeration until B0
  reports which case it is. A blind "add pharmfoldmdk" would paper over a possible
  real bug.
- **Deterministic state in the overlay** — `fly_balance_alert_threshold` is a
  runtime setting, not a constant. The billing model is state.
- **Living-document rule:** if the component/settings inventory in
  `docs/ARCHITECTURE.md` lists infra behavior, update it. Small change; likely a
  line.
- **Run both suites before pushing.**

## Report back

- **B0 first, separately if needed:** which org `pharmfoldmdk` is under, and
  therefore whether Change B is legibility or a real enumeration bug.
- Change A: the default (`None`) produces no balance fault, asserted; prepaid
  threshold fires, asserted.
- Change B1 (if legibility): the report states its org scope.
- Migration head unchanged (this adds a setting via the overlay, not a migration —
  if you're writing one, stop and report why).
