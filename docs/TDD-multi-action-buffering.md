# TDD: multi-action buffering — "do this, that, and the other" → N deliverables

**Status:** **v1 IMPLEMENTED 2026-07-17** (batch confirm via `PendingConfirmation.batch_id`,
migration 0015). The confirmation-hygiene fixes (pending TTL + bare-affirmative matching)
shipped first and this built on them. What shipped vs. deferred is noted per section below;
§4.2's channel-aware confirmation and §4.4 partial-skip remain open for Architect Claude.

### v1 — what shipped
- Every gated action raised in ONE request shares a per-turn `batch_id`
  (`orchestrator.run`). Ungated actions (tasks, docs, sheets) already execute immediately in
  the sub-agent, so they need no buffering.
- **No-confirmation work runs FIRST.** `run()` handles tool calls in two passes: pass 1
  executes every ungated action (and outright refusals), pass 2 buffers the gated ones — so
  ungated deliverables are completed and their results in hand before any gated action is
  queued, regardless of the model's tool ordering. (Cross-turn ordering — the model calling
  all ungated tools in the same turn — remains prompt-directed; nothing can force a deferred
  tool call.)
- A single bare "confirm" executes the WHOLE batch in creation order and returns one summary
  listing every deliverable; a single "cancel" drops them all (`_execute_batch` /
  `_cancel_batch`). Inherits the TTL + bare-affirmative rules.
- `book_flight`'s TOTP second factor is explicitly **not** batched — it keeps its own flow.
- The orchestrator prompt now instructs a compound request be done in one turn and read back
  as one numbered batch.

### Deferred (open for the architect)
- **Partial skip** ("send the email but skip the invite") — v1 is all-or-nothing per batch.
- **Channel-aware confirmation** (§4.2c) — v1 uses batch-confirm on every channel; voice may
  want sequential/narrated confirmation using the hold-state machinery.
- **A dedicated `buffered_actions` table** — v1 extended `pending_confirmations` (simpler,
  reuses the gate/TTL/audit machinery). Revisit only if the batch grows richer state.

## 1. Problem

A single request often contains several distinct actions:

> "Create a doc summarizing the Kubota research, add a task to order the filters, and email
> me the parts list."

The user's expectation: **three actions are buffered, each is worked, and three deliverables
come back** (a doc, a task, an email). Today the Secretary "has a tough time" with this. Two
root causes, one already fixed, one structural:

1. **Confirmation cross-contamination (FIXED 2026-07-17).** Pending confirmations never
   expired and any message starting with "yes" resolved the latest pending. A stale buffered
   email (`Google Sheets Test Successful`, 36 h old) was sent when the user said "Yes please
   run it now for part numbers and videos". Fixed by a pending TTL and requiring a *bare*
   affirmative. See `orchestrator._resolve_pending`.

2. **No multi-action model (THIS TDD).** The system tracks and confirms **one** pending
   action at a time, resolved by an ambiguous "yes". A compound request that mixes **gated**
   actions (send_email, create_event-with-attendees, book_flight) with **ungated** ones
   (add_task, create_google_doc/sheet, capture_idea) has nowhere to hold N actions and no way
   to confirm/execute them one by one. They collide, mis-fire, or get orphaned.

## 2. Goals / non-goals

**Goals**
- Decompose a compound request into an ordered list of concrete actions.
- Execute ungated actions immediately; queue gated ones for confirmation.
- Produce one deliverable per action, and a single clear summary of all of them.
- Confirm multiple gated actions without ambiguity ("yes" to which one?).
- Survive restarts (durable, like the existing job queue) and never fire a stale action.

**Non-goals**
- Cross-request planning or long-running autonomous workflows.
- Changing the gate's safety properties — gated actions still require explicit, per-action
  confirmation, and the gate still lives only at the orchestrator level.

## 3. Where this lives (the gated/ungated seam)

The hard part is the orchestrator ↔ sub-agent boundary:

- Sub-agents (Secretary) **cannot run gated tools** — `run_agent` refuses them (structural
  safety). The Secretary can `draft_email` but not `send_email`.
- So a compound request splits: the Secretary performs the **ungated** deliverables (task,
  doc, sheet) in its own tool loop, and returns **drafts/proposals** for the gated ones; the
  orchestrator then gates those.

The current single-`PendingConfirmation` model can't represent "3 drafts awaiting sign-off".

## 4. Design sketch (for discussion)

### 4.1 An action buffer, not a single pending
Introduce an **action batch**: a request produces an ordered set of `BufferedAction` rows
(durable), each with: `kind` (tool), `arguments`, `gated` (bool), `status`
(`ready|pending|done|cancelled|expired|failed`), `deliverable` (result summary), and a
`batch_id` + `thread_key`. Ungated actions are executed on arrival (`ready`→`done`); gated
ones enter `pending` and are shown as a numbered readback.

### 4.2 Confirmation model — the key decision
Options for confirming multiple gated actions (needs an architect call):
- **(a) Batch confirm.** "Two of these send email — confirm all, or say which to skip." One
  "confirm" clears the batch; "skip 2" drops one. Fewest round-trips; must read back clearly.
- **(b) Sequential.** Confirm each in turn ("Send the parts email? …now the calendar invite?").
  Unambiguous, but chatty — poor on a voice call.
- **(c) Hybrid.** Batch-confirm on text/email (readback is scannable), sequential on voice
  (can't scan a list aloud). Leans on the channel, matches the codebase's existing
  channel-aware posture (voice vocab is already narrowed).

Recommendation to evaluate: **(c)**, reusing the TTL + bare-affirmative rules already shipped.

### 4.3 Decomposition
Who splits the request into actions?
- **LLM-planned:** the orchestrator emits a small structured plan (list of tool calls) before
  executing — most flexible, needs a schema and a guard against over-planning.
- **Tool-driven:** the model just calls tools in sequence in its loop (today's model), and the
  buffer is populated as gated tools are hit. Simpler, less explicit. `_MAX_ITERS` already
  raised to 10 gives room.
- Recommendation: start tool-driven (no new planning surface), add the buffer to hold gated
  actions; revisit LLM-planned only if decomposition proves unreliable.

### 4.4 Deliverables & summary
Each executed action records its `deliverable` (task #, doc URL, "email sent"). After the
batch drains, the orchestrator returns ONE summary listing all deliverables — so "this, that,
and the other" comes back as three clearly-labeled results, not one blurred reply.

## 5. Data model
- New `buffered_actions` table (or extend `pending_confirmations` with `batch_id` +
  `sequence`). A new table is cleaner; `pending_confirmations` stays the single-gated-action
  primitive the batch is built from.
- Migration + `0013_baseline` coverage; guard per the migration-bootstrap rules.

## 6. Acceptance criteria (tests to write)
1. A 3-action request (doc + task + email) executes the two ungated ones immediately and
   leaves exactly one gated action pending; the reply names all three intentions.
2. Confirming the batch sends the email and reports three deliverables.
3. A partial "skip the email" executes doc + task, cancels the email, reports two.
4. Stale batch actions expire (inherits the TTL fix) and never fire on a later "yes".
5. Restart mid-batch: ungated already-done stay done; pending gated survive and can still be
   confirmed (durability).
6. Voice: sequential confirmation; text/email: batch readback.

## 7. Open questions for the architect
1. Confirmation model — (a)/(b)/(c) in §4.2.
2. New `buffered_actions` table vs extending `pending_confirmations`.
3. Decomposition — tool-driven (recommended start) vs LLM-planned.
4. How much to surface mid-batch on voice (the hold-state machinery could narrate progress).
5. Failure policy: if action 2 of 3 fails, continue with 3 and report, or stop?

## 8. Related
- Confirmation hygiene fixes: `orchestrator._resolve_pending`, `_bare_match`,
  `pending_confirmation_ttl_seconds` (shipped 2026-07-17).
- Gate is orchestrator-only; sub-agents refuse gated tools (`agents.run_agent`).
- Durable-queue prior art: `jobs` table + `recover_stale_jobs` (`app/jobs.py`).
