# PRE-WORK — next session (after the project-management arc)

**Written:** 2026-08-01, at arc completion (`main` @ 9f292e8).
**Read this first next session, then ground in a fresh `git archive HEAD` tarball
— the Planner's tarballs this session predated the entire arc and every migration
number in every draft was stale. Confirm live state, don't trust these notes for
"what does the code currently do."**

---

## The queue, in recommended order

### 1. Location freshness build — but a READ comes first

The `TDD-location-freshness-alert.md` is committed and ready, **except** one open
question gates the build and it's a Builder read, not a Planner decision:

> **Does `/api/location` (the ingest path) keep or drop a ping that arrives after
> its request already swept to `timeout`?**

This is the whole hinge of the `answering_late` vs `not_answering` distinction. If
the late answer is dropped, the two faults are indistinguishable and step 4a of the
TDD is a real (small) ingest change. If it's kept-and-correlated, the distinction
is already available. **First move: a diagnostic read order for the Builder** —
read `record_ping` / `/api/location`, report keep-vs-drop-vs-kept-uncorrelated.
Then the freshness build order gets written against the answer.

The live outage (phone answering ~50 min late) is still **owner-side and
independent** — confirm what changed since yesterday's clean cadence (the phone was
on-cadence the day before, so a lapsed doze exemption is *less* likely than a
change — a phone/OS update, a battery-saver toggle, a Tasker update). Don't assume
doze; confirm.

### 2. Infra order — ready to hand over as-is

`BUILD-ORDER-infra-report-legibility.md` is written, queued, uncommitted (in
Downloads). Two changes in `infra.py`:
- **Fly balance** → a `fly_balance_alert_threshold` setting (default `None` =
  autopay, no fault). Stops the daily $0.00 noise; a prepaid model can still set a
  real floor.
- **Fleet report legibility** — the PharmFoldMDK-missing question. **Diagnosis
  first:** the read confirmed pharmfoldmdk is *live* at pharmfoldmdk.fly.dev but
  absent from the fleet report (which enumerates org "Matt Kelly"). Step B0 of that
  order determines whether it's a different-org (→ legibility line) or a real
  enumeration bug (→ larger fix). Hand the order over; it self-diagnoses.

### 3. The arc is complete — no arc work remains

TDD #1/#2/#3/inception all shipped. The next *new* build is whatever you choose —
the persona/voice TDD (`TDD-persona-and-voice.md`, preconditions were not yet met),
PharmFold sessions, or KEEL curriculum work (deck + How-It-Works SVG still at V3).
None queued; owner's call.

---

## First moves next session

1. **Fresh tarball, confirm `main` @ 9f292e8 or later, confirm migration head 0029.**
2. **Commit this session's build orders to `docs/operational/`** (the list is in
   the close-out §4) — if not already done at this session's end.
3. **Go-private reminder** (close-out §6) — review any public repos; nothing to
   flip as of arc completion, but the discipline runs every close-out now.
4. Then pick from the queue: location read → freshness build, or hand over the
   infra order, or start something new.

## Standing doctrine reinforced this session (don't re-derive)

- Confirm migration head at build time — draft numbers were stale **five times**.
- The Planner is wrong about live repo state; the Builder reads bytes. Held true
  repeatedly (the `_summarize_promote` default, the `attach_document` double-insert,
  §6.3's un-callable gated tool — all Planner-order errors caught by Builder reads).
- Every outward-writing tool is structurally voice-excluded, fail-closed.
- Every emit calls the existing gate, never re-implements readiness.
- Fact, never judgment, on any surface that comments on the owner's work.
