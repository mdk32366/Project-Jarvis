# Pre-Work — Project Management Arc

**Date:** 2026-07-31
**Status:** Designed, not built. TDD #1 merged and live; the rest queued.
**Purpose:** Carry the five-TDD project arc forward into a fresh chat stream so no
context is lost if the day ends here.

---

## The shape of the arc

Five TDDs, drafted this session, forming one system. They were written in the
order they occurred to us, but they **build** in dependency order:

```
TDD #1  Project tracking        ── state (project/milestone/document tables)      ── MERGED, LIVE (PR #40)
TDD #2  Planning sessions       ── the interview engine (gate, accumulation)      ── drafted
TDD #3  Repo scaffolding        ── document commits + repo creation               ── drafted
        Project inception       ── #2's engine → #1's rows, dated, + timeline      ── drafted (the capstone)
        Capability status       ── independent; SHIPPED this session (PR #49)
```

The through-line: **#1 is state, #2 is the interview engine, inception is #2
producing #1's rows plus a living timeline, #3 is where the documents land.**
Capability status was independent and is already done.

---

## Build order for the arc (when it resumes)

### 1. TDD #2 — Planning Sessions (`TDD-planning-sessions.md`)

The real work of the arc. The **completeness gate (§5) is the invention**;
everything else is plumbing. Origin: on 2026-07-20 JARVIS produced a TDD with
every section a placeholder, because she was asked for a document when she should
have been asked for a conversation. The gate refuses to emit until required slots
are substantively filled.

The two unfakeable slots — `rejected` (an alternative *and why it lost*) and
`open_questions` — are what separate this from a better prompt. You cannot generate
either from a topic name alone; they require a real argument to have happened.

**Build §5 (the gate) before §7 (emission)** — building emission first produces a
system that emits, with a gate bolted on afterward, which is how gates end up
bypassable.

### 2. TDD #3 — Repo Scaffolding (`TDD-repo-scaffolding.md`)

**Read the existing code before speccing the build.** `create_project_from_idea`
already exists under the Ideas agent and already hits `POST /user/repos` — so §6.2
(repo creation) may be a **refactor, not a new build**, and the `GITHUB_ADMIN_TOKEN`
argument in §4.2 may already be satisfied by whatever token that path uses. Have
the Builder read that handler first; do not design around a capability that is
partly built.

Key invariants regardless: the **secret scanner runs before any write path exists**
(§4.5, §8), repo creation is **gated** (creating a repo is irreversible in a way a
PR is not), and **document commits never target `main`** — branch + PR only.

### 3. Project Inception (`TDD-project-inception.md`)

The capstone. Depends on #1 and #2. JARVIS interviews the owner into a project —
objectives, milestones, tasks, assumptions, risks, target dates — and the output
is **both** a committed plan document **and** seeded live rows.

Ratified design decisions (from the planning session that produced it):
- **Proposed dates are visibly proposals until ratified** (`date_status`). No
  baseline is set from an estimate — you cannot slip from a date you never agreed
  to. This is the fabrication guard in scheduling form.
- **Full baseline vs. current.** A replan is a *logged event* (from/to/reason), not
  a silent field update — "why did this slip" is answerable only if replans are
  captured. PharmFold-informs-JARVIS made structural.
- **The brief reports slippage as fact, never judgment** — "12 days past baseline,"
  never "you're behind." Guarded in test.
- **Risks and assumptions are rows, not prose** — so JARVIS can resurface a risk
  when it bites ("you flagged Duffel activation as a risk; it is now blocking
  milestone 4").
- **Milestone/task boundary finally resolved** (§4.5): a milestone date is a plan
  the timeline reads; a task date is a commitment that pings you. Kept apart.

Biggest open question (§11): **atomic emit across two systems** — a GitHub commit
and a DB transaction can't share one transaction. Likely resolution: seed rows in
draft, promote on successful commit; or seed first (reversible) then commit. Decide
at build; the requirement (document and rows land together or not at all) is pinned
in the test plan.

---

## Cross-arc notes carried from this session

- **Migration numbers are indicative, not reserved.** Confirm the next free slot
  against the live head at build time. Project tracking took 0024; capability
  rollup and runbook work have since consumed slots. Do not trust the numbers
  written in the draft TDDs.
- **The audit-starvation lesson applies here directly.** Inception and the timeline
  will add tool calls. Every tool call that exercises a component must go through
  `Registry.run_tool`, or it becomes a latent latch of the kind that made Calendar
  read red for four days. This is a live constraint on how the arc's tools are
  wired, not a general nicety.
- **The interview engine (#2) is reused by inception, not rebuilt.** Inception is a
  `project_plan` session type over #2's machinery, with its own slot set and its own
  output half (rows + timeline + document). If #2 is built with that reuse in mind,
  inception is much smaller.

---

## First move when the arc resumes

New chat stream, fresh `git archive HEAD` tarball uploaded to ground the Planner in
committed repo truth. Then: **have the Builder read `create_project_from_idea` and
report what already exists**, because it determines whether the arc starts with #2
(planning sessions) or with a #3 refactor that might already be half-done. Do not
spec #3 from the TDD alone — the repo may already disagree with it.
