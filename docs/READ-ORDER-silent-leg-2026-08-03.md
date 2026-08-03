# READ ORDER — The silent leg (2026-08-03)

**For:** Builder (Claude Code)
**From:** Planner
**Type:** **Read-only.** Two reads and two doc amendments. No behaviour changes.
**Follows:** PR #71 (`ae8d5f9`). The freshness TDD is complete; this is about the
fault it is now correctly reporting.

---

## Why this is the next thing

The loop is not healthy. Corrected post-deploy: **5 prompt / 1 late / 3 silent of
9** — 56% fulfilment, which `check_location_responsiveness` reads as degraded, and
does. What changed on 08-01 was the **fault class**, not the health:

- Before: answers arrived, too late → `answering_late` → power management.
- Now: 3 of 9 produce no ping at all → `not_answering` → delivery/Tasker.

Power management is off the table — nothing throttled answers in 13 seconds. But
the other runbook just came on it, and nobody has run it yet.

**The shape points at delivery, not scheduling.** Doze *defers* work; it does not
drop it. A doze-throttled phone answers late, which was the old mode. A phone that
answers in 13 seconds when it answers at all was never throttled — it never got
the message. That is the leg `check_location_scheduler`'s own docstring names as
unobservable: relay acceptance says nothing about relay → FCM → phone delivery.

---

## Read 1 — `relay_accepted` on the silent rows

```sql
SELECT id, trigger, status, requested_at, relay_accepted, relay_error,
       responded_at
FROM location_requests
WHERE requested_at > now() - interval '24 hours'
ORDER BY id DESC;
```

**Check the three silent rows first.** If any has `relay_accepted = false`, this is
a much cheaper problem than the delivery hypothesis — dispatch is failing and
`relay_error` may name it. Report those rows before interpreting anything else.

If all three are `relay_accepted = true`, the request left JARVIS cleanly and the
phone never answered. That is the unobservable leg, and no server-side read can
narrow it further — say so rather than continuing to dig.

Two things worth reporting either way:

- **Clustering.** Are the silent rows adjacent in time, or scattered? A run of
  three consecutive silences is a different phenomenon from three isolated ones —
  the first suggests an interval (phone off, no signal, app killed), the second
  suggests per-message loss.
- **Active-hours edges.** Do any silences sit at the 07:00 or 23:00 boundary? A
  request minted at the edge of the window is a different story from one at 14:00.

---

## Read 2 — Brief timing, to settle §7

Confirm the **effective** values (not the config defaults) of `briefing_hour`,
`briefing_minute`, `location_active_start_hour` and `calendar_timezone`.

Defaults are `06:30` and `07:00` — the brief running thirty minutes *before*
active hours begin, which makes `check_location_freshness` return `ok` every
morning by construction and step 7's line structurally unreachable. Confirm that
holds live, given there has been a 4am call setup at some point.

**If confirmed, no code changes.** Record in the TDD:

- Step 7's component line is **unreachable by construction** — the brief composes
  before active hours open, so freshness is always `ok` at that moment.
- It is also **redundant**. The capability rollup already carries the signal:
  "Location amber" fired off `location_responsiveness`, which is not hour-suppressed
  and correctly caught the silences. The working line was already there.
- The component-detail path is **deferred, not needed** — same reasoning as
  `rearm_on_clear`. Trigger for building it: a **second** component wanting
  per-component detail in the brief. Name that trigger so it isn't re-derived.

---

## Two doc amendments

### A. The diagnostic note

Conclusion stands, reasoning doesn't. Replace "bimodal → continuous is a success
signature" with what the latency numbers actually show:

> Bimodal with a ~55-minute mode → bimodal with a ~13-second mode, plus a residue
> of complete silences. A better result reached by a worse argument: the original
> reading came from arrival cadence, which cannot distinguish prompt answering
> from uniform lateness. Latency settled it; cadence never could have.

**Carry the n.** Nine requests is thin. "5 prompt / 1 late / 3 silent of 9" — with
the denominator — or the claim hardens into fact and gets cited against a future
outage it doesn't cover.

Also record that the fault class **moved** from `answering_late` to
`not_answering`, so a future reader doesn't run the power-management checklist
against silences.

### B. `§2.6` — the heredoc

Confirm this landed. §2.7 (the single-value plant) is committed; §2.6 was in the
previous order's housekeeping and hasn't been confirmed back. If it is missing,
add it: a failed command whose prior edit had already applied — §2.4's class with
the failure inverted, both instances of *the tool's claim about itself is not
evidence*.

---

## Report back

The three silent rows verbatim with `relay_accepted`, whether they cluster, the
effective brief/active-hours values, and confirmation of both doc amendments.

**No code.** If Read 1 says delivery, the fix is a phone-side receipt and that is a
TDD, not an edit.
