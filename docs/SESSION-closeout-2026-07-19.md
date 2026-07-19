# Session close-out — 2026-07-19 → tomorrow's re-entry

Read this first tomorrow. It's the "where were we" so you start sharp instead of
reconstructing.

The headline: **the self-health loop is built, deployed, and has already caught a
real fault in production.** Nine changes shipped (PRs #27–#34). The loop runs
end to end — detect → diagnose → surface → talk about it.

---

## What got DONE today (live and verified)

### The health loop, in build order

| PR | What | Why it mattered |
|---|---|---|
| #27 (PR-0) | Audit status derivation | **The hidden prerequisite.** `actions_audit` hardcoded `status="ok"` on every row — 539 rows over 17 days, zero faults, through real Calendar outages. Liveness reads that column; on an all-green substrate it could never go red. Nothing downstream was buildable until this was true. |
| #28 (PR-1 / R2) | Runtime settings overlay + `get_effective` | Root cause of the morning-brief-time defect: there was no overlay, every reader used `settings.X`, so a changed value was invisible. Migration 0017. |
| #29 (PR-2 / R3) | Scheduler hardening | Heartbeat (gives §5.2 something to read), missed-run catch-up, minute-tick enqueuer replacing the APScheduler cron — hot reschedule falls out for free. Migration 0018. |
| #30 (PR-A) | Relational health model | `component` / `remediation` / `health_result` + tool→component lookup, seeded and **reconciled** on startup per the `seed_agents()` lesson. |
| #31 (PR-B) | The check set | Liveness, heartbeat, freshness, app-up. The payoff PR — reads everything the previous four built. |
| #32 (PR-D) | `GET /api/status/full` | Parallel checks, runbook join, evidence join, auth-gated, no secrets. |
| #33 (PR-E) | Exception-first status page | The thing this session started from. Plus the Admin settings panel. |
| #34 (PR-F) | `self_whoami` + provenance + request log | "How are you feeling" answers from the same state the page renders. Migration 0020. |

Plus: `tasker-setup-and-recovery.md` updated.

### The two corrections that made it honest

**1. The PR-0 epoch floor.** Every `actions_audit` row before deploy `9855a28`
(2026-07-19T19:09:19Z) is `ok` by construction, not by outcome. Unwindowed,
liveness would read 17 days of fabricated green as evidence of health — the exact
false-green the "no evidence → `unknown`" rule exists to prevent. Liveness now
floors its lookback at the epoch.

Validated against real prod data: **12 truthful rows feed liveness, not 551.**
Eight external APIs correctly read `unknown` ("no calls in 30d") instead of the
false `ok` they'd have shown off pre-epoch rows.

**2. Deferring rather than shipping hollow checks.** Secret-age (needs a Fly API
token in-container) and published-expiry (Google refresh tokens publish no real
expiry) were deferred rather than shipped as perpetual `unknown`. Shipping them
would have been the TDD's own fabricated-countdown non-goal wearing a different
hat.

### First live catch

`location_pings: down` — last ping 163m ago, inside active hours, past the 60-min
threshold. The check independently corroborated, from the server side and knowing
nothing about Tasker, a diagnosis reached by hand the same morning. **The loop
did its job on live data before there was even a page to show it.**

---

## The Tasker finding — path closed, redesign needed

Half the day went here. The conclusion is worth more than the time it cost.

**Finding: Tasker on Pixel 9 (current version) does not appear in
Settings → Apps → Special app access → Alarms & reminders.** It therefore cannot
schedule exact alarms, so its scheduled profiles use inexact alarms that Android
defers indefinitely while idle.

**Symptom set — memorize this shape:** correct config, correct 15-minute context,
no fires, no errors, empty run log. Nothing visible is wrong. Manual runs work
perfectly.

Ruled out in order, each a real gotcha in its own right:

1. **Per-profile toggle** — separate from the Profiles-tab "1 of 1 enabled"
   count, which is misleading. Flipping it produced one ping, which looked like a
   fix and wasn't (it fires on enable, with the screen on, when doze isn't
   active).
2. **Battery** — "Allow background usage" on; Unrestricted already set. Not it.
3. **Monitor check intervals** — `All Checks Seconds` was at 600 from yesterday's
   diagnostics, well above a 15-minute context's needs. **These settings
   interlock** (wifi min ≤ timeout − 15, check interval ≥ timeout), so they must
   be reverted bottom-up — you cannot undo one without the others, which is how a
   half-reverted debugging session leaves you stuck. Fixed, still no fires.
4. **Version** — 6.6.20 → updated to current. Permission still absent.

**Meta-lesson: diagnostic settings from a debugging session are themselves a
failure mode.** Yesterday's changes actively broke today's diagnosis. Revert them
deliberately, as a checklist, at the end of the session that made them.

### The direction: invert the location schedule

Phone-side scheduling has no reliable path on this device. The answer is
**JARVIS pulls location on a schedule it controls**, rather than the phone
remembering to push.

Why this is right and not just a workaround:
- The server-side scheduler now has a heartbeat, catch-up, and hot reschedule —
  built today, observable, version-controlled.
- Tasker's role shrinks from "remember to fire every 15 minutes" (which it
  demonstrably cannot do) to "answer when asked" (which it does reliably, as
  every manual run proved).
- The trigger becomes external, removing the entire class of failure rather than
  working around it.

Likely a push notification or inbound trigger the phone reacts to. **This is a
TDD, not a settings change.** Design it fresh, not at the end of a long day.

Rejected: a `Wait 15m` self-perpetuating loop with a re-light watchdog. The loop
is *less* reliable, not more — a deferred alarm fires late, a killed loop stops
forever, and any phone-side watchdog inherits the phone's reliability, which is
the thing that failed. The re-light idea concedes the point.

---

## The `OutboundCall` thread — resolved, and a real gap logged

Chased a suspicion that JARVIS was autonomously scheduling recurring calls for
herself. **She wasn't.** The audit trail settled it:

```
19:57:02  delegate → secretary      "Check the status of the standing daily morning b[riefing]"
19:58:10  secretary:call_me_back    → created OutboundCall id=16 (4 AM PDT)
19:58:10  secretary:add_task        "Re-queue tomorrow's 4:00 AM Pacific briefing call"
19:58:14  delegate → secretary      "Set up a standing recurring daily call-back"
```

Mid-conversation, on a phone call with Matt, JARVIS found the cron unreliable and
hand-rolled a substitute out of the one-shot `call_me_back` primitive — adding a
task to re-queue it because she knew it couldn't recur. **Working around a real
gap, in the open, not acting behind anyone's back.** The workaround existed
*because* R3 didn't. Now it does.

Cleaned up: id=16 cancelled, the re-queue task and the 554 watch closed. Row id=4
(a `failed` briefing from Jul 13) left in place — deleting failure history from a
table we now read for health is the wrong instinct.

**Process lesson:** the first explanation offered was "deploy artifact." Plausible,
tidy, and wrong — it would have closed the question incorrectly. The query was
cheap. Run it.

### R22 — quiet-hours exemption keys on `kind`, not provenance

*(Numbered R22; R21 is already taken by snapshot-pile cleanup in the 07-15 doc.)*

`("callback","briefing")` are exempt from quiet hours because the *assumption* is
a human asked. Nothing enforces that assumption. An agent-minted `callback`
inherits an exemption designed for someone else's intent.

Bounded correctly: `outbound_calls_enabled` and `max_outbound_calls_per_hour`
enforce at **dial** time, so this isn't unlimited calls. The hole is calls that
ring *through* quiet hours, backstopped only by a rate cap — and a rate cap was
never a safety boundary, just a runaway-loop guard.

**Fix shape:** carry provenance on `OutboundCall` (user-requested vs.
agent-initiated); extend the quiet-hours exemption only to genuinely
user-requested callbacks.

**Priority: Medium.** Real, worth fixing, **not observed being abused** — the row
found was user-adjacent, created mid-conversation.

**The pattern is the valuable part:** a safety exemption riding on a proxy
(`kind`) instead of the real property (who initiated it). Same theme as the
existing `netstatus.py` note about "destructive" needing its own flag rather than
riding on notional value. Same error twice — name it so it's findable a third
time.

---

## OPEN — pick up next, roughly in priority order

### 1. Location schedule inversion (new TDD)
The Tasker path is closed. Design JARVIS-pulls-location properly. Highest value
of anything remaining — it's the one live fault in the system.

### 2. Verify `location_pings` reporting stays honest
Until inversion ships, the check correctly reads `down`. Make sure that's
understood as a true reading of a dead push path, not a check defect.

### 3. R22 — provenance on `OutboundCall` (Medium)
Scoped above. Small, well-understood.

### 4. Secret-age check
Needs a Fly API token in-container. Threshold decision already made: **180d
`degraded`, 365d `down`**, configurable per component. Deliberately deferred, not
forgotten.

### 5. R7 — morning brief health section (exception-only)
The brief consumes the same check state the page does. Silent when green.

### 6. PR-C — real network / tailnet checks
Needs an on-LAN session. Network components are seeded as `unknown` with no
fixture liveness — deliberately, so they can't read permanently fake-green.
Open question still: which nodes are load-bearing (`down` = real fault) vs.
informational (a sleeping iPad is not a degraded system).

---

## PARKED / captured

- **Test results in Postgres** — designed in `design-note-test-architecture.md`,
  separate build. An endpoint that *runs* the suite stays rejected: arbitrary
  code execution on an internet-facing app that can book flights and send email.
- **R19 — push alerting.** Deferred until the false-positive rate is known.
  v1 is pull-only by design.
- **R17 — multi-repo provenance.** Parked.
- **Duffel live-mode activation** — production access, prepaid balance on a
  low-limit card, `DUFFEL_LIVE_API_KEY`, then `BOOKING_ENABLED=true`. Owner
  action, gated.
- **Tasker diagnostic reverts** — Force High Accuracy off, Continue Task After
  Error off, delete the `err=` flash, clear stray tasks from the house project.

---

## Decisions made today (so they don't get relitigated)

- **Merge-on-green authorized** for code PRs in a build order. Gate behavior,
  secrets, and outward-facing switches (`BOOKING_ENABLED`, outbound toggles)
  still come to Matt first.
- **Request log retention: time-based, 90 days**, with a row-count safety valve
  so a runaway loop can't fill the disk before the sweep runs. Time is policy;
  count is backstop. (Write cost measured: ~4ms, off the voice critical path.)
- **Heartbeat staleness: 300s**, seeded as `component.check_config` data, not a
  code constant. The tick is ~5s but `_briefing_tick` runs after
  `process_available`, so a heavy LLM job can legitimately delay a beat.
- **Admin settings panel folded into PR-E** rather than built standalone and
  rebuilt three PRs later.
- **Evidence excludes tool arguments** — they can carry addresses, phone numbers,
  flight details. Asserted in test, not left as an implementation property.

---

## The one-line status

The self-health loop is complete and live: JARVIS can now detect her own faults,
join them to stored runbooks and real evidence, surface them exception-first on a
polling page, and talk about them in chat and on voice — all from one source of
state. The one open fault is real, correctly reported, and needs a design session
rather than more debugging.
