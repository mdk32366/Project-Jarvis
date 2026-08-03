# TDD — Phone-side receipt: making the delivery leg observable

**Author:** Planner
**Date:** 2026-08-03
**Status:** Draft. **Premise not yet confirmed — read §0 before building.**
**Follows:** PR #71 (`ae8d5f9`). Location freshness TDD complete.

---

## §0 — Premise: one confirmed, one still open

### 0.1 Dispatch is clean — confirmed

Every request in 24h shows `relay_accepted = true` with an empty `relay_error`,
including all the silent ones (PR #72, read-only). The original gating condition
is **satisfied**: the request left JARVIS cleanly and the phone never answered. No
server-side read narrows it further.

### 0.2 A new gate replaces it — do not build until this is read

The clustering data from the same read raises a hypothesis this TDD **cannot
resolve**, and which — if true — makes most of it unnecessary.

The early-morning pattern is a clean period-3 rhythm (`L S S`, one answer per 45
minutes) with latencies of 35–40 minutes. **The answered nonce is therefore the
oldest of the three in its window, not the newest.** That single fact rules out
the reconnection story: a phone coming back online would have FCM deliver either
all three, or the most recent one at ~10 minutes' latency. Neither matches.

Two hypotheses fit "oldest answered, rest discarded":

- **Doze maintenance windows.** Nudges queue during deep doze; roughly one is
  released per ~45-minute window and the rest expire.
- **Tasker task collision.** All three *are* delivered at the window and arrive
  near-simultaneously; Tasker runs the first instance and discards the overlapping
  ones.

**The ACK does not distinguish them.** A discarded task instance never ACKs, for
exactly the same reason an undelivered message never ACKs — see §4.3. Both
hypotheses also produce identical `acked_at` values (~40 minutes), because both
put the surviving task start at the same moment.

**What does distinguish them is free:** the phone's own AutoRemote receive log and
Tasker run log for one early-morning cycle. Three receives with one task run is
collision — a Tasker collision setting, no build at all. One receive is deferral,
and this TDD's premise firms up considerably.

**Read the phone log before building anything here.** Five minutes on the Pixel
against a multi-step build with a migration is not a close call.

### 0.3 Why this section exists

The freshness TDD's §4.5 specified a latency signal against a field that was
always NULL, caught only by reading live bytes. **A specification resting on an
unverified premise is the same defect one level up.** Premises are written down
here so they can be checked rather than assumed — and §0.2 is what that discipline
looks like when it costs the TDD something.

Everything below remains worth having regardless of which hypothesis wins: the
leg between relay and running task is unobservable today, and that is a structural
gap, not an incident.

---

## 1. Problem

The request path has a leg nobody can see:

```
worker → new_request → relay POST → FCM → phone → Tasker task → /api/location
         [visible]     [visible]    [ DARK ]        [ DARK ]      [visible]
```

`relay_accepted` records that the relay *took* the message. It says nothing about
whether the message was *delivered*. `check_location_scheduler`'s own docstring
already names this: a message accepted and then never delivered reads `ok` there
while responsiveness correctly goes down.

So when a request times out with no ping, the current fault code `not_answering`
covers at least four distinct causes:

1. The message never reached the phone (FCM deferral or expiry, network, phone off).
2. It reached AutoRemote but the Tasker profile didn't fire or the filter didn't
   match.
3. It reached a *running* Tasker task that was discarded as a collision with an
   already-executing instance (§0.2).
4. The task fired but never obtained a fix (location permission, GPS timeout).
5. The task got a fix but the POST failed (token, network, endpoint).

**One fault code, five runbooks.** That is the mis-routing problem #69 fixed for
late-vs-silent, one layer deeper — and the current outage is sitting in exactly
this space: 3 of 9 requests silent, with no way to say which of the five.

Cause 3 was invisible until the 08-03 clustering read and is the reason §0.2
gates this build. It is also the cheapest of the five to fix if it is the one.

---

## 2. Goals

- Split "the request never arrived" from "the request arrived and the fix didn't."
- Record **two latencies, not one** (§4.2) — the current single latency conflates
  delivery time with fix-acquisition time, and they have different causes.
- Route the resulting faults to runbooks that differ.
- Change nothing about the trust direction (§4.5).

## 3. Non-goals

- Fully isolating FCM delivery from Tasker configuration. **Not achievable** with
  an ACK the phone sends — see §4.3, which is the most important section here.
- Any new outbound path to the phone. The server still sends one content-free
  nudge and nothing else.
- Retry, backoff, or re-dispatch. Observability first; policy later, if ever.

---

## 4. Design

### 4.1 The ACK

The Tasker task's **first action**, before it requests a fix, is a POST to a new
endpoint:

```
POST /api/location/ack
X-Location-Token: <LOCATION_TOKEN>
{ "nonce": "<the nonce it received>" }
```

The handler stamps `location_requests.acked_at` for that nonce and returns 200.

- **First ACK wins.** A retry must not move the timestamp, same rule as
  `responded_at` in step 4a.
- **An ACK for an already-`timeout` request is recorded, not rejected.** Identical
  reasoning to the late-ping decision in `close_request`: `status` answers *did it
  arrive in time*; `acked_at` answers *when the phone heard us*. Do not touch
  `status`.
- An ACK for an unknown nonce logs and returns 200. It is not an error worth
  failing on, and a 4xx would teach the phone to retry into a wall.
- Same shared-secret auth as `/api/location`. No new credential.

### 4.2 Two latencies, and why that matters

Today there is one latency — `responded_at − requested_at` — and it silently sums
two independent things. `acked_at` splits it:

| Measure | Span | What a long value means |
|---|---|---|
| **Time-to-task-start** | `requested_at → acked_at` | The nudge did not reach a *running task* promptly. Deferral, queueing, or collision — **not attributable further** (§4.3). |
| **Fix latency** | `acked_at → responded_at` | The task started promptly and the *fix* took a long time. GPS acquisition, location permission, indoors. |

**Name the first one honestly.** An earlier draft called it "delivery latency" and
attributed a long value to doze or FCM backoff. That is wrong: the ACK is stamped
when the *task runs*, so the span includes any time the message sat in a queue on
the phone and any time lost to a collision. Calling it delivery latency asserts a
layer the measurement cannot see — the netstatus-stub defect in a column heading.

The split is still worth having, because it cleanly separates *everything before
the task ran* from *fix acquisition*, and those two have entirely disjoint
runbooks. It just does not subdivide the first half.

Against the current data this matters concretely. The 08-01 outage showed a
~55-minute mode collapsing to ~13 seconds during active use, while the idle
regime still runs 35–40 minutes. With the split we would know that the 13 seconds
is *fix acquisition* and the 40 minutes is *everything before the task ran* —
which is already more than we know today, and is why the "08-01 fix worked" claim
should read **"cleared lateness during active phone use"** rather than
unqualified.

**Do not add a third derived field for total latency.** It is
`responded_at − requested_at`, it already exists, and a stored duplicate is a
second source of truth that will drift.

### 4.3 What the ACK does NOT tell us — read this before writing runbooks

The ACK is sent **by the Tasker task**. Therefore a missing ACK cannot distinguish
*any* of these three:

- the message never reaching the phone;
- the message arriving at AutoRemote while the Tasker profile is disabled or its
  filter doesn't match;
- the message arriving and triggering a task instance that Tasker then **discarded
  as a collision** with one already running (§0.2).

All three produce silence. Splitting them would require an acknowledgement from
AutoRemote itself, which we do not control and will not fake — or the phone's own
receive and run logs, which is exactly why §0.2 gates this build.

**This is the honest cost of the design and it grew after the first draft.** The
third cause was invisible until the clustering read, and it is the one most likely
to be behind the current outage. A build that cannot resolve its own motivating
question should be entered into deliberately, not by momentum.

**So the fault codes must be named for what is actually observed, not for the
cause we would like them to mean.** The honest split is:

| Observed | Fault code | What it rules IN | What it rules OUT |
|---|---|---|---|
| No ACK, no ping | `no_receipt` | Delivery failure, Tasker profile/filter, **or** task collision | The task ran to completion and failed |
| ACK, no ping | `receipt_no_fix` | Fix acquisition or the POST back | Anything upstream of the task |
| ACK, ping after timeout | `answering_late` | Late — and now says *which* leg was late | — |
| ACK, ping in time | *(fulfilled)* | — | — |

`no_receipt` still spans three causes. That is a real and permanent limit, and the
runbook must say so rather than asserting one. **Naming a layer we cannot see is
the netstatus-stub defect**, and it is the specific thing this design is trying
to stop doing.

What we gain is nonetheless large: `receipt_no_fix` is a fault that is
**completely invisible today** and has its own runbook, and `no_receipt` shrinks
the search space from five causes to three.

### 4.4 Fault placement

All three codes live on **`location_responsiveness`**, consistent with the #69
ruling: it is the component that reads `location_requests`, and a second component
emitting overlapping codes is the drift this project has already paid for once.

**`not_answering` is retired and replaced by `no_receipt` and `receipt_no_fix`.**
Retired, not repurposed — leaving the name in place with narrower meaning would
leave a runbook that no longer matches its code, silently. Delete the code, delete
its remediation, add the two new pairs in the same PR so the join guard never sees
an orphan.

Runbooks:

- **`no_receipt`** — the nudge was not acknowledged. Two layers, both listed, in
  order of cheapness: (1) Tasker **collision handling** on the location task — if
  overlapping instances are discarded, a burst of queued nudges yields one answer
  and the rest vanish (§0.2). (2) AutoRemote installed, receiving,
  battery-unrestricted; test from the AutoRemote web console. (3) Tasker profile
  enabled, filter regex `^[A-Za-z0-9_-]{22}$` with Use Regex ON and Exact Message
  OFF. **State explicitly that this fault cannot distinguish the three** and that checking (1)
  first is a cost ordering, not a diagnosis.
- **`receipt_no_fix`** — the phone heard us and no fix came back. Location
  permission "Allow all the time"; GPS available (indoors/underground); the POST
  target and `LOCATION_TOKEN` on the phone matching Fly. This is a genuinely new
  checklist for a genuinely new signal.
- **`answering_late`** — unchanged from #69, but its detail line now names which
  leg was slow (§4.2).

### 4.5 Trust direction is unchanged

The ACK is phone → server, the same direction as every existing ping. Nothing new
flows toward the device; the server still sends one content-free nudge. The
asymmetry the location module was built on is untouched, and this section exists
so a future reader does not have to re-derive that.

---

## 5. Data model

Migration **0030** — confirm with `alembic heads` at Step 0; the number in this
document will be stale if anything lands first.

```
location_requests.acked_at  TIMESTAMPTZ NULL
```

One nullable column. Nothing else changes. `acked_at IS NULL` on every pre-existing
row, which means the new fault split cannot classify historical requests — the
same deploy-boundary property as step 4a, and it must be stated in the PR
description so the first post-deploy reading isn't misread as a finding.

**Do not backfill.** There is nothing to backfill from; the fact was never
recorded.

---

## 6. Build order

**Step 0** — `alembic heads`, clean tree. §0.1 is confirmed; **§0.2 is not.** Do
not proceed until the phone's AutoRemote receive log and Tasker run log have been
read for one early-morning cycle. If that read shows three receives and one task
run, this build is very likely unnecessary — report and stop.

**Step 1** — Migration 0030 + `acked_at` on the model. Nothing reads it yet.

**Step 2** — `POST /api/location/ack`: auth, first-ACK-wins, unknown-nonce
tolerance, no `status` mutation.
*Plant:* make the handler overwrite `acked_at` on every call; the first-ACK-wins
test must go red.

**Step 3** — Fault split in `check_location_responsiveness`: `no_receipt` /
`receipt_no_fix` / `answering_late`, with both latencies in the detail line.
*Plant:* classify on `responded_at` alone, ignoring `acked_at`; the
`receipt_no_fix` tests must go red. **Choose a planted value no branch legitimately
produces** — §2.7 of `design-note-unwatched-instruments.md`.

**Step 4** — Retire `not_answering`: remove the code and its remediation, add the
two new remediation pairs, in one commit.
*Plant:* remove one new remediation and confirm the join guard fires.

**Step 5** — `location_ping_log` and the freshness tool report delivery latency
where they already report fix age. No new surface.

**Owner-side, not the Builder's:** the Tasker task gains a first action that POSTs
the ACK. `.prj.xml` extension with a `<Project>` wrapper. This must ship *before*
Step 3 is deployed, or every request classifies `no_receipt` and the new signal
reads as a total outage on day one.

---

## 7. Test plan

- ACK stamps `acked_at`; second ACK does not move it.
- ACK on a `timeout` request records and leaves `status` alone.
- Unknown nonce: 200, logged, nothing written.
- Bad token: 401, nothing written.
- Classification: all four rows of §4.3's table.
- Both latencies appear in the detail line and are attributed to the right leg.
- Every new fault code joins a remediation; no orphans.
- **Structural:** classification never reads `acked_at IS NOT NULL` as fulfilment.
  Same class of guard as step 4a's — a populated timestamp on a failed row is
  exactly the thing a future refactor mistakes for success.

---

## 8. Open questions

0. **Deferral or collision?** (§0.2) The gating question, answerable only from the
   phone's logs. If collision, the fix is a Tasker setting and this TDD is filed
   rather than built. If deferral, note that the module docstring in
   `providers/autoremote.py` asserts *"AutoRemote delivers over high-priority FCM,
   which Android does deliver through doze"* — a load-bearing claim carrying no
   artefact, which the idle-regime data would falsify. That claim belongs in
   `findings.md` with its evidence, or marked as the assumption it currently is.

1. **Does the Tasker task reliably reach the network before requesting a fix?** If
   the ACK POST is what wakes the radio, delivery latency will read artificially
   high on the first request after idle. Worth one manual observation before
   trusting the number.
2. **Should a missing ACK suppress the fix request?** No — and recorded here so it
   isn't proposed later. The ACK is an observation, never a gate. A phone that
   cannot ACK but can still report a position should still report it.
3. **Sample size before the new codes are trusted.** The split needs roughly one
   window (6 requests) of post-deploy traffic before it means anything, same as
   #69. Carry the n on any claim made from it.
