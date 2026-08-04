# PRE-WORK — next session

Resume from `SESSION-closeout-2026-08-04.md`. **Step 0 of every order: `alembic heads`.**
Expected `0029_plan_draft_status`. Two drafts both claim `0030` — whichever lands second
takes the next number.

---

## A. Owner actions — do these first, they unblock two orders

**A1. Read `/api/settings` through the Admin UI.** Log in; JWT-gated is the front door,
not a wall. Record the effective values of `location_active_start_hour`,
`location_active_end_hour`, and both quiet-hours keys.

> Defaults are **7 and 23**. Under those, `due_for_pull` returns `False` overnight and
> `_check_location_freshness` returns *"not treated as a fault"* — so a 03:30 call
> reporting a **30-minute-old** fix is impossible. If start is 0, that is the cause and
> the threshold change is treating a symptom. **If all four are at defaults, the 03:30
> call should not have happened at all — that is a third finding and more interesting
> than either.**

**A2. Merge PR #77 — after the plant evidence, not the CI green.** P2 and P4 per
close-out §5.

**A3. Note there is no legitimate route to §1a yet.** See B1.

## B. Build queue, in order

**B1. Watch/alert read surface — NEW, small, do first.**
`_list_watches` filters `status == "active"`, so a fired system watch vanishes at the
moment it becomes interesting. `fire_count` and `last_fired_at` surface nowhere; no
`/watches` or `/calls` endpoint exists. Extend the tool to include fired watches with
both fields, and/or add a read endpoint alongside `/api/infra/health`.

This is the **legitimate** answer to §1a. Both fields are already durable in the DB —
deploying a read surface reveals the 08-04 observation rather than disturbing it. Do
not SSH into production to get it.

**B2. `BUILD-ORDER-watch-threshold-drift.md` §4 — unblocked, build it.**
Render condition and `opening` from the effective threshold at fire time (shape (a),
no write path). Do not touch `recurring`, `every_minutes`, `fire_count`,
`last_fired_at`. **§2 (the 30→60 change) stays blocked on A1 and B1.**

Plant P3 is the point of the order: one change must redden **both** the condition test
and the opening test. If only one reddens, they aren't sharing a source.

**B3. Fork** — `TDD-repo-fork.md`. Ratifications below.
**B4. Flight readback** — `TDD-flight-readback.md`.
**B5. Defect journal** — `TDD-defect-journal.md`.

## C. Ratifications outstanding

**Fork (§7).** 7.1 gate on fork but not on adopt-existing. 7.2 no scaffold on a
template `generate`. 7.3 **the load-bearing one** — does a missing tracked project get
created *inside* the confirmed action? Recommended yes; it is the honest answer to "how
do we delegate more smartly" (one confirmation per irreversible outcome, not per
prerequisite) and it sets a precedent about what may ride along inside a gate. 7.4
state the upstream licence as fact.

**Flight (§10).** 48h brief-enrichment horizon; 6h live-traffic horizon;
`<code> parking` convention in `OWNER_PLACES`; enriched readback on voice with a
character-grouped confirmation code.

**Defect journal (§9).** 9.1 email attachment not `commit_document`. 9.2 zero-defect
week still sends. 9.3 items carry until acted on. 9.4 no bespoke health component,
trigger named. **9.5 is genuinely open** — may JARVIS file unprompted? Recommended
middle: structural detectors only, never the model's general sense that something felt
wrong. Start with one detector.

## D. First two defect-journal entries, already identified

1. **The email confirmation latch.** Eleven confirmations, none resolved, `actions_audit`
   green throughout. The detector: *a confirmation raised and re-raised on the same
   `thread_key` more than twice without resolution.* Countable, unfabricatable.
2. **`_list_watches` filtering to `active`.** The instrument goes dark when the alarm
   fires.

Both are the argument for the feature: health checks catch components that stop; the
journal is for failures that keep running and keep looking right.

## E. Standing items

- Go-private at project close-out — covers `create_project_repo`,
  `create_project_from_idea`, `emit_project_plan`, and now `fork_repo`.
- Naming-check duplication from #76 still queued.
- Moving the 04:00 brief past 07:00 would silently expire the "brief step 7 unreachable
  by construction" ratification. The brief stays at 04:00 for now.
- `MANETMDK` / Idea #5 was never created — the latch, not a refusal. Retry after #77
  deploys.
