# PRE-WORK — next session

**Written at the close of 2026-08-03.** `main` at the #76 merge, head
`0029_plan_draft_status`, no migration outstanding.

Read `SESSION-closeout-2026-08-03.md` first. This is what to *do*; that is what
happened.

**The queue is short.** Four arcs closed today. What's left is one owner-side read
that gates a filed TDD, three small corrections, and one document still two
revisions behind.

---

## Open with these — small, and two of them are steering wrong right now

### 1. The phone log read — five minutes, gates a whole TDD

**Owner-side, on the Pixel. Not the Builder's.**

Open AutoRemote's receive history and Tasker's run log. Find one early-morning
`L S S` triple between 07:00 and 09:15 and answer one question:

> **How many messages were received, and how many task runs started?**

| Result | Meaning | What follows |
|---|---|---|
| **3 received, 1 run** | Tasker discarded overlapping instances | Collision. Fix is a Tasker collision setting. **`TDD-phone-side-receipt.md` is filed, not built.** |
| **1 received** | The other two never arrived | Deferral. The TDD's premise firms up and it becomes worth building. |

Do this **before** gathering more `responded_at` data. Another day of latency
numbers sharpens a premise the phone log might make moot.

If the answer is deferral, one thing falls out: `providers/autoremote.py`'s
docstring asserts *"AutoRemote delivers over high-priority FCM, which Android does
deliver through doze."* No artefact, we set no priority ourselves, and the idle
regime is exactly what that claim predicts won't happen. **The whole pull-inversion
architecture rests on it.** It belongs in `findings.md` with evidence, or marked as
the assumption it currently is.

### 2. Amend the diagnostic note

It still says the fault class moved and warns against power management for the
silences. Replace with §2 of the close-out:

- The early `L S S` rhythm is the **same** doze phenomenon as the 08-01 lateness,
  still running during idle. The fault class did not move.
- The 08-01 fix cleared lateness **during active phone use** only.
- The discriminator is that **the answered nonce is the oldest of its window**,
  ruling out reconnection and leaving deferral vs collision.
- Carry the denominator: 5 prompt / 1 late / 3 silent **of 9**.

### 3. Two loose ends from #76

- **Answer the shared-implementation question.** Is the naming check one function
  called against two sources, or implemented twice? If twice, unify it — two
  implementations of one rule will drift, and both will stay green about different
  things.
- **Plant the "skip is named" property.** Suppress the skip list from the detail
  line and confirm the test reddens. The #76 plant reached the count test only.
- **Confirm the suite count** — the final report was truncated.

---

## Then

### 4. KEEL How-It-Works SVG

Still V3, now three revisions behind the five documents and the deck. It is the
last inconsistency in the set, and the set is about to be taught from.

The V6 structural change it must carry: **the four named documents** —
`architecture.md`, `decisions.md`, `findings.md`, `testplan.md` — which appear
nowhere in the V3 diagram.

### 5. `TDD-phone-side-receipt.md` — only if item 1 says deferral

Filed and unbuilt. Note its own §4.3 before starting: the ACK cannot separate
non-delivery from profile/filter from collision, so **it will not answer its own
motivating question**. Enter deliberately or not at all.

---

## Deferred, recorded so it isn't re-derived

- **Component-detail path in the brief.** Trigger: a *second* component wanting it.
- **`location_keep_pings` is deploy-only**, not on the runtime allow-list. At the
  5-minute interval floor, 200 pings is ~16 hours — shorter than the diagnostic's
  own 24-hour window.
- **Prompt-review limits 2 and 3.** A prompt edited in production but not in seed
  is invisible to everything. And shape 3 — a name present with no real guidance —
  requires judging whether prose *is* guidance, which is not mechanisable. Shape 2
  (guidance present, tool unnamed) *is* findable by a human reading the prompt.
  Don't burn a session automating the first.

---

## Standing reminders

- **Do not cut, upload, or ask for a repo archive.** The claude.ai Project syncs
  source directly from GitHub, so the Planner already reads `backend/`, `ui/` and
  `docs/` at `main`. Decided **2026-07-17** in
  `design-note-project-code-sync.md`, whose title is literally *"decision: no
  archive"* — a zip is opaque to the Project and gives the architect **less** than
  the live sync. Grounding means "read the repo," not "receive a snapshot."

  *This line exists because the note alone was not enough: archives were still
  being cut on 2026-08-03, seventeen days after the decision. See `findings.md`
  F-002.*

- **Step 0 of every build order:** `alembic heads`. Draft migration numbers go
  stale.
- **Plants:** inject a value **no branch can legitimately produce** (§2.7), and
  verify the patch applied before reading the result. A plant that reddens one test
  and not another may be reaching a different property — report it.
- **Instructions to the Builder name module paths, not bare function names.**
- **An empty collection is not evidence of absence.** Key on the fact you want, not
  a proxy that correlates with it.
- **When the question is "what does the code currently do," it goes to the
  Builder** — even when it looks like analysis.
