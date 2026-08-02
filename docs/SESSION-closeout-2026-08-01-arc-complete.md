# SESSION CLOSE-OUT — Project-Management Arc complete — 2026-08-01

**Session:** 2026-08-01 (full day). Planner + Claude Code (Builder).
**Headline:** The project-management arc is **complete** — TDD #1 (tracking),
TDD #2 (interview engine), TDD #3 (repo scaffolding), and inception (the capstone),
shipped across PR #40 and #53–#63. Plus the morning's `Unauthorized` latch fix
(#52) and the secretary prompt correction (#60).

---

## 1. What shipped (in order)

| PR | What | TDD |
|----|------|-----|
| #52 | `Unauthorized` latch fix — clear `user` state on 401; surface backend detail; + first UI test runner (vitest) | — |
| #53 | Secret scanner + `github_write_log` | #3 steps 1–2 |
| #54 | `commit_document` (branch+PR) + scanner enforcement | #3 steps 3–4 |
| #55 | Scaffold template (versioned, in-image probe) | #3 step 5 |
| #56 | `create_project_repo` + visibility flip (public-by-default) | #3 step 6 |
| #57 | `github_writes` health component | #3 step 7 |
| #58 | Planning-session migration + notes + **the completeness gate** | #2 steps 1–3 |
| #59 | `emit_tdd` + banner + provenance + `commit_document` wiring | #2 emission |
| #60 | Secretary `system_prompt` correction (removed stale "no emit" claim) | — |
| #61 | Inception migration 0028 + `project_plan` session type | inception 1–2 |
| #62 | Ratification/baseline + replan/slippage/`project_timeline` | inception 3–4 |
| #63 | Risk/assumption resurfacing + `emit_project_plan` (atomic) + brief | inception 5–7 |

`main` at **9f292e8**, both clones synced, suite **823 passed / 2 skipped**.
Migration head **0029_plan_draft_status**.

## 2. The arc, in one paragraph

JARVIS can now be **interviewed into a project**. A `project_plan` session runs on
the completeness gate (#58) — it refuses to emit until the substantive slots,
including the unfakeable `risks`/`assumptions`, are really filled. On ratification
(`ratify_plan`) proposed dates become a baseline; nothing can slip from a date the
owner never agreed to. Replans are logged events, not field edits, so "why did this
move" is always answerable. Emit produces both a committed plan document (via the
real `commit_document`, branch+PR, scanned, never `main`) **and** the live tracking
rows — atomically: seed drafts, commit, promote on success, delete on failure, no
half-landed state. The brief surfaces slippage as **fact, never judgment**,
exception-first, and a stalled open milestone surfaces by today's reckoning so a
frozen project can't read as on-plan.

## 3. Decisions ratified this session (ratification-is-the-deliverable)

These changed behavior or standing policy at the moment of the call, with no code
attached; recorded so they don't decay into assumptions:

- **Public-by-default, uniformly** — both `create_project_repo` and
  `create_project_from_idea` create public. Safe *only because* the scanner exists
  and gates every write. **Scanner-precedes-public is a safety property, not a
  preference.**
- **Go-private is owner action, prompted by close-out** — see §6, now load-bearing.
- **`visibility_review`** (auto-surface dormant public repos) — parked and named,
  the structural upgrade if the reminder discipline proves leaky.
- **Option 1 on the 24h JWT expiry** (from the latch fix) — clean redirect, no
  refresh-token flow built speculatively.
- **Inception's §6.3 is not implementable as written** — emit cannot call the
  gated `create_project_repo` from inside an ungated tool (gate bypass); it refuses
  a repo-less project and points at the gated tool instead.

## 4. Documents to commit to `docs/` (the anti-evaporation step)

**These build orders and notes live only in the Planner's outputs + chat. Commit
them so they don't vanish the way the inception TDD did this morning.** Docs-only,
straight to `main`.

**Design records / TDDs (already in `docs/` — verify, don't re-commit if present):**
- `TDD-project-inception.md` (committed 47285aa)
- `TDD-location-freshness-alert.md`, `design-note-answering-late.md`,
  `BUILD-ORDER-location-pull-silence-diagnostic.md` (committed earlier today)

**Build orders to archive in `docs/operational/`** (executed handoffs — the tier
for spent orders):
- `BUILD-ORDER-unauthorized-regression-diagnostic.md`, `...-latch-fix.md`
- `BUILD-ORDER-tdd3-steps-1-2-scanner.md`, `...-steps-3-4-commit-document.md`,
  `...-step-5-scaffold-template.md`, `...-step-6-create-repo-visibility.md`,
  `...-step-7-github-health.md`
- `BUILD-ORDER-tdd2-steps-1-3-planning-gate.md`, `...-tdd2-emission.md`
- `BUILD-ORDER-inception-steps-1-2.md`, `...-3-4.md`, `...-5-7.md`

**NOT yet committed / still queued (leave in Downloads):**
- `BUILD-ORDER-infra-report-legibility.md` — queued, not executed. Commits when run.

## 5. Lessons for the KEEL corpus (the arc's real product)

The arc's failures were almost never wrong logic — they were **instruments that
report confidently while missing what they were built to catch.** New entries:

- **A passing test proves only the failure modes it actually exercised.** #63:
  `commit_document` fails two structurally different ways (raise vs. return-a-string);
  the half-landed test covered only the raising path and read as complete. The
  question that finds the gap: *"how many ways can this fail, and does the test hit
  each?"*
- **Verify the defect actually landed before trusting a watch-it-fail.** #59: a
  `str.replace` silently no-opped, tests passed, read as "guard validated." Assert
  the file changed before running the test.
- **A test that breaks when correct work lands gets replaced, not weakened.** The
  migration-head tripwire (#58) and `test_nothing_can_emit` (#59) — retired with
  successors stating the durable invariant, not muted.
- **Judge success on state, not on another tool's prose.** Emit decides
  emitted-or-not on a document row, never on `commit_document`'s message — a
  reworded refusal can't silently flip behavior.
- **Make the bad state unrepresentable, not just untested-for.** The value-free
  `SecretFinding`; the entropy floor above the hex ceiling; row-level `plan_status`
  (a session flag *couldn't* identify drafts — the schema settled it).

## 6. STANDING ITEM — go-private reminder (now load-bearing)

**This is a required line item in every future close-out until `visibility_review`
exists.** As of this session, **two** paths create public repos by default
(`create_project_repo`, `create_project_from_idea`). The go-private reminder is the
*only* mechanism between "public during active work" and "public forever." At each
close-out:

> **Review repos created public this session. Set private any that are now
> production-stable / dormant, per the ratified go-private-at-close-out policy.**

If this reminder is skipped, a repo stays public past its working window and
nothing catches it. That is the known risk of a discipline-not-structure guard;
`visibility_review` is the structural fix when/if it proves leaky.

**This session:** no *new-project* repos were created (the arc built the machinery,
didn't run it on a real new project). `Project-Jarvis` itself remains public by
KEEL doctrine (pre-production-stable). Nothing to flip today — but the discipline
starts now.

## 7. Open threads into next session — see PREWORK note
