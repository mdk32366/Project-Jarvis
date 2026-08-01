# BUILD ORDER — Inception (capstone), Steps 3–4: ratification/baseline + replan/timeline

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-project-inception.md` (§4.3, §4.4, §4.5, §4.7, §7)
**Builds on:** Inception steps 1–2 (#61 — migration 0028, `project_plan` session
type on #2's engine)
**Type:** Code PR. **Merge-on-green.** Steps 3–4 of 7. This is where the schema
from step 1 gets **behavior**: dates become proposable/ratifiable, and slippage
becomes computable. Steps 5–7 (risk/assumption tools, emit, brief) follow.

---

## The one idea steps 3–4 make real: you cannot slip from a date you never agreed to

Step 1 created `baseline_date`, `current_date`, `date_status`. They don't move yet.
This order makes them move — under the fabrication guard (§4.3) that is the whole
point of inception's date model. Get this seam right and the rest of inception is
bookkeeping; get it wrong and JARVIS fabricates commitments the owner never made.

---

## Step 3 — Dates: proposed → ratified, and the baseline (§4.3)

### 3.1 Proposed dates (the interview output)

Milestones elicited during a `project_plan` session carry `date_status='proposed'`
and a `current_date` (the floated date), **`baseline_date` NULL.**

- A proposed date is **visibly marked a proposal** everywhere it is shown —
  `project_timeline`, any status output. Never rendered as a commitment.
- **No baseline is written from a proposed date.** This is the guard: an elicited
  date is a suggestion, not an agreement.

### 3.2 `ratify_plan(session_or_project)` — the one-way gate into tracking (§7)

The single explicit act that converts proposals into commitments:

- For each milestone with a `proposed` date: set `date_status='ratified'` and
  **`baseline_date = current_date`** (baseline is set equal to the then-current
  date, once).
- After ratification, `baseline_date` **never moves** except via `reset_baseline`
  (§4.7, a later concern — but the immutability is asserted now).
- Ratification is the owner's deliberate act. Ungated in the confirmation-gate
  sense (it's not an outward write), but it is the **only** path that writes a
  baseline. Nothing else may set `baseline_date`.

### 3.3 The fabrication guard, as enforced behavior (§4.3)

This is the netstatus-stub / fabricated-green lesson in scheduling form:

- **Before ratification: no baseline exists, and nothing reports slippage.** A
  project with only proposed dates is invisible to any slippage computation and to
  the brief. It cannot slip, because there is nothing agreed to slip from.
- The difference between "proposed" and "agreed" is a **stored fact**
  (`date_status`), not a tone or a rendering choice. Assert that slippage code and
  (later) the brief read `date_status` and refuse to compute against a non-ratified
  milestone.

### 3.4 Milestone vs. task (§4.5) — enforce the boundary here

A milestone date is **a plan the timeline reads; it does not ping.** A task date is
a commitment that pings, and lives in the existing `tasks` store. Inception seeds
milestones into `milestone`; concrete near-term actions become `tasks` rows via the
existing task path. **This order must not wire any reminder/ping to a milestone
date** — if a milestone starts wanting a reminder, it's a task, and goes to the
task path. Assert milestones don't create reminders.

### 3.5 Tests

- **Proposed sets no baseline** — an elicited milestone has `date_status=proposed`,
  `baseline_date` NULL.
- **Ratify sets baseline = current, once** — after `ratify_plan`, `baseline_date`
  == `current_date`, `date_status=ratified`.
- **Only ratify writes a baseline** — assert no other code path sets
  `baseline_date` (grep-level + behavioral: proposing, replanning, seeding all
  leave it NULL until ratify).
- **Unratified project reports no slippage** — a project with only proposed dates:
  slippage computation returns nothing, does not treat proposed as a baseline.
- **Milestone creates no reminder** — seeding/ratifying a milestone does not create
  a `tasks` reminder row. The §4.5 boundary, asserted.

---

## Step 4 — Replan, slippage, and `project_timeline` (§4.4, §7)

### 4.1 `replan(milestone, new_date, reason)` — a logged event, never a field edit (§4.4)

The load-bearing shape: **write the event, then move the date.**

1. Write a `replan` row: `milestone_id`, `from_date` (the current `current_date`),
   `to_date` (`new_date`), `reason` (**required** — the column is NOT NULL from
   step 1), `created_at`.
2. *Then* update `current_date = new_date`.
3. `baseline_date` is **untouched.** A replan moves the plan, not the baseline.

"Why did milestone 4 slip three weeks" is answerable only because the replan was
captured as a first-class event. A `replan` that edited `current_date` without
writing the row would be the overwrite-the-cell anti-pattern the whole design
rejects — assert the row is written and `from_date`/`to_date` are correct.

### 4.2 Slippage — the delta, computed only against a ratified baseline

`slippage(milestone)` = `current_date − baseline_date` in days, **only if
`date_status=ratified`**. A milestone open past its `current_date` with no later
replan also slips against baseline. Null-dated or proposed milestones are excluded
(not counted as on-time, not counted as late — excluded).

### 4.3 `project_timeline(project)` (§7) — read-only, fact-only

Composes the answer to "where is this project":

- Each ratified milestone: title, `baseline_date`, `current_date`, and — if
  current (or today, for an open milestone past its date) is past baseline — the
  **slippage as a day count.**
- Proposed milestones shown **marked as proposals**, no slippage.
- **Never a verdict.** "Milestone 4: 12 days past baseline" is a fact the owner
  reads. "You're behind on this project" is a judgment JARVIS does not make. This
  is the §6 discipline (same as exception-first component health) and it is
  asserted in test: the timeline output contains the day count and **not** an
  evaluative phrase.

### 4.4 `reset_baseline(project, reason)` — the logged re-baseline (§4.7)

The rare legitimate baseline move (the plan genuinely changed, not just slipped):

- **Snapshot the entire current baseline into a `baseline_reset` row** (`snapshot`
  JSON, `reason` NOT NULL) *before* overwriting.
- Then set new baselines. A re-baseline that isn't logged is indistinguishable
  from hiding a slip — so the snapshot-before-overwrite is the guard, asserted.

### 4.5 Tests

- **Replan writes the row, then moves current** — `replan` → a `replan` row with
  correct `from`/`to`/`reason`, `current_date` moved, **`baseline_date`
  unchanged.**
- **Replan without a reason is rejected** — required at the tool and the DB
  (already NOT NULL); assert the tool refuses an empty reason before writing.
- **Slippage is the delta** — milestone 12 days past baseline → `slippage` returns
  12; `project_timeline` and (later) the brief report "12 days past baseline".
- **Slippage only against ratified** — a proposed milestone returns no slippage.
- **Timeline states fact, not judgment** — output contains the day count, contains
  no evaluative phrase ("behind", "late", "failing"). Guards §6 in test.
- **Null-dated milestone excluded** — not counted as on-time or late.
- **reset_baseline snapshots before overwriting** — the old baseline is captured in
  `baseline_reset` before the new one is written; reason required.

---

## Explicitly NOT in this order (steps 5–7)

- **Step 5:** `flag_risk`, `break_assumption`, risk/assumption **resurfacing** (the
  rows exist from step 1; the tools that raise and resurface them are step 5).
- **Step 6:** `emit_project_plan` — plan doc via the **real** `commit_document`
  (#54, not stubbed) + atomic row seeding. The §11 atomicity question resolves here.
- **Step 7:** brief integration — the slippage fact surfaced exception-first, the
  `project_slippage_brief_days` floor.

---

## Guardrails

- **Only `ratify_plan` writes a baseline.** The single most important invariant in
  this order. No seeding, proposing, or replanning may set `baseline_date`.
- **Replan logs the event before moving the date** — never an unlogged field edit.
- **Fact, never judgment** — `project_timeline` (and everything downstream) reports
  the day count, not a verdict. Asserted.
- **Milestone ≠ task** — no reminder/ping wired to a milestone date.
- **Reuse, don't rebuild** — these are new tools on inception's step-1 schema and
  #2's session; no new gate, no new tables (step 1 made them).
- **Registry discipline; living-document rule** (ARCHITECTURE.md tool inventory);
  **run both suites.**

## Report back

- That **only `ratify_plan` writes `baseline_date`** — asserted, grep + behavioral.
- That an unratified project reports **no** slippage (the fabrication guard as
  behavior) — called out.
- That `replan` writes the event row before moving `current_date`, baseline
  untouched.
- That `project_timeline` states the day count and **no** evaluative phrase — the
  fact-not-judgment guard, asserted.
- That a milestone creates no reminder (the §4.5 boundary).
- Migration head unchanged at `0028` (this order adds tools, not schema — step 1
  made the columns; if you're writing a migration, stop and report why).

Merge-on-green once CI is green and ARCHITECTURE.md is updated. Steps 5–7 follow —
step 6 wires to the real `commit_document`, not a stub.
