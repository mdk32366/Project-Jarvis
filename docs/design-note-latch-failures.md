# Design note — Latch failures: when a transient fault becomes permanent

**Status:** Named pattern + design checklist. Written 2026-08-01 after the third
instance in two days. Not a code change; a thing to check *before* writing code.

**Prompted by:** `BUILD-ORDER-unauthorized-latch-fix-v2.md` §"The latch as a class".

---

## Why this note exists

Two bugs in two days had the same shape and neither was found by reasoning about
the symptom — both took a live probe to see, because **the symptom was a
confident, stable, wrong answer.** A third had the same shape and was prevented
at design time, which is the outcome this note is trying to make repeatable.

The point of naming the class is to buy a **design-time question** instead of a
fourth rediscovery.

---

## 1. The class

> **A latch is a transient fault that converts into a permanent state because the
> path that would clear it no longer runs.**

Three parts, all required:

1. **The originating fault is transient** — a token expires, a grant is revoked,
   a network call fails once. Left alone it would resolve or be resolvable.
2. **The failure destroys or starves its own recovery path.** This is the
   defining part. Not "the system stays broken", but "the system removed the
   mechanism that would have noticed it is no longer broken."
3. **The surface reports it with total confidence.** No "stale", no "unknown" —
   a definite answer that happens to be a fossil.

### The tell

**Uniform, content-independent, onset mid-life, immediately after something
worked.** Every request fails identically; nothing about the input changes the
outcome; and the last thing before the failure was a success. That combination is
close to diagnostic on its own.

The corollary is the useful one: **a wrong answer that is perfectly consistent is
more suspicious than one that is erratic.** Erratic suggests a live, varying
cause. Perfectly uniform suggests nothing is being evaluated at all.

---

## 2. Confirmed instances

### 2.1 Calendar liveness (2026-07-31 → 08-01)

`check_liveness` derives health from `actions_audit`: unhealthy iff the most
recent call failed. The Google grant expired, an audited `calendar_lookup`
failed, and `google_calendar_svcacct` went `down` — correctly.

Then the grant started working again and **the component stayed `down` for a
day**, on a credential that a live API call proved fine.

The recovery path was "an audited success". The only thing that exercised the
calendar routinely was the 4 AM brief — which called `_calendar_lookup`
**directly**, bypassing `Registry.run_tool`, writing no audit row. The one
mechanism that could have cleared the latch was the one mechanism that never
recorded anything.

**Fix:** route the brief through the audited seam (PR #51), so real traffic feeds
the substrate the check reads. Swept 12 bypass sites; `list_trips` → `duffel` and
`get_traffic` → `google_maps` were latent latches of the identical shape.

### 2.2 Session auth 401 (2026-08-01)

JWT expired after 24h. The UI's 401 handler called `setToken(null)` — clearing
storage but not the React `user` state. `ProtectedRoute` gates on `user`, still
truthy in memory, so no redirect fired. Every subsequent request went out with no
`Authorization` header and re-401'd, wiping an already-empty token.

The recovery path was "log in again". The failure handler destroyed the
credential while leaving the app convinced it was still logged in — so the user
never reached the login screen that would have fixed it. Only a manual page
reload escaped.

**Fix:** clear the identity and the token together, so the gate fails and the
redirect happens (PR #52).

**And then the latch defended itself.** With #52 merged and deployed — bundle
verified on the server, containing the new message and zero instances of the old
string — the owner was *still* seeing `Unauthorized`. Nothing was wrong with the
deploy.

The open browser tab was running the pre-deploy JavaScript held in memory. The
old code's defining bug is that **it never navigates** — so the page never
reloaded, so it never fetched the corrected bundle. The fix shipped *inside the
page the bug prevents from loading.*

A hard refresh resolved it in seconds. But the shape is worth keeping:

> **A latch in a client can block delivery of its own remedy.** The failure mode
> and the update mechanism were the same mechanism.

This is not exotic — it applies to any browser SPA, long-lived worker, cached
config, or pinned connection. Anywhere the fix arrives through the channel the
bug has jammed, "we deployed it" is not "they have it", and the remediation must
include a forced-reload step rather than assuming the client will pick it up.

### 2.3 Prevented by design — `location_requests` (2026-07-21)

Worth recording because it is the same shape caught *before* it shipped. A
`pending` location request that never resolves would accumulate forever, and
`check_location_responsiveness` scores completed requests — so with nothing ever
completing, it could never read anything but green. `sweep_timeouts` ages
`pending` → `timeout` precisely so the check has something falsifiable to read.

The TDD says it outright: *"Without this, `pending` rows accumulate forever and
the responsiveness check can never read anything but false."* That sentence **is**
the design-time question in this note, asked and answered before the code existed.

---

## 3. What this class is NOT — and why the distinction matters

There is a neighbouring family it keeps getting merged with, and merging them
blunts both checklists. Call it the **proxy-signal** family: *the thing being
measured is not the thing being reported.*

| | Latch | Proxy signal |
|---|---|---|
| Originating fault | transient | persistent from day one |
| What's broken | the recovery path | the measurement itself |
| Before the bug | it worked | it never worked |
| Fix | restore the clearing path | measure the real thing |

Proxy-signal instances in this codebase: `relay_accepted` reading the HTTP status
instead of the response body (the relay answered 200 to everything);
`actions_audit.status` hardcoded `"ok"` pre-epoch; a runbook keyed to a fault code
no check emits; `check_type` declaring `liveness` while `_APP_UP` routes the
component elsewhere.

**These were never transient and never cleared, because they were wrong from the
first commit.** Calling them latches suggests looking for a recovery path that
was never there. The relay bug in particular gets grouped here often — it is not
a latch; `dispatch_ok` was false-green for the feature's entire life.

They share one consequence — **a confident wrong answer** — which is why they feel
alike. But the fixes differ, so the questions must differ.

---

## 4. The fix shape — the same two moves, all three times

1. **Restore the clearing path.** Make sure the transient condition ending is
   something the system can actually observe. Audited traffic for the calendar; a
   redirect to login for the session; a timeout sweep for pending requests.
2. **Stop discarding the detail that identifies it.** Every one of these was
   diagnosable only from a value that had been thrown away — the received nonce,
   the backend's 401 `detail`, the relay's response body.

Move 2 is not cosmetic. In the 401 case the discarded detail nearly **misrouted
the investigation**: the diagnostic order's `grep -rn "Unauthorized" backend/app`
came back empty and instructed us to read that as "upstream 401 surfacing raw."
The string was ours the whole time — it was in the UI. The swallowed detail hid
the bug from the screen *and* from the search for it.

---

## 5. The checklist

Ask at design time, of any check, cache, session, or failure handler:

1. **When this transient condition ends, what specifically clears the state?**
   Name the mechanism. If the answer is "the next successful call", ask what
   makes a successful call happen — and whether that path is one the system can
   see.
2. **Does the failure handler destroy anything the recovery needs?** Credentials,
   tokens, queue entries, the record that a request was ever made.
3. **Is the routine exercise path the same path the check reads?** A component
   exercised daily through an unaudited call is starved regardless of how healthy
   it is.
4. **If it fails, what does the surface still know?** If the answer is "a status
   word", the value that would have identified the cause is already gone.
5. **Can this state be false?** A check that cannot return a bad answer is not
   passing — it is not evaluating. (`published_expiry` returning `unknown`
   forever; `project_hygiene` never returning `down`, which is fine but must be
   *stated*, and is — the amber ceiling is documented on the capability.)

6. **If this latches on a client, can the fix even reach it?** For anything
   browser-delivered, cached, or held by a long-lived process, the update path
   may be the same path the bug has jammed. Deployed is not delivered. The
   remediation needs an explicit forced-reload / restart / cache-bust step.

Question 5 has a mirror worth asking too: **can this state ever return to good?**
That is the latch question stated positively, and it is the one nobody asks.

---

## 6. Testing for it

A latch is invisible to a test that only asserts the failure. `google_calendar_
svcacct` going `down` on a failed call was correct and tested. The bug was
everything after.

**Test the recovery, not just the failure.** Fail → assert down → *succeed* →
assert it clears. `test_expired_token_does_not_latch` is this shape: it asserts
what happens *after* the 401, not the 401.

And the standing discipline, now applied to every guard added this week:
**break the instrument once to confirm it fires.** The runbook-join guards, the
bypass guard, and the three 401 tests were each watched failing before being
trusted. An instrument you have not seen fail is one you do not yet trust — and a
guard against a latch is exactly the kind that silently stops guarding.

### The sharpest instance of that, worth carrying

`test_missing_runbook_degrades_gracefully` asserted `remediation is None` for a
failing component and called it *graceful* — a docstring dressing a bug as design.
Every future fix would have tripped it and been reverted "because it broke a
test."

**A green test asserts behaviour; it does not prove correctness. A bug with a
passing test around it has a defender.**

---

## 7. Related decision — 24h session expiry (ratified, Option 1)

The 401 latch surfaced a genuine UX question: access tokens are HS256, 24h TTL,
no refresh flow, so the owner is silently logged out once a day mid-work.

**Ratified 2026-08-01: leave it.** With the latch fixed, expiry is now graceful —
a clean bounce to `/login` instead of a dead session — so the thing that actually
hurt is gone. A refresh-token flow is real auth surface (new endpoint, rotation
policy, storage decisions), and every one of those is a place to introduce a
subtler version of what was just fixed. Building it speculatively trades a known
small annoyance for unknown auth risk.

**Revisit if** daily re-login proves irritating in practice — not before. The
middle option (sliding expiry / longer TTL) is the cheaper next step if it does.

No code changed for this decision; the ratification *is* the deliverable, which is
why it is recorded here and in `ARCHITECTURE.md` rather than left as a shared
assumption that decays.

---

## 8. Verifying a latch fix in production

A latch fix is unusually easy to *believe* and hard to *see*, because the broken
state and the fixed state look identical from the client until something clears.
Three checks, in order, each answering a different question:

| Check | Question |
|---|---|
| Deploy succeeded | did CI build and ship? |
| **Artifact contains the change** | is the fix actually in what's being served? |
| **Client is running that artifact** | did it reach the user? |

The 401 fix passed the first, passed the second (live bundle hash matched the
built one; new string present, old string absent — count zero), and **failed the
third**. Stopping at either of the first two would have concluded "fixed" while
the owner sat looking at the bug.

Same discipline as the rest of this note: verify the value, not the status.
