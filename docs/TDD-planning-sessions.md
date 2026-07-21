# TDD — Planning Sessions

**Status:** Draft, ready to build
**Date:** 2026-07-21
**Series:** 2 of 3 (project tracking → **planning sessions** → repo scaffolding)
**Depends on:** TDD #1 (`project`, `milestone`, `project_document`), episodic
memory (Phase 1), runtime settings overlay (PR #28)

---

## 1. Problem

Asked to write a TDD on 2026-07-20, JARVIS produced a document with every section
present and every section a placeholder. None of the substance from the
conversation that preceded it appeared.

**This is not a prompt bug and it will not be fixed by a better template.**

A TDD's *shape* is trivially generatable — headings, tables, a test plan. Its
*content* requires having actually thought about the thing, which requires having
had an argument, considered and rejected alternatives, and been told things the
model could not otherwise know. JARVIS was asked for a document, so she produced
a document. The shape arrived with nothing to fill it, so it filled with
placeholders. **That is the honest output of a system asked for a deliverable
when it should have been asked for a conversation.**

The failure is structural: a single-turn request for an artifact that is only
meaningful as the residue of a multi-turn process.

## 2. Goals

1. A **planning session** is a durable, stateful thing that starts, accumulates
   across turns and channels, and ends — not a request.
2. A session emits a TDD **only when there is something to write down**, enforced
   mechanically rather than by instruction.
3. The output is a TDD **ready to bring to a Planner session** — see §3.
4. Sessions survive channel switches. An idea captured by SMS at the dock, three
   more by voice on the drive, and the real work at a keyboard are one session.
5. The emitted document lands in the right place with the right tier, attached to
   its project.

## 3. Non-goal, and it is the load-bearing one

**The output is not build-ready and must never claim to be.**

A build-ready TDD from JARVIS would put Planner and Builder in the same seat,
which is precisely the split KEEL exists to enforce. JARVIS is not the Planner
here; she is the thing that makes a planning session *possible* away from a
keyboard, and that captures it faithfully enough to argue with later.

The bar is therefore: **does this contain thinking that cannot be reconstructed
from memory, and is it sharp enough to be argued with?** Not "could Claude Code
build this."

This is a lower bar and a more honest one. It also changes what "complete" means
in §5 — completeness is about *having thought*, not about *having specified*.

Other non-goals:

- Emitting to `main` directly. Everything goes to a branch and a PR (TDD #3).
- Replacing sessions like this one. This feeds them.
- Multi-participant sessions.

---

## 4. Design

### 4.1 A session is an object, not a conversation

```
start_planning(topic, project=None)
  └─ INSERT planning_session (status=open)
       └─ turns accumulate as planning_note rows, any channel
            └─ each note classified into a slot (§5)
                 └─ session_readiness() → what's still missing
                      └─ emit_tdd() — REFUSES unless gate passes (§5.3)
                           └─ markdown → TDD #3 → PR → attach_document()
```

The durable thing is **accumulated slot state**, not the transcript. This is what
makes cross-channel work: SMS adds to a slot, voice adds to a slot, the keyboard
session reads all of it. Nothing depends on one continuous conversation.

At most one `open` session at a time by default (`planning_sessions_concurrent`,
default 1). Two open sessions and a stray SMS has no unambiguous home — the
ambiguity is the problem, not the count.

### 4.2 Channel roles

Matt's only voice channel is the Twilio phone call. There is no local voice, no
wake word, no always-listening device. That constrains the design and is worth
stating plainly rather than designing around a capability that does not exist.

| Channel | Role |
|---|---|
| SMS | capture. One thought, one slot. The dock and the parking lot. |
| Voice | capture and interrogate. JARVIS asks the next open question. Bad at revision — no scrollback, no "go back to §5". |
| Web chat | the real work. Review accumulated state, revise, emit. |
| Email | capture, and delivery of the emitted document. |

**Voice cannot be the emit channel.** Reviewing a TDD by having it read aloud is
not review. `emit_tdd()` from voice returns a summary of readiness and the fact
that emission needs a keyboard.

### 4.3 The interrogation loop

On voice and chat, when a session is open and Matt is not driving the
conversation elsewhere, JARVIS asks **the next question that would close the
biggest gap** — not a fixed script.

Priority: problem → rejected alternatives → data model → tests → open questions.
Problem first because everything downstream is unanchored without it. Rejected
alternatives second because it is the hardest to retrofit and the most revealing
(§5.2).

One question at a time. A planning session that feels like a form is one Matt
will stop using, and an abandoned tool captures nothing.

---

## 5. The completeness gate

**This is the invention. Everything else is plumbing.**

### 5.1 Slots

A session accumulates into named slots:

| Slot | Required | What it means |
|---|---|---|
| `problem` | ✅ | What is broken or missing, and how it shows up |
| `goals` | ✅ | What "done" looks like |
| `non_goals` | ✅ | Explicitly out of scope |
| `approach` | ✅ | The chosen design |
| `rejected` | ✅ | ≥1 alternative considered **and why it lost** |
| `data_model` | ⚠️ | Schema/state changes, or explicit "none" |
| `tests` | ✅ | What would prove it works |
| `open_questions` | ✅ | ≥1 unresolved thing |

⚠️ = required unless explicitly marked not-applicable, which is itself recorded.

### 5.2 Why `rejected` is the sharpest requirement

**You cannot fake it.** Every other slot can be filled with something
plausible-sounding derived from the topic name alone. A rejected alternative
requires having actually considered a path and having a reason it lost — which
means either Matt supplied it or a real argument happened.

If JARVIS cannot name something considered and discarded, she has not been
planning. She has been transcribing.

`open_questions` is the second-sharpest and works the same way: a design with no
uncertainty in it is a design nobody thought hard about. **An empty
`open_questions` is evidence of insufficient thought, not of thoroughness.**

### 5.3 The gate

> **If a section would be a placeholder, the session is not done.**
> The correct behavior is to ask the next question — never to emit the
> placeholder.

`emit_tdd()` **refuses** when a required slot is empty, and returns the specific
missing slots with the question that would fill each. Refusal is the feature.

**Substance checks, not just presence.** A slot is empty if any of:

- absent, or below `planning_min_slot_chars` (default 120)
- matches a placeholder pattern — `TBD`, `TODO`, `to be determined`, `[...]`,
  `<...>`, `Details to follow`, `N/A` in a required slot
- for `rejected`: fewer than one entry with **both** an alternative and a reason.
  An alternative without a reason is not a rejection, it is a list.

Placeholder detection is asserted in test against the actual failure mode: a
generated TDD whose sections all read "TBD" must be refused, not emitted with a
warning.

### 5.4 What the gate cannot do

Stated so nobody mistakes the gate for a guarantee: it catches *empty*, not
*shallow*. A 200-character `problem` slot that says nothing passes. The gate
raises the floor from "placeholder" to "someone typed something real," which is
the entire distance between the 07-20 failure and a usable document.

Depth is the Planner session's job. That is the division of labor and it is
correct — trying to enforce depth mechanically would either block real work or
produce a bar so low it is theatre.

---

## 6. Data model

### 6.1 `planning_session`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `topic` | text, not null | |
| `project_id` | int FK → `project.id`, null | null until promoted/linked |
| `target` | enum | `jarvis` (→ `Project-Jarvis/docs/`) or `new_project` (→ TDD #3) |
| `status` | enum | `open` / `emitted` / `abandoned` |
| `created_at` / `updated_at` | timestamptz | |
| `emitted_at` | timestamptz, null | |
| `document_id` | int FK → `project_document.id`, null | set on emit |

`target` is the routing decision from the outset: a new capability *for JARVIS*
goes to `Project-Jarvis/docs/` and adheres to KEEL as today; a new *project* gets
a new repo (TDD #3). Deciding this at session start rather than at emit means the
session knows where it is going while it is still cheap to change.

### 6.2 `planning_note`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `session_id` | int FK, not null | |
| `slot` | text, null | null = unclassified |
| `content` | text, not null | as captured, **never rewritten** |
| `channel` | text | `sms` / `voice` / `web` / `email` |
| `created_at` | timestamptz | |

Notes are append-only and preserved verbatim. Slot *content* is composed from
notes at emit time; the notes themselves are the record. A misclassified note is
reclassified, never edited — the raw capture is the evidence that a real
conversation happened.

### 6.3 Migration

`0023_planning_sessions.py`. Depends on 0022.

---

## 7. Tools

| Tool | Notes |
|---|---|
| `start_planning(topic, target, project=None)` | refuses if one is already open |
| `add_planning_note(content, slot=None)` | auto-classifies when slot omitted |
| `planning_status()` | slots filled, slots missing, next question |
| `next_planning_question()` | the single highest-value gap (§4.3) |
| `emit_tdd()` | **refuses on incomplete** (§5.3); web/email only |
| `abandon_planning(reason)` | terminal, reason required |

`add_planning_note` is the workhorse and must be usable from SMS in one message.

### 7.1 Emission

1. Compose markdown from slots. Standard TDD structure.
2. **Header banner, non-negotiable:**
   ```
   > Drafted in a JARVIS planning session on <date>. Planner-ready, NOT
   > build-ready — bring to a design session before implementation.
   ```
   This is the KEEL boundary made visible in the artifact itself. A document that
   looks build-ready and is not is worse than no document.
3. Append a **Provenance** section: session id, dates, channels used, note count.
   The evidence that a real conversation happened, and the thing that makes a
   placeholder-filled document impossible to pass off as thought-through.
4. Route by `target` → TDD #3 → branch + PR.
5. `attach_document(project, kind='tdd', tier='live', ...)`.
6. Session → `emitted`.

**Never commits to `main`.** A PR is reviewable and revertible; that is the whole
point of the Planner/Builder split having a seam.

---

## 8. Health check — `planning_sessions`

- `ok` — no open session, or one open and updated within 7 days
- `degraded` — an open session untouched for 7+ days (started and forgotten,
  which is the realistic failure), or more than one open
- `unknown` — none ever
- never `down` — a stale planning session is not a system fault

---

## 9. Build order

| # | Work | Testable |
|---|---|---|
| 1 | Migration 0023, models | ✅ |
| 2 | Slot classification + `add_planning_note` | ✅ |
| 3 | **Completeness gate** — the core | ✅ |
| 4 | `next_planning_question` interrogation | ✅ |
| 5 | Emission + banner + provenance (stub the commit) | ✅ |
| 6 | Wire to TDD #3 for real PRs | after #3 |
| 7 | Health check + Admin panel | ✅ |

Step 3 before step 5, deliberately. Building emission first would produce a
system that emits, and then a gate bolted onto something that already works
without it — which is how gates end up bypassable.

---

## 10. Test plan

- **The 07-20 regression.** A session with every slot filled with `TBD` /
  `To be determined` / `[details]` → `emit_tdd()` **refuses**. This is the actual
  observed failure and it gets a named test.
- **Empty `rejected` refuses** — all other slots complete, `rejected` empty →
  refused, and the returned missing-slot list names it.
- **Alternative without a reason refuses** — `rejected` contains "considered
  Redis" with no reason → refused.
- **Empty `open_questions` refuses** — with a message that says why (§5.2).
- **Short slot refuses** — below `planning_min_slot_chars`.
- **Complete session emits** — all slots substantively filled → markdown
  produced, banner present, provenance section present.
- **Banner is not omittable** — assert the not-build-ready banner appears in
  emitted output. Asserted in test, not left as an implementation property.
- **Cross-channel accumulation** — notes from `sms`, `voice`, and `web` on one
  session all appear; provenance lists all three.
- **Notes are never rewritten** — reclassify a note; assert `content` byte-identical.
- **Voice cannot emit** — `emit_tdd()` from voice returns readiness, emits
  nothing, session stays `open`.
- **Second session refused** while one is open.
- **Never targets `main`** — assert the emit path produces a branch ref, never
  `main`. Asserted in test.

---

## 11. Open questions

- **Slot auto-classification accuracy.** Misclassification is recoverable
  (reclassify, notes are preserved) but annoying at volume. Unknown until real
  use. Mitigation: `planning_status()` shows unclassified notes so they cannot
  silently vanish.
- **Does the interrogation loop annoy?** JARVIS asking the next planning question
  during an unrelated call may be unwelcome. Start conservative: only when Matt
  raises the session or explicitly asks.
- **Is 120 chars the right floor?** Arbitrary. It is a setting, not a constant;
  tune from real sessions.
- **Should the gate ever be overridable?** Current answer: no. An override would
  be used at 11pm on a Friday and the resulting document would be exactly the
  07-20 artifact with an extra flag on it. Revisit only with a real case where
  refusal blocked genuine work.
