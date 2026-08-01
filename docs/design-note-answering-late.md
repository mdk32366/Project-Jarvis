# Design note — "answering late" is a distinct fault from "not answering"

**Status:** finding, 2026-08-01. Extracted from the location pull-silence
diagnosis so the lesson is findable without reading the whole freshness TDD.
**Feeds:** `docs/TDD-location-freshness-alert.md` (§4.3, §4.5, §5, §11).

---

## The finding in one line

A monitor that only distinguishes *answered* from *not answered* is blind to a
third state that looks like failure but isn't silence: **answered, but too late
to be useful.** That state has a different cause and a different fix, and
collapsing it into "not answering" sends the diagnosis to the wrong runbook.

## What happened

The location pull loop went six hours stale with no alert. Diagnosis found the
phone was **answering ~50 minutes late** against a 120-second request timeout:
every request swept to `timeout` before its answer arrived, the late answer
landed as an orphan, fulfilment sat at 26%, and the latency was bimodal — a fast
cluster and a slow cluster.

Dispatch worked. FCM delivered. Tasker responded. Nothing was silent. The phone
was simply answering outside the window we were willing to wait.

## Why it matters — three separate lessons

**1. Late is not silent, and the runbooks diverge.**
"Phone not answering" points at Tasker configuration — profile enabled, filter
regex, permissions. "Phone answering late" points somewhere else entirely: power
management (doze, battery optimization, exact-alarm scheduling). A late-answer
fault sent down the not-answering runbook wastes the whole diagnosis re-checking
a config that is already correct — because if the config were broken, nothing
would answer at all. The fault codes must be split precisely because they route
different runbooks; a single "phone" fault with one checklist is half-wrong for
one of its two causes.

**2. Raising the timeout is a fabricated green, not a fix.**
The tempting "fix" is to widen the timeout so the late answers count as
fulfilled. That takes a check which is *correctly red* and turns it green — while
the actual location data stays 55 minutes stale. A 50-minute-old fix is useless
for "where am I now," which is the entire staleness premise the system was built
on. Widening the window doesn't make the fix fresher; it makes the monitor lie
about how fresh it is. Same class as the 17 days of hardcoded `ok` in
`actions_audit`: a green engineered rather than earned. The timeout is measuring
something true — keep it, and name the lateness as its own fault.

**3. The evidence that distinguishes the two is the thing most likely to be
thrown away.**
A late answer arrives *after* its request already timed out. The natural ingest
behavior is to drop it as unmatched — and dropping it destroys the one signal
(a late `responded_at`) that separates "answering late" from "not answering at
all." The distinguisher has to be deliberately preserved: a post-timeout ping
must still be recorded and correlated by nonce, marked late. Discard it and the
two faults become indistinguishable, which collapses lesson 1 back into the
blindness it was meant to fix.

## The honesty boundary (why the alert says less than the diagnosis found)

Bimodal latency is *consistent with* Android doze. But the phone had been
answering cleanly every 15 minutes the day before, and a working power-management
exemption does not spontaneously lapse — so doze is the **leading hypothesis**,
not a finding. The alert names the observable layer ("your phone is answering
late — fixes arrive but too stale to use") and stops. It does not announce
"disable battery optimization," because the cause within the phone layer could be
a phone/OS update, a battery-saver toggle, a Tasker update, or an actual doze
reset. Naming one would be the netstatus-stub defect in alert form: a guess
wearing a fact's clothes. The layer is the honest ceiling; the runbook points the
*investigation* at power management without pretending to have concluded it.

## The reusable tell

When a request/response system reports a fault, ask not only "did it answer?" but
"did it answer *in time to matter?*" — and make sure the late answer survives long
enough to prove it was late. Two systems that both "fail" can fail in opposite
directions with opposite fixes; the timing is the discriminator, and the
discriminator is the evidence most likely to be discarded as noise.
