# TDD — Project Inception & Living Timeline

**Status:** Draft, ready to build (reconstructed 2026-08-01 from the planning
session of the same date — the original draft was written to a scratch path and
never committed; this is the faithful reconstruction, with migration numbers and
schema references re-grounded against the live repo head)
**Date:** 2026-07-31 (original) / 2026-08-01 (reconstruction)
**Series:** the connective tissue between project tracking (TDD #1) and planning
sessions (TDD #2) — the capstone of the project-management arc
**Depends on:** TDD #1 (`project`, `milestone`, `project_document` — SHIPPED,
migration 0024), TDD #2 (interview engine: completeness gate, cross-channel
accumulation, emit path — NOT YET BUILT), TDD #3 (repo commit path — NOT YET
BUILT), morning brief

> **Reconstruction note.** The original of this document was produced in a
> planning session on 2026-07-31, written to `/home/claude/`, and never committed
> to the repo — a live instance of "context isn't durable until it's in the repo."
> Sections 1, 2, 8, 9, 10, 11 and the five ratified decisions are recovered
> near-verbatim from that session. Sections 3–7 are reconstructed to be
> consistent with those anchors and re-grounded against the shipped schema
> (`Project`/`Milestone`/`ProjectDocument` as they actually exist). Where the
> original cited migration `0028`, this reconstruction corrects to **0026**, the
> confirmed next free slot above live head `0025_capability_rollup`. Confirm again
> at build time — the number is indicative, not reserved.

---

## 1. Problem

Projects currently come into being informally — a name, a summary, milestones
entered by hand or reconstructed from a close-out. There is no structured
*inception*: no interview that draws out what a project actually is before work
starts, and no living plan that records what was intended and tracks how reality
diverged from it.

The owner wants JARVIS to **interview him into a project** — milestones,
objectives, tasks, assumptions, risks, target dates — producing both a committed
plan document *and* the live, dated rows that drive a timeline. And he wants the
timeline to **remember**: to hold a baseline, track slippage against it, and
surface it in the daily brief.

This is the capability that multiplies effectiveness. State (TDD #1) records where
you are; the interview engine (TDD #2) produces gated artifacts; this TDD points
that engine at project creation, so the output is both a plan and the seed of the
tracking system — and then keeps that plan honest over time.

## 2. Goals

1. **JARVIS-led project inception**: a structured interview eliciting milestones,
   objectives, tasks, assumptions, risks, and proposed target dates.
2. Output is **both** a committed plan document **and** seeded live rows —
   milestones, risks, assumptions — not one or the other.
3. A **baseline** is established only on explicit ratification, and slippage is
   tracked against it thereafter.
4. A **replan** is a logged event, not a silent field edit — the timeline can
   answer "why did this move" because every move was recorded.
5. The brief surfaces slippage **as fact, exception-first** — the day count past
   baseline, never an evaluative judgment about the owner's work.

## 3. Non-goals

- **Inventing dates.** JARVIS proposes dates in the interview; she never sets a
  baseline from her own estimate. §4.3 is the fabrication guard.
- **Build-readiness.** Like TDD #2's output, an inception plan is Planner-ready,
  not build-ready. It is the residue of an interview, not a design session.
- **Merging anything.** The plan document lands as a branch + PR via TDD #3.
  JARVIS never merges (TDD #3 §3).
- **Amending a plan mid-flight as a full re-interview** — see §11. Incremental
  change is covered by the living tools (`replan`, `flag_risk`); wholesale
  re-planning is deferred.
- **A visual/Gantt render** — §9. The rows and the brief carry the value; the
  picture is v2.

---

## 4. Design

### 4.1 Inception is TDD #2's engine, not a new one

Inception is a **`project_plan` session type** running on TDD #2's interview
machinery — the same completeness gate, the same cross-channel note accumulation,
the same refuse-until-substantive discipline. It differs from TDD #2 in two
places only: a **richer slot set** (§4.2), and an **output half that seeds rows
and a timeline** in addition to committing a document (§4.6).

Building it any other way rebuilds the gate, and a second gate is a second thing
that can be bypassed. The invariant from the pre-work note holds: **the interview
engine is reused by inception, not rebuilt.** If TDD #2 is built with this reuse
in mind, inception is small.

### 4.2 The `project_plan` slot set

Extends TDD #2's base slots (problem, goals, non_goals, approach, rejected,
open_questions) with the project-shaped ones:

| Slot | Required | What it means |
|---|---|---|
| `objectives` | ✅ | What the project is for — the durable "why" |
| `milestones` | ✅ | ≥2 checkpoints, each with a title and (proposed) date |
| `risks` | ✅ | ≥1 named risk. A plan with no risk is a plan nobody stress-tested |
| `assumptions` | ✅ | ≥1 stated assumption — the things that, if false, break the plan |
| `tasks` | ⚠️ | Concrete near-term actions; may be "none yet" and recorded as such |

`risks` and `assumptions` are the inception analogue of TDD #2's `rejected` slot:
the unfakeable ones. You cannot generate a real risk from a project name — it
requires having thought about what could go wrong. An empty `risks` slot is
evidence of insufficient planning, and the gate refuses it (§10).

### 4.3 Proposed dates are proposals until ratified — the fabrication guard

**The load-bearing decision.** Every milestone date carries a `date_status`:

- `proposed` — a date JARVIS elicited or the owner floated during the interview.
  Visibly marked as a proposal everywhere it is shown. **No baseline is set from
  a proposed date.**
- `ratified` — the owner has explicitly accepted the plan's dates via
  `ratify_plan`. Only at ratification is `baseline_date` written, set equal to
  the then-current date.

**You cannot slip from a date you never agreed to.** Before ratification, no
baseline exists and the brief says nothing about dates — a project can't slip
from an unratified plan. This is the same discipline that killed the netstatus
stub and the fabricated-green audit rows, in scheduling form: no fabricated
commitment, and the difference between "proposed" and "agreed" is a stored fact,
not a tone.

### 4.4 Baseline vs. current — a replan is a logged event

Each milestone carries two dates once ratified:

- `baseline_date` — set once, at ratification. **Never moves except through an
  explicit, logged re-baseline** (`reset_baseline`, §4.7).
- `current_date` — the live plan. Moves when the owner replans.

A **replan** is not a field edit. `replan(milestone, new_date, reason)` writes a
`replan` row (`milestone_id`, `from_date`, `to_date`, `reason`, `created_at`) and
*then* updates `current_date`. "Why did milestone 4 slip three weeks" is
answerable only because the replan was captured as an event. This is a
PharmFold-informs-JARVIS lesson made structural: the systems that let you
reconstruct "why did this move" are the ones that logged the move as a first-class
thing, not the ones that overwrote a cell.

### 4.5 Milestone vs. task — the boundary, finally resolved

The distinction that has been implicit since TDD #1 (`Milestone` docstring: "If
something wants a due date and a reminder, it is a task"), stated as a hard rule
for inception:

- **A milestone date is a plan the timeline reads.** It drives slippage and the
  brief's timeline line. It does **not** ping you. It is a marker of intended
  progress.
- **A task date is a commitment that pings you.** It lives in the existing `tasks`
  store, with the existing reminder machinery. It is a discrete action.

They are kept apart deliberately. Collapsing them means either every milestone
nags you (noise) or tasks stop reminding you (missed commitments). Inception seeds
milestones into `milestone`; any concrete near-term actions it elicits become
`tasks` rows through the existing task path — two stores, one interview.

### 4.6 Risks and assumptions are rows, not prose

A risk buried in a plan document is inert. A risk as a **row**
(`plan_risk`: `project_id`, `milestone_id` nullable, `description`, `status`
open/realized/retired, `created_at`) can be **resurfaced when it bites**:

> "You flagged Duffel live-mode activation as a risk at inception; it is now
> blocking milestone 4."

Same for assumptions (`plan_assumption`: `project_id`, `description`, `status`
holding/broken, `created_at`). `break_assumption(id)` flips the status and the
brief surfaces it once — an assumption that turned out false is exactly the thing
worth a single, un-repeated flag.

### 4.7 The living timeline

`project_timeline(project)` composes, from the rows above, the answer to "where
is this project":

- Each ratified milestone: title, baseline date, current date, and — if
  `current_date` (or today, for an open milestone past its date) is past
  `baseline_date` — the **slippage as a day count**.
- Open risks, and any assumption currently `broken`.
- Never a verdict. "Milestone 4: 12 days past baseline" is a fact the owner reads;
  "you're behind on this project" is a judgment JARVIS does not make (§6).

`reset_baseline(project, reason)` exists for the rare legitimate re-baseline (the
plan genuinely changed, not just slipped). It snapshots the entire current
baseline into a `baseline_reset` record before overwriting, so even the act of
moving the baseline is itself recoverable. A re-baseline that isn't logged is
indistinguishable from hiding a slip.

---

## 5. Data model

Migration **0026** (confirmed next free slot above live head
`0025_capability_rollup`; re-confirm at build). All additive — `Milestone` has no
date columns today, so nothing to reconcile.

**Additions to `milestone`:**

| Column | Type | Notes |
|---|---|---|
| `baseline_date` | date, null | Set once at ratification. Moves only via `reset_baseline`. |
| `current_date` | date, null | The live plan date. Moves via `replan`. |
| `date_status` | str(16) | `none` / `proposed` / `ratified`. Default `none`. |

**New tables:**

- `replan` — `id`, `milestone_id` FK, `from_date`, `to_date`, `reason` (not null),
  `created_at`. Append-only.
- `baseline_reset` — `id`, `project_id` FK, `snapshot` (JSON of prior baselines),
  `reason` (not null), `created_at`. Append-only.
- `plan_risk` — `id`, `project_id` FK, `milestone_id` FK null, `description`,
  `status` (`open`/`realized`/`retired`), `created_at`.
- `plan_assumption` — `id`, `project_id` FK, `description`,
  `status` (`holding`/`broken`), `created_at`.

`project.repo_url` and `project_document` already exist from TDD #1 — the plan
document reuses them. `planning_session` (TDD #2) gains `project_plan` as a valid
`target`/session type; no new session table.

---

## 6. Brief integration — fact, never judgment

The highest-value, highest-risk surface. "Project Y slipping" is JARVIS making an
observation about the owner's work and surfacing it unprompted — get the threshold
wrong and it's either noise he learns to ignore or false alarms about projects
that are fine.

The discipline that falls out, and it is the same as exception-first component
health: **the brief reports slippage against baseline, a fact — never "you're
behind," a judgment.** "Milestone 4 is 12 days past its baseline date" is
observable and true. "You're falling behind on Y" is an inference JARVIS does not
assert. Report the fact; let the owner draw the conclusion.

Exception-first: a project on or ahead of baseline produces no brief line at all.
Only slippage past a threshold (a setting, default 0 days but suppressed below a
`project_slippage_brief_days` floor to avoid one-day noise) surfaces. A project
with no ratified baseline is invisible to the brief entirely — nothing to slip
against.

---

## 7. Tools

| Tool | Gated | Notes |
|---|---|---|
| `start_project_plan(name, ...)` | no | Opens a `project_plan` session on TDD #2's engine |
| `ratify_plan(session_or_project)` | no | Sets baselines = current; `date_status=ratified`. The one-way gate into tracking |
| `replan(milestone, new_date, reason)` | no | Logs a `replan` row, then moves `current_date`. Reason required |
| `reset_baseline(project, reason)` | no | Snapshots old baseline, then overwrites. Reason required |
| `flag_risk(project, description, milestone=None)` | no | Adds a `plan_risk` row |
| `break_assumption(assumption_id)` | no | Flips to `broken`; brief surfaces once |
| `project_timeline(project)` | no | Read: baseline/current/slippage + open risks (§4.7) |
| `emit_project_plan(session)` | no* | Atomic: seed rows + commit plan doc (§4.6, §8, §11). Web/email only, inheriting TDD #2's no-emit-from-voice rule |

\* Not gated in the confirmation-gate sense, but `emit` and repo creation route
through TDD #3, whose `create_project_repo` **is** gated. For a genuinely new
project, inception and TDD #3's repo creation are the same moment (§11).

None of these tools may bypass `Registry.run_tool` — the audit-starvation lesson
from the pre-work note applies directly. Every tool that exercises a component
goes through the registry, or it becomes a latent latch of the kind that made
Calendar read red for four days. This is enforced by the test that walks
non-handler app code for direct calls to liveness-backed tools.

---

## 8. Build order

| # | Work | Testable |
|---|---|---|
| 1 | Migration 0026; milestone date columns; risk/assumption/replan/baseline tables | ✅ |
| 2 | `project_plan` session type + slot set on TDD #2's engine | ✅ |
| 3 | Date proposal + ratification; baseline establishment | ✅ |
| 4 | Replan logging; slippage computation; `project_timeline` | ✅ |
| 5 | Risk/assumption rows + resurfacing tool | ✅ |
| 6 | Emit: plan document to repo (via TDD #3) + row seeding, atomic | ✅ |
| 7 | Brief integration (fact-only, exception-first) | ✅ |

Depends on TDD #1 and #2 being built first — this is the capstone of the project
set, not a starting point. Step 6 cannot land before TDD #3's commit path exists;
until then, stub the commit and seed rows only, exactly as TDD #2 §9 stubs its own
emit.

---

## 9. Deferred to v2, recorded so it isn't re-specced

- **Visual timeline (Gantt-ish render)** over the same baseline/current data. The
  rows and brief deliver the value; the visual is presentation.
- **Milestone dependencies / critical path.** Add when real plans show two
  milestones that genuinely block each other and ordering isn't enough.
- **Spoken timeline on voice** — "where's JARVIS at" on a morning call.

---

## 10. Test plan

- **Gate blocks a placeholder plan** — `risks` filled with "TBD" → emit refuses
  (inherits TDD #2's gate; assert it fires for the project slot set too).
- **Proposed ≠ baseline** — propose dates, do not ratify → no `baseline_date` set,
  brief reports no slippage, timeline shows dates marked proposed.
- **Ratification sets baseline = current** — after `ratify_plan`, both dates equal,
  `date_status=ratified`.
- **Baseline never moves on replan** — replan a milestone; `baseline_date`
  unchanged, `current_date` moved, a `replan` row written with from/to.
- **Slippage is the delta** — milestone 12 days past baseline → `project_timeline`
  and brief both report "12 days past baseline", not "behind".
- **Brief states fact, not judgment** — assert the brief line contains the day
  count and not an evaluative phrase. (Guards the §6 discipline in test.)
- **Null-dated milestone excluded from slippage** — an undated milestone is not
  counted as on-time or late.
- **Risk resurfaces linked** — a `plan_risk` linked to milestone 4, when 4 is
  active/blocked, is surfaceable by the resurfacing tool.
- **Broken assumption surfaces** — `break_assumption` → brief surfaces it once.
- **Atomic emit** — if row seeding fails, the document commit does not land
  half-done, and vice versa. A plan that is a document but no rows, or rows but no
  document, is a defect. Assert the two land together or not at all.
- **Re-baseline is logged** — `reset_baseline` snapshots the old baseline into
  `baseline_reset` before overwriting.

---

## 11. Open questions

- **Atomicity of emit across two systems** (repo commit + DB seed). A GitHub PR
  and a DB transaction can't share one transaction. Likely resolution: seed rows
  first (reversible), then commit the doc; on commit failure, the rows exist
  without a document and a follow-up reconciles — or seed rows in `draft` and
  promote them only on successful commit. Decide at build; §10 pins the
  requirement, not the mechanism.
- **Does inception create the repo too?** For a genuinely new project, inception
  and TDD #3's `create_project_repo` are the same moment. Sequence: create repo →
  seed rows → commit plan → ratify. Worth confirming the ordering when both are
  built.
- **Re-interviewing an existing project.** This TDD is inception. Amending a plan
  mid-flight (new milestones surface three weeks in) reuses the replan and
  flag_risk tools, but a full re-interview is undefined. Probably fine to defer —
  the living tools cover incremental change; wholesale re-planning is rare enough
  to handle by hand until it isn't.
- **Voice+SMS as the interview channel.** Inception is a long, high-substance
  interview — chat is its natural home. Whether a project can be *started* by
  voice at the dock and *ratified* at a keyboard is the same cross-channel
  accumulation TDD #2 already supports; worth confirming it carries over cleanly
  to the heavier project-plan slot set.
