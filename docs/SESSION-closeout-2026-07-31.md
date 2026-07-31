# Session Close-out — 2026-07-31

**Focus:** Closed the location pull loop end to end. The transport built on 07-21
had never once produced a `fulfilled` request; this session drove the nonce
round-trip through **four** distinct faults to first light.

**Result:** `request 647 fulfilled` at 15:30:28 — server-initiated pull → AutoRemote
delivery → Tasker Event profile → GPS fix → POST → nonce match → close-out, every
layer working in sequence for the first time.

**Predecessor:** `SESSION-closeout-2026-07-21.md` (design + build + first two faults).

---

## 1. Where it started today

The 07-21 session ended believing one thing stood between here and done: switch
the Tasker filter from `jarvis_locreq` to `^[A-Za-z0-9_-]{22}$`. That was true but
radically incomplete. After the filter change, 600+ scheduled requests had fired,
every one `timeout`, zero `fulfilled`. Pings arrived on cadence with
`request_id=None` throughout. The transport worked; the correlation never did.

The whole session was isolating why the nonce never completed its round trip.

## 2. The four faults, in the order they were peeled

This is the record worth keeping. Each fault **masked the next** — fixing one
revealed the one behind it, and the visible symptom (`timeout`, null
`request_id`) was identical across all four. Nothing about the symptom
distinguished them; only instrumentation did.

| # | Fault | Why it hid | Fix |
|---|---|---|---|
| 1 | Key stored as `key=<token>` | Relay returned 200 + `NotRegistered` body; status-only check read green | Bare token (07-21) |
| 2 | Nonce sent as `jarvis_locreq=:=<nonce>`, split yielded one field | Delivery worked, so nothing looked wrong server-side | Bare nonce, PR #39 (07-21) |
| 3 | HTTP body had a stray `%` — posted `%<nonce>` | Real nonce arrived; tripped `startswith("%")` guard, read as unresolved | Removed stray `%` (07-31) |
| 4 | `Tasker Vars Message` Advanced field blank | Body was *correct* (`%armessage`); variable never populated, so it posted the literal token | Set field to `armessage` (07-31) |

Faults 3 and 4 are the day's work, and their ordering is the lesson: **fixing
fault 3 made the symptom worse-looking, not better.** Before the fix, the log
showed `'%U4Gkia...'` — the real nonce with a `%` bolted on. That is *closer to
working* than what came after: removing the `%` produced `'%armessage'` literal,
because the body was now correct but the variable behind it was never set.

An observer watching only "did it close" would have concluded the fix regressed.
The instrumentation (PR #46, below) is what showed the value changing from
"right answer, one junk char" to "field name correct, unpopulated" — which is
what correctly redirected attention from the body to the profile wiring.

## 3. The instrumentation that ended the guessing

For most of the session, three parties were reasoning about a config file none
could read: the Planner guessing variable names, the Builder deducing from
server-side effects, the owner relaying screenshots. Four wrong variable-name
guesses were made against that blind spot.

**PR #46** (`debug/log-unmatched-location-nonce`) turned it observable: log the
received nonce, quoted, on every path where a nonce is present but no request
closes — distinguishing empty (`''`), unresolved (`%`-prefixed), and unmatched
(a real string that doesn't match the mint). The quoting was the point: it was
the *value* `'%U4Gkia...'` — not merely "unresolved" — that revealed a stray `%`
on a genuinely-correct nonce, which no boolean could have shown.

The decisive log line:
```
15:15:25  location ping nonce unresolved: '%U4GkiaJiPFBCHIBweXwLug' — dropped
```
That single quoted value collapsed the entire remaining search space.

## 4. Lessons

**A symptom identical across faults cannot guide diagnosis — only instrumentation
can.** Four faults, one symptom (`timeout` + null `request_id`). Every advance
this session came from making the *value* visible, never from reasoning about the
symptom. When four distinct causes produce one appearance, stop reasoning about
the appearance.

**A fix can make the symptom look worse while moving forward.** Removing the stray
`%` changed the logged value from a real nonce to a literal `%armessage`. That is
progress that reads as regression. The tell was in the *content* of the failure,
not the fact of it — which is only available if the content is logged.

**The phone-side config was the load-bearing blind spot, and it still is.** The
mint side is pinned by a test; the body-template and profile wiring live on the
device and are pinned by nothing. The blank `Tasker Vars Message` field cost the
back half of the session and would have been a two-second grep against a
committed export. **The export is not housekeeping — it is the only instrument
that makes the phone-side config reviewable.** It has been owed across two
closeouts now.

**Fifth instance of the recurring proxy-signal error, arguably.** `relay_accepted`
(status not body), quiet-hours on `kind` not provenance, "destructive" on notional
value — and now, a guard rejecting on `startswith("%")` as a proxy for "unresolved
variable," which false-positived on a real nonce that happened to carry a stray
`%`. The guard was reasonable; the lesson is that `%`-prefix is a proxy for
unresolved, not the thing itself.

## 5. Immediate orders for the Builder

1. **`LOCATION_TOKEN` — rotate.** It appeared in plaintext in an HTTP-body
   screenshot shared this session (`X-Jarvis-Token: fkk9r4tf…`), so it is
   compromised. Regenerate, `fly secrets set`, and confirm the phone's header is
   updated in the same pass as the export scrub (§6, owner). Until rotated, treat
   it as public.
2. **Retire or gate PR #46's debug logging.** It was scaffolding for this hunt.
   Options: revert the branch now that the loop is closed, or keep the three lines
   behind a `LOG_LOCATION_NONCE` runtime flag (default off) — the `unmatched` case
   in particular stays useful for the *next* time a phone-side edit breaks the
   round trip, which the phone-side pinning gap guarantees will recur. Recommend
   keeping `unmatched` behind a flag, dropping `empty`/`unresolved`. Builder's
   call on shape; the requirement is that `main` not carry always-on per-ping
   nonce logging.
3. **`backfill_projects.py` — commit it.** Ran with `--commit` against production
   (5 projects, 39 milestones); it is an operational artifact, not scratch. Land
   under `scripts/` or `docs/operational/`, wherever that class lives.

## 6. Owner actions still owed on the phone

Now that the loop is proven, these are cleanup — none blocks a working system, but
all of it is invisible in a week if not done now.

1. **The export, finally.** Export the project → scrub the token (which you are
   rotating anyway, so this is free) → commit `devices/jarvis-location-pull.prj.xml`
   → restore the token. **This closes the phone-side blind spot that cost most of
   today.** Do it before any further body-template edits.
2. **Manual push task** — duplicate the answer task, no profile, home-screen
   shortcut, no nonce, `"trigger":"manual"`.
3. **Delete the old timed profile and task.**
4. **§8.1 diagnostic reverts** — Force High Accuracy off, Continue Task After
   Error off, delete the `err=` flash (and any debug Flash added today), Tasker
   battery Unrestricted, Monitor intervals reverted **bottom-up**.

## 7. Verification signal to watch

`location_responsiveness` read `down` truthfully through the entire nonce failure
— it scores fulfilment, and fulfilment was genuinely broken. It will now climb to
`ok` after 5 of 6 fulfilled requests, **on its own**. That climb is the
confirmation that the attribution mechanism works end to end: the check was never
wrong, it was pointing at the correct fault (nothing fulfilling) while the runbook
it named (the phone) happened to be where the fix lived after all. Do not touch
the check; let it green itself.

## 8. Carried forward, unchanged

- **PR #40** (project tracking, migration 0024) — still green, still unmerged.
  Merging deploys and runs 0024 against production. Owner's merge decision.
- **`request_location_fix()`** (location TDD step 6) — the on-demand pull, still
  unbuilt. It now has a working round-trip to call, so it is unblocked for the
  first time. Candidate for the next build session.
- **TDD #2 / #3** (planning sessions, repo scaffolding) — drafted, unbuilt.
  Migration slots now **0025 / 0026** (0024 taken by project tracking). Numbers
  indicative, not reserved.
- **Prefix-form follow-up** — the `jarvis<nonce>` / `^jarvis` design that removes
  the mint/filter coupling was never folded in. Still the better design; still
  optional. Lower priority now that the export will make the phone side reviewable
  regardless.
