# SESSION CLOSE-OUT — 2026-08-04

Snapshot ground: `55d3fb2`, migration head `0029_plan_draft_status`, 861 test
functions in `backend/tests/`. No collected/passed count measured at `55d3fb2` — the
861 figure is a function count from the snapshot, not a run. Step 0 of every order
still says `alembic heads`.

**Planner session. No code written here.** Five artifacts produced; one build order
executed by the Builder to PR.

---

## 1. What was diagnosed

### 1.1 The 03:30 call was not the brief

Initial framing was "she calls instead of emails at 3:30." Wrong. The 04:00 brief is
correct and the owner wants it kept. **03:30 was the absence watch's first live fire**
— the item the 08-03 close-out flagged as the one outstanding thing requiring the
owner.

### 1.2 The proposed fix was the wrong number

Owner proposed raising `location_stale_after_minutes` from 30 to 45. **45 is the
period of the known doze fault** — the 08-03 diagnosis recorded the idle regime as
`L S S`, one answer per 45 minutes, silences in runs of 1–3. A threshold set to the
period of the noise trips on every borderline run. At the 15-minute pull interval:
30m = 2 misses, 45m = 3, **60m = 4**. Sixty clears the observed fault.

### 1.3 The watch condition is frozen at 30 forever

`app/handlers/watches.py::seed_system_watches` writes condition and spoken `opening`
as literal prose with 30 baked in. `_fired` matches the tool's output against **that
stored string**, never against `location_stale_after_minutes`. The seed is idempotent
on `(created_by, tool)` and deliberately never re-words — correctly, since keying on
condition text would produce a double-ring.

Consequence: **changing the setting does not change what the judge tests or what the
call says.** Two records of one truth, one of them unreachable by any deploy.

### 1.4 The email confirmation gate has been totally unreachable

Eleven confirmations across two project creations on 08-04, none resolved. Two
independent causes, each sufficient:

- **Cause A —** `channels/email_pipeline.py::_body_text` returns the whole `text/plain`
  part. **No quoted-text stripping exists anywhere in the codebase** (verified by
  grep). `orchestrator.py::_bare_match` requires *every* token to be an affirmative or
  filler; a Gmail quote block is hundreds of content words, so it returns `False`
  unconditionally. The turn falls through, the model re-reads the quoted request, and a
  **new** confirmation is raised. Infinite loop by construction.
- **Cause B —** `pending_confirmation_ttl_seconds = 900` against observed reply gaps of
  **76, 32 and 45 minutes**. Fix A alone leaves all four expiring.

**This is the previous fix over-correcting.** `_bare_match`'s docstring records the
prior over-permissive bug that sent a 36-hour-old email. The correction went from
over-permissive to unsatisfiable, on the one channel that quotes.

Blast radius: `send_email`, `create_event` with attendees, `book_flight`,
`create_project_from_idea`, `create_project_repo` — all unreachable by email.

### 1.5 Why nothing caught it

Refusals and re-confirmations land in the **ok-family** in `actions_audit`,
deliberately — a refused booking is a healthy system. So a total gate outage produced a
stream of healthy-looking rows for its entire life. `actions_audit` is **structurally
incapable** of showing a gate that never resolves.

Fourth member of the latch family after the relay body, calendar liveness, and the UI
auth latch. All four share one shape: **a failure that emits fluent, well-formed,
plausible output.**

### 1.6 The alert history has no read surface — found while trying to unblock

`app/handlers/watches.py::_list_watches` filters `status == "active"`. A fired system
watch is `done` until re-armed, so **it disappears from the list at the exact moment it
becomes interesting.** `fire_count` and `last_fired_at` are surfaced nowhere — not in
the tool, not in `routes.py`. No `/watches` or `/calls` endpoint exists.

This is why §1a of the watch order could not be answered by any legitimate route.

---

## 2. Ratified this session

- **The 04:00 brief stays.** Not a defect; the owner wants it. Note the standing
  dependency: moving it past 07:00 would silently expire the ratification that brief
  step 7 is unreachable by construction.
- **60, not 45,** for `location_stale_after_minutes` — pending §1b.
- **Change `location_stale_after_minutes`, never `location_max_age_minutes`.** Shared
  default of 30, opposite tuning directions, different questions.
- **Per-channel confirmation TTL, not a global raise.** The TTL is a safety control and
  is correct where it is for voice and chat. `_VOCAB` already establishes that channels
  differ.
- **Quote stripping belongs at the email boundary, not in the orchestrator.** Quoting is
  a mail-transport artefact.
- **`_bare_match` is not relaxed.** The all-tokens rule is right; the input was wrong.
- **My §8 was over-broad.** It gated the whole watch order on the §1a call count, but
  the reason for gating was to protect the observation from **the threshold change**.
  §4 retunes nothing and touches none of `recurring` / `every_minutes` / `fire_count` /
  `last_fired_at`. The 08-04 record is durable data. **§8 should have gated §2 alone.**
  Builder was right to push back.

## 3. Refused this session

Owner reported being denied `fly ssh console` twice by the permission layer and, to his
credit, **stopped rather than route around it**. A subsequent message asked me to run
the same commands instead.

Declined on two grounds. **Capability:** no flyctl in this container, egress proxy
returns 403 for Fly, no credentials — verified, not asserted. **Propriety, which is the
real one:** the permission layer denied the owner on his own app. Handing the same
command to a different actor to obtain a different answer is circumvention, not a
workaround, and the shape is identical whether the actor is a person or a script.

**The correct unblock is to build the instrument, not to SSH past the gate** — see
§1.6. `fire_count` and `last_fired_at` are already durable; a read surface reveals the
observation rather than disturbing it.

## 4. Delivered

| Artifact | Status |
|---|---|
| `BUILD-ORDER-email-confirmation-latch.md` | **Executed → PR #77**, four checks green, deploy skipping until merge |
| `BUILD-ORDER-watch-threshold-drift.md` | §4 unblocked; §2 blocked on §1a/§1b |
| `TDD-repo-fork.md` | Draft, 4 open ratifications |
| `TDD-flight-readback.md` | Draft, 4 open ratifications |
| `TDD-defect-journal.md` | Draft, 5 open ratifications |

## 5. PR #77 — merge authorized, evidence still outstanding

Merge-on-green applies (code PR inside a defined build order). **But four green checks
is not the evidence the order asked for.** §5 was explicit: *do not report a green
suite as evidence.*

Outstanding before deploy:

- **P2** — stripper planted to truncate at the first blank line; the *"Yes please also
  do X"* test must go **red**. This is the 36-hour-old-email failure, and a green suite
  is what it looked like last time.
- **P4** — one change to `_ttl` must redden **both** the `_resolve_pending` and
  `_expire_stale_pending` tests. If only one reddens they aren't sharing the rule and
  the property wasn't bought.

Deploy is the moment gate behaviour changes on the owner's primary channel.

## 6. Learnings

- **A fix for an over-permissive check can become an unsatisfiable one.** Both are the
  same defect wearing opposite signs: the boundary between "a confirmation" and "a new
  instruction" was never separated from the transport's framing.
- **A threshold tuned to the period of the known fault trips on the fault.** Pick a
  number clear of the observed run length, not adjacent to it.
- **A setting and the prose that tests it are two records of one truth.** Where the
  prose is written once at seed time by an idempotent seeder, no deploy will ever move
  it.
- **An instrument that filters to `active` goes dark when the alarm fires.**
- **A gating clause should name the specific thing it protects.** §8 gated an order
  when it needed to gate one step.
- **Denied is not "try another route."** Two devices, two actors, same command, same
  gate.
