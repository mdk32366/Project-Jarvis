# BUILD ORDER — Inception (capstone), Steps 1–2: migration + `project_plan` session type

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-project-inception.md` (§4.1, §4.2, §5, §8 steps 1–2)
**Builds on:** TDD #1 (`project`/`milestone`/`project_document` — live), TDD #2
(the interview engine: gate, note accumulation, emission — #58–#59), TDD #3
(`commit_document` — #54, for later steps)
**Series:** the capstone. It **reuses** #2's engine — it does not rebuild the gate.
**Type:** Code PR. **Merge-on-green.** This order is steps 1–2 (migration + the
session type). Steps 3–7 (dates/baseline, replan/timeline, risks/assumptions,
emit, brief) follow as later orders — inception is a 7-step feature, sequenced.

---

## Two staleness corrections the reconstructed TDD carries — read these first

The inception TDD was reconstructed *before the arc finished*, so two of its own
statements are now stale against shipped code. The order corrects both:

1. **Migration number.** The TDD §5 says `0026`. That was consumed by the arc
   (`0026_github_write_log`), and #58 consumed `0027_planning_sessions`. **Live
   head is `0027`; inception is `0028`.** Confirm at Step 0.
2. **"Step 6 stubs the commit until TDD #3 exists" (§8).** TDD #3 **now exists** —
   `commit_document` shipped (#54) and emission wired to it (#59). So the later
   emit step (6) **does not stub** — it wires to the real `commit_document`, the
   same path TDD #2's `emit_tdd` uses. Not this order's concern (it's step 6), but
   don't carry the stub assumption forward.

---

## Step 0 — Confirm head + confirm the #2 engine surface

**Migration head** (the Planner tarball predates the whole arc — do not trust it):
```
cd backend && alembic heads
```
Expect **`0027_planning_sessions`**. If so, inception migration is
**`0028_inception`** (or a descriptive name), `down_revision = "0027_planning_sessions"`.
Stop-and-report if head differs.

**The #2 engine surface inception reuses** — read the real shipped signatures
(#58–#59), because Step 2 extends them:
- How is a session's `target` / session-type represented on `planning_session`?
  (Inception adds `project_plan` as a valid value.)
- How does slot classification (`add_planning_note` auto-classify) decide the slot
  set? (Inception needs a *richer* slot set — §4.2.)
- How does `session_readiness` / the gate read the required-slot set? (Inception's
  gate must require the project-shaped slots too.)

Report how the session type + slot set are represented — that determines whether
Step 2 is a clean extension or needs a small refactor of #2's slot handling.

---

## Step 1 — Migration `0028` + models (§5)

All additive. Confirmed: `Milestone` has no date columns today (verified against
live models this session), so nothing to reconcile.

### 1.1 Columns on `milestone`

| Column | Type | Notes |
|---|---|---|
| `baseline_date` | date, null | Set once at ratification. Moves only via `reset_baseline` (later step). |
| `current_date` | date, null | The live plan date. Moves via `replan` (later step). |
| `date_status` | str(16), default `none` | `none` / `proposed` / `ratified`. |

### 1.2 New tables (append-only where noted)

- `replan` — `id`, `milestone_id` FK, `from_date`, `to_date`, `reason` (**not
  null**), `created_at`. Append-only.
- `baseline_reset` — `id`, `project_id` FK, `snapshot` (JSON of prior baselines),
  `reason` (**not null**), `created_at`. Append-only.
- `plan_risk` — `id`, `project_id` FK, `milestone_id` FK **null**, `description`,
  `status` (`open`/`realized`/`retired`), `created_at`.
- `plan_assumption` — `id`, `project_id` FK, `description`, `status`
  (`holding`/`broken`), `created_at`.

`reason` NOT NULL on `replan` and `baseline_reset` is load-bearing: a replan or
re-baseline with no reason is exactly the silent field-edit §4.4 exists to prevent.
Enforce it at the column, not just the tool.

Migration follows the `0024_projects` / `0027_planning_sessions` dialect. `project`
and `project_document` are reused, not modified.

### 1.3 Tests

- Migration round-trips (upgrade/downgrade clean, fresh DB).
- `replan.reason` and `baseline_reset.reason` reject null at the DB layer.
- `date_status` defaults `none`; the three new milestone columns are nullable and
  default as specified.
- Enum-ish string columns accept their valid values (light check; not exhaustive).

**No behavior yet** — this step is schema only. Dates don't get *set* until Step 3;
this just makes the columns exist. Resist wiring logic in here.

---

## Step 2 — `project_plan` session type on #2's engine (§4.1, §4.2)

Inception is **a session type, not a new engine.** This step teaches #2's existing
machinery one new session type with a richer slot set. **Do not rebuild the gate,
note accumulation, or emission** — extend the slot set they operate on.

### 2.1 Register `project_plan` as a valid session target/type

`planning_session.target` (or however #2 represents type — confirm in Step 0)
gains `project_plan`. `start_planning(topic, target='project_plan', project=...)`
opens an inception session on the same table, same note accumulation, same
cross-channel capture.

### 2.2 The richer slot set (§4.2)

Extends #2's base slots with the project-shaped ones. Required unless noted:

| Slot | Required | Meaning |
|---|---|---|
| `objectives` | ✅ | What the project is for |
| `milestones` | ✅ | ≥2 checkpoints, each a title + (proposed) date |
| `risks` | ✅ | ≥1 named risk |
| `assumptions` | ✅ | ≥1 stated assumption |
| `tasks` | ⚠️ | May be "none yet", recorded as such |

Plus #2's base slots still apply (problem/goals/etc. as the TDD specifies for a
project plan). The gate, extended to this slot set, requires them.

### 2.3 `risks` and `assumptions` are the unfakeable slots (§4.2) — the point

Exactly parallel to #2's `rejected`/`open_questions`: you cannot generate a real
risk or a real assumption from a project name — they require having thought about
what could go wrong and what the plan depends on. The gate treats an empty `risks`
or `assumptions` as insufficient planning and **refuses**, same mechanism as #2's
empty-`rejected` refusal. This is why inception reuses #2's gate rather than
inventing a new one: the completeness discipline is identical, only the slots
differ.

### 2.4 Tests

- `start_planning(target='project_plan')` opens a session on the existing table.
- The gate requires the project slots — a `project_plan` session missing `risks`
  → not ready, `risks` named. Missing `assumptions` → not ready.
- Empty `risks` refuses with the same substance-check as #2's `rejected` (not just
  presence — a placeholder risk is empty).
- Base #2 behavior is unbroken — a normal `jarvis`/`new_project` session still
  gates on its own slot set; the new type didn't regress the old ones. (Assert #2's
  slot set still works — inception must not weaken the engine it extends.)

---

## Explicitly NOT in this order (later steps, sequenced)

- **Step 3:** date proposal + ratification, `baseline_date` establishment,
  `date_status` transitions, the fabrication guard (proposed ≠ baseline).
- **Step 4:** `replan` logging, slippage computation, `project_timeline`.
- **Step 5:** risk/assumption resurfacing tools (`flag_risk`, `break_assumption`).
- **Step 6:** emit — plan doc via `commit_document` (real, not stubbed) + atomic
  row seeding. The atomicity open question (§11) gets resolved here.
- **Step 7:** brief integration — fact-not-judgment, exception-first slippage.

The schema for these lands in Step 1, but their *behavior* is later. Step 1 makes
columns; it does not make them move.

---

## Guardrails

- **Reuse #2's engine — do not rebuild the gate.** If Step 2 starts
  re-implementing readiness logic, stop; extend the slot set the existing gate
  reads. The whole reason inception is small is that #2 already built the hard part.
- **`reason` NOT NULL at the column** — the silent-edit guard is structural, not
  just tool-level.
- **Schema-only in Step 1, no behavior.** Dates don't move until Step 3.
- **Don't regress #2** — the existing session types must still gate correctly.
  Assert it.
- **Registry discipline; living-document rule** (update ARCHITECTURE.md table +
  session-type inventory); **run both suites**.

## Report back

- Confirmed head from Step 0 → the number `0028` consumed (or the real one).
- How #2 represents session type + slot set, and whether Step 2 was a clean
  extension or needed a small refactor.
- That the gate refuses a `project_plan` session with empty `risks`/`assumptions`
  — the unfakeable-slots discipline, inherited from #2, proven for the new type.
- That #2's existing session types are unregressed — asserted.
- `reason` NOT NULL enforced at the DB layer.

Merge-on-green once CI is green and ARCHITECTURE.md is updated. Steps 3–7 follow
as their own orders once the foundation is proven.
