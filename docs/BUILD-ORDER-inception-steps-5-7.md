# BUILD ORDER — Inception (capstone), Steps 5–7: risks/resurfacing + emit + brief

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-project-inception.md` (§4.6, §4.7, §6, §7, §11)
**Builds on:** inception 1–2 (#61), 3–4 (#62), TDD #2 emit path (#59), TDD #3
`commit_document` (#54)
**Type:** Code PR(s). **Merge-on-green** for the code. **This closes inception —
and with it the entire project-management arc.** Three steps: resurfacing tools,
emit (the one real open design question), brief.

---

## Step 5 — Risk/assumption tools + resurfacing (§4.6)

The rows exist (step 1). This adds the tools that raise and resurface them.

### 5.1 Tools

- `flag_risk(project, description, milestone=None)` — inserts a `plan_risk`
  (`status='open'`). Callable during and after the interview.
- `break_assumption(assumption_id)` — flips a `plan_assumption` to `broken`.
- `retire_risk(risk_id)` / mark `realized` — a risk that fired or is no longer live.

### 5.2 Resurfacing — the reason rows beat prose (§4.6)

A risk buried in a document is inert; a risk as a row can be **surfaced when it
bites.** `project_timeline` (extend from step 4) and the brief (step 7) surface:

- **Open risks**, especially one linked to a milestone that is now active or
  slipping: "You flagged Duffel live-mode as a risk at inception; it is now
  blocking milestone 4."
- **A newly-`broken` assumption**, surfaced **once** — an assumption that turned
  out false is worth a single, un-repeated flag (§4.6). "Surfaced once" means the
  brief marks it seen after first surfacing; don't re-alarm every morning.

### 5.3 Tests

- `flag_risk` inserts an open row; `break_assumption` flips to broken.
- A risk linked to a slipping milestone is surfaced by `project_timeline`.
- A broken assumption surfaces **once** — assert it's not re-surfaced on the next
  compose after being seen.

---

## Step 6 — `emit_project_plan` — and the atomicity question, RESOLVED (§4.6, §11)

Emit is inception's payoff: a `project_plan` session becomes a committed plan
document **and** the seeded live rows (milestones with dates, risks, assumptions).
It refuses on an incomplete session by calling **#2's gate** (extended to the
project slot set in #61) — not a new check.

### 6.1 Voice-excluded, like every emit (§ the emit-path pattern)

`emit_project_plan` is an **outward write** (a plan doc committed to a repo, public
under the ratified default). **Structurally voice-excluded, fail-closed** — not in
`VOICE_TOOLS_PHASE1`, absent from a voice-restricted registry, same as `emit_tdd`
(#59) and `commit_document` (#54). Capture and ratify may be voice-reachable;
**emit is not.** Assert the exclusion as an absence, house pattern. (Note: steps
3–4's `ratify_plan`/`replan` are voice-reachable and that's fine — they're logged
and recoverable. `emit` is the outward write, and it's the one that's excluded.)

### 6.2 The atomicity resolution — seed-then-commit, rows own recovery (§11)

A GitHub PR and a DB transaction cannot share one transaction. The requirement
(§10): **no half-landed state** — never a document with no rows, never rows with no
document that silently looks done. The **resolved mechanism** (choosing §11's first
option, made concrete):

1. **Seed rows first, in `draft`** — milestones/risks/assumptions written with a
   `plan_status='draft'` marker (add a nullable `plan_status` to the seeded rows,
   or a session-level flag — pick the lighter; report which). Reversible: draft
   rows can be deleted if commit fails.
2. **Then commit the plan doc** via the real `commit_document` (#54) — branch+PR,
   scanned, never `main`. **Not stubbed** — TDD #3 shipped; the TDD's old "stub
   until #3 exists" note is dead (§ the stale-note correction).
3. **On commit success → promote draft rows to live** (`plan_status='live'`), set
   session `emitted`, `document_id`. On **commit failure → delete the draft rows**
   and leave the session `open` with a clear error. Either the whole thing landed
   (rows live + doc committed) or nothing did (draft rows gone, session still open).
4. A draft row that somehow outlives a failed emit is **visible, not silent** — a
   `project_hygiene`-style check (or a note in `project_timeline`) surfaces
   orphaned drafts so a partial never masquerades as done. This is the §11.8
   lesson (partial-reported-as-partial) applied to inception's two-system write.

### 6.3 Does inception create the repo? (§11)

For a **new** project: the sequence is `create_project_repo` (gated, #56) → seed
draft rows → commit plan → promote. For **JARVIS herself / an existing project**:
the repo exists, skip creation. `emit_project_plan` does **not** itself create the
repo — it calls `create_project_repo` (gated) when needed, keeping repo creation
behind its own gate. Confirm the ordering: repo first, then the seed-then-commit
dance.

### 6.4 Tests

- **Not-ready refuses emit — no rows seeded, no commit** — the gate-before-emit
  proof at the inception layer: an incomplete `project_plan` session → no draft
  rows, no `commit_document` call. Patch the client, assert zero calls.
- **Emit is voice-excluded** — absent from allowlist and restricted registry.
- **Atomic success** — complete session → draft rows seeded, doc committed,
  **then** rows promoted to live, session `emitted`. Assert the promote happens
  after the commit returns success.
- **Atomic failure leaves nothing half-done** — force `commit_document` to fail →
  draft rows deleted, session still `open`, no live rows, no orphaned document.
  **The sharpest test** — assert neither a live-rows-without-doc nor a
  doc-without-rows state exists after a failed emit.
- **Orphaned draft is visible** — a draft row surviving a failure is surfaced, not
  silent.
- **Never `main`** — branch+PR only (inherits `commit_document`'s guarantee;
  assert at this layer too).

---

## Step 7 — Brief integration: slippage as fact, exception-first (§6)

The highest-risk surface — JARVIS commenting on the owner's work unprompted. The
discipline is the §6 rule already proven in `project_timeline` (#62): **report the
fact, never the judgment.**

### 7.1 What surfaces

- **Slippage past the floor** — a ratified milestone whose slippage (in days)
  exceeds `project_slippage_brief_days` (setting, default a small floor — 1 or 2 —
  to avoid one-day noise; **not** 0). "Project X, milestone 4: 12 days past
  baseline." Fact, day count, no verdict.
- **Open milestones slip by today's reckoning** (Code's step 3–4 judgment, now
  load-bearing here): an open milestone past its date is *N days past*, computed
  against **today**, not its plan date — so a **stalled** project surfaces, not
  just a formally-replanned one. A done milestone is judged on its plan date. This
  is what stops a frozen project reporting as on-plan (fabricated-green in another
  costume).
- **A risk now biting** — an open `plan_risk` linked to a slipping/active
  milestone (§5.2).
- **A newly-broken assumption** — once (§5.2).

### 7.2 Exception-first — silence is the default

- A project **on or ahead of baseline** produces **no brief line.**
- A project with **no ratified baseline** is **invisible** to the brief — nothing
  to slip against (the fabrication guard from step 3, at the brief layer).
- Only exceptions surface. The brief is quiet unless something is past its floor.

### 7.3 Fact, never judgment — asserted (§6)

Same `JUDGMENT_WORDS` guard as `project_timeline` (#62): the brief's project lines
contain the day count and **none** of the enumerated evaluative phrases. Reuse the
existing guard list — don't write a second one. Breaking §6 requires consciously
deleting the shared list, not drifting into a phrase.

### 7.4 Tests

- **Slippage past floor surfaces; under floor is silent.**
- **On-baseline project produces no line.**
- **Unratified project is invisible to the brief** (fabrication guard at brief).
- **Stalled open milestone surfaces** — open, past date by today's reckoning, over
  floor → surfaces. The anti-fabricated-green case.
- **Brief states fact, no judgment** — day count present, `JUDGMENT_WORDS` absent.
- **Broken assumption / biting risk surface** per §5.

---

## Guardrails

- **Emit calls #2's gate, not a new one.** Refuse-on-incomplete is the existing
  gate extended to project slots (#61). No new readiness logic.
- **Emit is voice-excluded, atomic, and never `main`.** The three emit invariants.
- **Atomicity: either all landed or none did.** No half state; orphaned drafts
  visible. The sharpest test in step 6.
- **Fact, never judgment** — reuse the `JUDGMENT_WORDS` guard, don't fork it.
- **Repo creation stays behind its own gate** — emit calls `create_project_repo`
  (gated) for new projects; it doesn't create repos itself.
- **Registry discipline; living-document rule** — update ARCHITECTURE.md, and
  **mark inception (and the project-management arc) complete** in the doc, with a
  pointer to where the reconstructed TDD's §5/§8 differed from what shipped
  (migration 0028 not 0026; emit real not stubbed).
- **Open questions to note, not build:** natural-language dates in the interview
  (strict-ISO stands until a deliberate step with `get_current_datetime`);
  re-interviewing an existing project (deferred — living tools cover incremental
  change). Record in §11, don't slip them in.
- **Run both suites.**

## Report back

- That not-ready refuses emit — **no rows, no commit** — the gate-before-emit proof.
- That emit is **voice-excluded** (absent from allowlist + restricted registry).
- **The atomicity outcome:** which draft mechanism (row-level `plan_status` vs
  session flag), and that a forced commit failure leaves **no** half-landed state —
  called out specifically, it's the design question this step resolved.
- That the brief surfaces a **stalled open milestone** (today's reckoning) and
  stays silent on an on-baseline or unratified project.
- That the brief reuses the `JUDGMENT_WORDS` guard, not a fork.
- Migration head — if step 6's draft marker needs a column, that's a small
  migration off `0028` → `0029`; confirm head and report. If a session flag
  suffices, no migration.
- **INCEPTION COMPLETE — project-management arc complete.** TDD #1 + #2 + #3 +
  inception, all shipped.

Merge-on-green once CI is green and ARCHITECTURE.md marks the arc complete.
