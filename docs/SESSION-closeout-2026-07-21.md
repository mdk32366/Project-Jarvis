# Session Close-out — 2026-07-21

**Focus:** Location schedule inversion (JARVIS pulls, phone answers) — designed,
built, and debugged through two independent faults to a working transport.
Secondary: three TDDs drafted for project tracking, planning sessions, and repo
scaffolding.

**Resume point:** set the Tasker Event filter to `^[A-Za-z0-9_-]{22}$` (regex on,
exact off), confirm the first `fulfilled` request, then work the §8.1
diagnostic-revert checklist and the export/scrub/commit sequence.

*Amended at close: PR #40 was merged and deployed after this document was
drafted. The only outstanding work is phone-side.*

---

## 1. What shipped

| PR | Contents |
|---|---|
| **#36** | Migration 0021, `LocationRequest`, `app/providers/autoremote.py`, pull on the worker tick, health-check split (`location_pull_scheduler` + `location_responsiveness`), retirement of `location_pings` freshness check |
| **#37** | TDD revision in canonical + migration 0022 (`location_pings.trigger`), retained manual push |
| **#38** | `relay_accepted` / `relay_error` rename (migration 0023), body-reading in `send()`, `_scrub()` fix for percent-encoded values, `relay_rejected` fault code, key normalization |
| **#39** | Bare-nonce message format, phone-side regex filter, `NONCE_PATTERN` pinned by test, runbook and §8 setup corrections |
| **#40** | Project tracking (TDD #1 steps 1–5), migration 0024 — **merged `1d167ec`** |

Test count moved 523 → 527 → beyond with #38/#39. Migration gate passed against
fresh Postgres on each — which matters, because the SQLite test path never runs
the migration chain.

## 2. Where it stands

**Working:** server-side pull on a 15-minute cadence; AutoRemote relay accepting
with a genuine `OK` body; message delivered to the phone; Tasker Event profile
firing; Get Location running; POST landing; ping recorded with `trigger=pull`.

**Not yet closed:** no request has ever reached `fulfilled`. 27+ pings, zero
linked. `location_responsiveness` reads `down` — **accurately**. The remaining
gap is the phone-side filter, which still matches the retired `jarvis_locreq`
format.

**Immediately next:** change the Tasker profile's message filter to the nonce
regex shipped in #39 — `^[A-Za-z0-9_-]{22}$`, **not** a prefix match; the prefix
form was discussed and did not ship (see §6). That is one field on one screen. The
next scheduled pull should then flip `timeout` → `fulfilled`, and responsiveness
climbs to `ok` after 5 of 6.

---

## 3. The two faults, and why the order mattered

This is the part worth keeping. The evening's shape was produced by **two
independent defects, one masking the other.**

### Fault 1 — the key carried a literal `key=` prefix

`AUTOREMOTE_KEY` was set to `key=<token>`, copied out of the personal URL's query
string with the parameter name still attached. The TDD's own §9 invited this by
describing the value as "the `key=` parameter"; the error was in the instruction,
not the execution.

The relay answered **HTTP 200 with a body of `NotRegistered`** on every dispatch.
`send()` checked only `r.status_code == 200`, so `dispatch_ok` read `True` for
every dispatch over the entire life of the feature while not one message was
delivered. `location_pull_scheduler` read `ok` throughout a total delivery
failure.

### Fault 2 — the nonce never reached a readable variable

Messages were sent as `jarvis_locreq=:=<nonce>`, relying on AutoRemote's
command/parameter split. On this device the split yielded **one** field:
`%arpar1` held `jarvis_locreq` and `%arpar2` did not resolve at all. There was
never a nonce to find.

### Why this was slow

**Fixing fault 1 changed nothing observable.** Messages began genuinely arriving,
the Tasker task fired, pings landed on cadence — and `request_id` stayed null,
exactly as before. The visible evidence after the fix was indistinguishable from
the evidence before it.

Worse, the intermediate evidence fit a wrong theory well. A manual send from the
web console succeeded at 16:21 while a scheduled dispatch two minutes later did
not, which looked like a payload-encoding difference and put suspicion on the
`=:=` separator. It was not the separator — the manual send simply used a
correctly-formed key. **The separator question could not even be tested until
fault 1 was fixed, because nothing was arriving at all.**

The record must not read as though the key fix should have worked. It worked
completely; a second fault stood behind it.

---

## 4. Lessons

**A transport that reports failure in-band must be read in-band.** AutoRemote
returns 200 for rejected sends and reports the outcome in the body. Status-code
checking against such an API is not a shortcut — it is a blind spot with a green
light on it.

**Third instance of one recurring error: a signal riding on a proxy instead of
the real property.** Quiet-hours exemption keyed on `kind` rather than provenance
(R22). "Destructive" riding on notional value rather than its own flag
(`netstatus.py`). Delivery riding on status code rather than the body. Named so
it is findable a fourth time.

**When a fix changes nothing observable, suspect a second fault rather than
concluding the fix failed.** The instinct to revert or re-diagnose the first fix
is wrong when the first fix is independently verifiable — verify it directly
(`relay_accepted: True` with an `OK` body) and then look past it.

**Diagnostic settings can break the diagnosis.** Carried from 07-19 and true
again: a Flash action with unbracketed variables cannot distinguish "empty" from
"did not print." Brackets around a variable in a debug output are the difference
between an answer and another round trip.

**Read the plugin's own configuration screen rather than guessing variable
names.** Four wrong guesses on the AutoRemote Advanced screen cost several round
trips to the phone. The screen documented `Tasker Vars Message`, `Comm Params
Prefix`, and `Command` with a worked example. Same lesson as `%gl_latitude` vs
`%LOC_LAT` earlier in the same session.

**Prefer eliminating a coupling over pinning it.** The bare-nonce format made the
phone-side filter depend on the mint's exact shape (`token_urlsafe(16)` → 22
chars). A pinning test is the correct treatment when a coupling cannot be
removed; a prefix (`jarvis<nonce>`, matched on prefix, sliced on the phone)
removes it outright and was the better call.

---

## 5. Code decisions taken by the builder, ratified

- **`app/providers/autoremote.py`**, not `backend/integrations/` — the latter
  does not exist in this repo; `sms.py` sets the convention.
- **`check_freshness` and orphaned helpers deleted**, not left registered but
  unreachable — the `seed_agents()` stale-registration lesson.
- **`relay_error` renamed alongside `relay_accepted`** — renaming half a pair
  leaves the other half naming a leg it cannot see.
- **Fault code `relay_rejected`**, not `dispatch_failing`.
- **Key normalization: strip-and-warn**, not reject. Rejecting fails closed but
  turns a paste error into a dead feature; stripping makes the common mistake
  harmless and still says so out loud.

---

## 6. Open items

### RESOLVED — the filter string is `^[A-Za-z0-9_-]{22}$`

The **bare nonce** shipped in #39 (merged, `dec9e11`). The prefix follow-up
(`jarvis<nonce>` / `^jarvis`) was **not** folded in — the builder implemented the
format described in its own report. Not re-opened: the pinning test covers the
coupling and the filter works either way.

**Tasker Event profile → message filter:**

```
^[A-Za-z0-9_-]{22}$
```

with **Use Regex ON**, **Exact Message OFF**, **Case Insensitive OFF**.

Until this is changed, the literal `jarvis_locreq` filter will never match,
because the message is now just the nonce. Typing the wrong filter reproduces
exactly the failure this session diagnosed: everything working, nothing matching.

**Live coupling to know about.** `NONCE_PATTERN` in the client and the phone-side
filter are the same regex, pinned by
`test_minted_nonces_match_the_phone_side_filter`. `secrets.token_urlsafe(16)` is
always exactly 22 characters from that alphabet. **Changing the mint silently
breaks the phone** — the test is what makes it loud instead. The prefix form
would have removed this coupling rather than pinning it; it remains the better
design if this is ever revisited.

### On the phone (owner)

1. **Change the message filter** to `^[A-Za-z0-9_-]{22}$` — regex ON, exact OFF,
   case-insensitive OFF. *This is the one thing standing between here and a
   working system.*
2. Confirm a request flips to `fulfilled`; watch `location_responsiveness` climb.
3. Duplicate the task as the **manual push** — no profile, home-screen shortcut,
   no nonce, `"trigger":"manual"`. Duplicate rather than one task with a
   conditional.
4. **Delete the old timed profile and task.**
5. **§8.1 diagnostic reverts** — Force High Accuracy off, Continue Task After
   Error off, delete the `err=` flash, delete stray tasks from the house project,
   Tasker battery Unrestricted, Monitor intervals reverted **bottom-up** (they
   interlock).
6. **Export → scrub token → commit `devices/jarvis-location-pull.prj.xml` →
   paste the real token back.** That order. Pending since before today.

### In the repo

- **PR #40 — project tracking. MERGED (`1d167ec`) and deployed** after this
  document was drafted. TDD #1 steps 1–5, 575 tests passing. Migration renumbered
  **0023 → 0024**, `down_revision` repointed at `0023_relay_accepted`, single head
  verified, gate green against fresh Postgres. Migration 0024 has now run against
  production.

  The rebase hit one conflict in `health.py`: main renamed the
  `dispatch_failing` runbook to `relay_rejected` in the same list this branch
  used to add `project_hygiene`. Resolved by keeping both new entries and
  dropping the superseded one. **A blind take-ours or take-theirs would have
  silently lost a runbook** — a fault code with no remediation, surfacing months
  later.

- **TDD migration numbers are indicative, not reserved.** The series now lands at
  projects **0024**, planning **0025**, github writes **0026**. Relevant before
  starting TDD #2.
- **Step 6 of the location TDD** — `request_location_fix()`, the on-demand pull.
  Deliberately unbuilt; it has nothing to call until the phone answers.
- **Prefix change did not ship.** See the RESOLVED item at the top of this
  section. Optional follow-up: one line in `app/providers/autoremote.py` plus a
  filter edit, removing the mint/filter coupling outright rather than pinning it.
  Not urgent — the pinning test makes the coupling loud rather than silent.

### Security

- **AutoRemote key rotated** after the broken `_scrub()` printed it in plaintext
  during diagnosis. Done. The scrubber fix in #38 addresses the cause: it now
  handles percent-encoded values, which matters because an unscrubbed value can
  reach `relay_error` — stored in the DB and rendered on the status page.

---

## 7. Also produced this session

Three TDDs drafted, none built:

- **`TDD-project-tracking.md`** — `project` / `milestone` / `project_document`.
  Partially implemented on the parked branch above.
- **`TDD-planning-sessions.md`** — planning sessions that accumulate across
  channels and emit a TDD only when a **completeness gate** passes. Written in
  response to the 07-20 placeholder-TDD failure. The gate (§5) is the invention;
  `rejected` and `open_questions` are the unfakeable slots.
- **`TDD-repo-scaffolding.md`** — document commits and repo creation. **Check
  before building:** `create_project_from_idea` already exists under the Ideas
  agent and already hits `POST /user/repos`, so §6.2 may be a refactor rather
  than a build, and the `GITHUB_ADMIN_TOKEN` argument in §4.2 may already be
  satisfied.

Also discovered: `ideas.promoted_url` already existed and records that a GitHub
repo exists. `ideas.status` was made orthogonal to it rather than colliding.
