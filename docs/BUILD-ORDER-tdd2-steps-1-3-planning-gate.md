# BUILD ORDER — TDD #2 (Planning Sessions), Steps 1–3: migration, notes, **the gate**

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-planning-sessions.md`
**Series:** the interview engine — 2 of 3 in the project arc. TDD #1 (state) shipped;
TDD #3 (repo scaffolding) shipped #53–#57. This is the centerpiece.
**Type:** Code PR(s). **Merge-on-green** for the code. This first order covers
build-order steps **1–3** (the TDD's §9): migration + models, slot classification +
`add_planning_note`, and **the completeness gate**. Emission (steps 5–7) is a
*separate later order* — deliberately, see below.

---

## The one non-negotiable ordering: gate before emission

TDD §9 and the pre-work note are explicit and this is the whole reason the arc is
sequenced the way it is:

> **Build §5 (the gate) before §7 (emission).** Building emission first produces a
> system that emits, with a gate bolted on afterward — which is how gates end up
> bypassable.

So this order stops at the gate. `emit_tdd` is **not** built here. A session that
can accumulate notes and be judged complete-or-not, with nothing yet able to emit,
is the correct intermediate state. The gate is the invention; everything else is
plumbing (TDD §5 opening line). Build the invention first and prove it refuses.

---

## Step 0 — Confirm migration head against live bytes

The Planner's grounding tarball predates the arc — **do not trust a migration
number from any draft or from the Planner.** The draft TDD says `0023`; that is
three slots stale.

```
cd backend && alembic heads
```

Expected single head: **`0026_github_write_log`** (the last arc migration). If so,
this migration is **`0027_planning_sessions`**, `down_revision =
"0026_github_write_log"`. If head is anything else, use the real next slot and
report it before proceeding. A fork or unexpected head is stop-and-report.

---

## Step 1 — Migration `0027_planning_sessions` + models

TDD §6. Two tables, greenfield (confirmed: no planning-session code exists today).

### 1.1 `planning_session` (§6.1)

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `topic` | text, not null | |
| `project_id` | int FK → `project.id`, null | null until linked |
| `target` | str(16) | `jarvis` (→ Project-Jarvis/docs/) or `new_project` (→ TDD #3). Routing decided at session start |
| `status` | str(16) | `open` / `emitted` / `abandoned` |
| `created_at` / `updated_at` | timestamptz | |
| `emitted_at` | timestamptz, null | |
| `document_id` | int FK → `project_document.id`, null | set on emit (later order) |

### 1.2 `planning_note` (§6.2)

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `session_id` | int FK, not null | |
| `slot` | text, null | null = unclassified |
| `content` | text, not null | **as captured, never rewritten** |
| `channel` | str(16) | `sms` / `voice` / `web` / `email` |
| `created_at` | timestamptz | |

**Notes are append-only and preserved verbatim** — mirror the `EpisodeQuote`
discipline (models.py:578): the raw capture is the evidence a real conversation
happened. A misclassified note is *reclassified* (its `slot` changes), never
edited (`content` is immutable). Migration follows the `0024_projects`
dialect (`Table.__table__.create(checkfirst=True)`).

### 1.3 Tests

- Migration round-trips (upgrade/downgrade clean on fresh DB).
- `content` is never mutated by a reclassify — assert byte-identical after a slot
  change.
- At most one `open` session by default (`planning_sessions_concurrent`, default 1)
  — a second `start_planning` while one is open refuses (§4.1).

---

## Step 2 — Slot classification + `add_planning_note` (§7)

The workhorse tool. Must be usable from SMS in one message.

- `add_planning_note(content, slot=None)` — appends a `planning_note`. When `slot`
  omitted, auto-classify into one of the §5.1 slots via the LLM (the same
  LLM-against-prose pattern watches use). Unclassifiable → `slot=null`, surfaced by
  `planning_status()` so it can't silently vanish.
- `start_planning(topic, target, project=None)` — refuses if one is already open.
- `planning_status()` — slots filled, slots missing, unclassified notes, next
  question.
- Notes accumulate across channels — SMS, voice, web, email all append to the one
  open session. This is the durable-slot-state design (§4.1): nothing depends on
  one continuous conversation.

### Tests

- `add_planning_note` from `sms` in one message lands a note.
- Cross-channel accumulation: notes from `sms`, `voice`, `web` on one session all
  present.
- Omitted slot auto-classifies; a garbled note lands `null` and shows in
  `planning_status`.

---

## Step 3 — The completeness gate (§5) — THE INVENTION

**This is what the PR is for.** A `session_readiness()` check that judges whether
required slots are substantively filled, used by (the later) `emit_tdd` to
**refuse** when they aren't.

### 3.1 Slots and their requirement (§5.1)

Required: `problem`, `goals`, `non_goals`, `approach`, `rejected`, `tests`,
`open_questions`. `data_model` required-unless-explicitly-marked-N/A (and the N/A
is itself recorded).

### 3.2 Substance checks, not presence checks (§5.3)

A slot is **empty** (fails the gate) if any of:

- absent, or below `planning_min_slot_chars` (default 120)
- matches a placeholder pattern — `TBD`, `TODO`, `to be determined`, `[...]`,
  `<...>`, `Details to follow`, `N/A` in a required slot
- for `rejected`: fewer than one entry with **both an alternative and a reason**.
  An alternative without a reason is a list, not a rejection.

### 3.3 The two unfakeable slots (§5.2) — the point of the whole design

`rejected` and `open_questions` are the slots you cannot fill from a topic name
alone. Every other slot can be plausibly bluffed; these require an argument to
have actually happened. The gate treats them as load-bearing:

- `rejected` empty, or entries without reasons → refuse, and the missing-slot list
  names it specifically.
- `open_questions` empty → refuse, with a message saying *why* an empty
  open-questions is evidence of insufficient thought, not thoroughness (§5.2).

### 3.4 The gate returns *what's missing and the question that fills it*

`session_readiness()` doesn't just return a bool — it returns the specific missing
slots each with the question that would fill it, so the refusal is actionable
(§5.3). This is what makes refusal a feature, not a dead end.

### 3.5 Tests — the 07-20 regression is the named one (§10)

- **The 07-20 regression** — a session with every slot filled with `TBD` /
  `To be determined` / `[details]` → readiness reports NOT ready, names every slot.
  This is the actual observed failure that created this TDD; it gets a named test.
- **Empty `rejected` refuses** — all else complete, `rejected` empty → not ready,
  list names `rejected`.
- **Alternative without a reason refuses** — `rejected` = "considered Redis" with
  no reason → not ready.
- **Empty `open_questions` refuses** — with the why-message (§5.2).
- **Short slot refuses** — below `planning_min_slot_chars`.
- **Complete session is ready** — all slots substantively filled → ready.
- **Substance over presence** — a slot present but matching a placeholder pattern
  is treated as empty. Assert the placeholder patterns are caught, not just
  absence.

---

## Explicitly NOT in this order (later PRs)

- **`emit_tdd`, the banner, provenance, the branch+PR** (§7.1) — the *next* order,
  after the gate is proven. Emission wires to TDD #3's `commit_document` (which
  now exists, #54). Building emission now would invert §9.
- **`next_planning_question` interrogation loop** (§4.3) — can come with emission
  or its own step; not needed to prove the gate.
- **Health check + Admin panel** (§8) — later.
- **Voice-cannot-emit** (§4.2) — belongs with emission, since there's nothing to
  emit yet.

---

## Guardrails

- **Gate before emission — do not build `emit_tdd` in this PR.** If the work starts
  wanting an emit path, stop; that's the next order.
- **Notes are immutable** — `content` never rewritten; reclassify changes `slot`
  only. The raw capture is the evidence.
- **Registry discipline** — all tools through `Registry.run_tool`.
- **Living-document rule** — adds two tables and tools. Update
  `docs/ARCHITECTURE.md` (table inventory, tool inventory) in the same PR.
- **Run both suites before pushing.**

## Report back

- Confirmed migration head from Step 0 (→ the number this consumed; inception
  rebases off it next).
- That the 07-20 regression test (all-`TBD` → refuses) is present and green —
  the named proof the gate does its job. Call it out.
- That `rejected`-without-reason and empty-`open_questions` both refuse — the two
  unfakeable slots enforced.
- That `emit_tdd` was **not** built — gate-before-emission honored.
- Notes-are-immutable asserted.

Merge-on-green once CI is green and ARCHITECTURE.md is updated. The emission order
follows once the gate is proven.
