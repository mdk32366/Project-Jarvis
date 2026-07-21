# TDD — Location Schedule Inversion (JARVIS pulls, phone answers)

**Status:** Draft, ready to build
**Date:** 2026-07-21
**Supersedes:** the phone-side scheduling approach in `tasker-setup-and-recovery.md`
**Depends on:** PR #29 (minute-tick enqueuer, heartbeat, catch-up), PR #30/#31
(relational health model + check set), PR #28 (runtime settings overlay)

---

## 1. Problem

The 15-minute location push is dead in production and correctly reported as such
by the health loop (`location_pings: down`, first live catch, 2026-07-19).

Root cause, established 2026-07-19: **Tasker on Pixel 9 (current version) does not
appear in Settings → Apps → Special app access → Alarms & reminders.** It cannot
hold the `SCHEDULE_EXACT_ALARM` permission, so its scheduled profiles fall back to
inexact alarms, which Android defers indefinitely while the device is idle.

Symptom shape, worth memorizing: correct config, correct 15-minute context, no
fires, no errors, empty run log, manual runs perfect. Nothing visible is wrong.

Ruled out and not to be relitigated: per-profile toggle, battery optimization,
monitor check intervals, Tasker version. All four were checked; each was a real
gotcha; none was the cause.

**Phone-side scheduling has no reliable path on this device.** The fix is not a
better phone-side schedule. It is to stop asking the phone to remember.

---

## 2. Goals

1. Location fixes arrive on a **server-controlled** 15-minute cadence during
   active hours.
2. Tasker's responsibility shrinks to **"answer when asked"** — the one thing it
   demonstrably does reliably.
3. A missing fix is **attributable**: server-fault and phone-fault are
   distinguishable from stored state, not inferred.
4. **On-demand pull**: JARVIS can request a fresh fix mid-conversation when the
   last one is stale.

## 3. Non-goals

- Building a companion Android app. AutoRemote already is one.
- Continuous / high-frequency tracking. 15 minutes during active hours is the
  requirement; nothing here should make a tighter cadence tempting.
- Retaining the phone-side **timed** profile. It does not work; keeping it would
  produce ambiguous pings and reintroduce the failure class. (A **manually run**
  task is explicitly *not* covered by this non-goal — see §6.6. It cannot silently
  fail, because it only runs when pressed.)
- Push alerting on location faults. (R19 remains deferred; v1 is pull-only.)

---

## 4. Design

### 4.1 Mechanism

**AutoRemote message → Tasker Event profile.**

AutoRemote delivers via FCM as a high-priority message. Android delivers
high-priority FCM **through doze** — precisely the delivery guarantee that exact
alarms would have provided and that this device cannot grant Tasker. The trigger
becomes external to the phone, which removes the entire failure class rather than
working around it.

```
scheduler minute-tick (existing, PR #29)
  └─ location_pull job — every 15 min, active hours only
       ├─ mint nonce, INSERT location_request (status=pending)
       └─ POST https://autoremotejoaomgcd.appspot.com/sendmessage
            key=<AUTOREMOTE_KEY>
            message=jarvis_locreq=:=<nonce>
              └─ [phone] Tasker Event → AutoRemote Message, filter "jarvis_locreq"
                   ├─ Get Location v2 (timeout 30s)
                   └─ HTTP Request POST /api/location
                        { lat, lon, accuracy, source: "tasker",
                          nonce: %arpar1, trigger: "pull" }
                          └─ server: INSERT location_ping (request_id),
                                     UPDATE location_request → fulfilled
```

### 4.2 Alternatives rejected

| Option | Why not |
|---|---|
| Direct FCM from JARVIS | Requires a companion app to hold the token and handle the message. AutoRemote is that app, already built and maintained. |
| Tasker HTTP server / inbound over Tailscale | Phone must hold a listening socket. Doze kills it. Same failure class we are closing. |
| `Wait 15m` self-perpetuating loop + re-light watchdog | Already rejected 07-19 and the rejection stands. A deferred alarm fires late; a killed loop stops forever; a phone-side watchdog inherits the phone's reliability, which is the thing that failed. |
| Join (same author, similar API) | Viable as a drop-in substitute if AutoRemote proves flaky. Keep as noted fallback, not initial choice — AutoRemote's message-filter model maps more directly onto a nonce. |

### 4.3 Why this is a capability upgrade, not just a repair

Two properties fall out of the inversion that the push design could never have:

**Attribution.** Push-only gave you "no ping" and no way to distinguish a dead
phone from a dead scheduler. A request record makes the fault addressable:
request sent at T with no response by T+120s means the phone is at fault; no
request sent at all means the scheduler is. These are different problems with
different fixes and they should never again share one signal.

**On-demand freshness.** The same primitive answers "where am I?" with a fix
taken *now* rather than up-to-15-minutes-stale. This only exists because the
server can initiate.

---

## 5. Data model

### 5.1 New table — `location_request`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `nonce` | text, unique, indexed | opaque; 16 bytes urlsafe. Not a secret — it is a correlator, and the token still authenticates. |
| `requested_at` | timestamptz, not null | |
| `responded_at` | timestamptz, null | |
| `status` | enum | `pending` / `fulfilled` / `timeout` |
| `trigger` | enum | `scheduled` / `on_demand` |
| `dispatch_ok` | bool | did the AutoRemote POST return 200 |
| `dispatch_error` | text, null | transport failure detail, no key material |

### 5.2 Modified table — `location_ping`

Add `request_id` int FK → `location_request.id`, **nullable**. Nullable is
deliberate: manual force-runs and any legacy push remain valid pings, they simply
carry no correlation. An unsolicited ping is data, not an error.

Add `trigger` text, nullable — `pull` / `manual`, echoed from the client. This is
descriptive only: it records how a fix arrived. **No health check reads it.**
Attribution lives on `location_request` (§7), which is the point of the
inversion; a client-supplied field must never be load-bearing for health, because
the client is the thing whose reliability is in question.

### 5.3 Migration

`0021_location_request.py` — create table, add nullable FK and nullable `trigger`
to `location_ping`, add index on `location_request.nonce` and on
`(status, requested_at)`.

No backfill. Historical pings keep a null `request_id` honestly.

---

## 6. Server changes

### 6.1 AutoRemote client — `backend/integrations/autoremote.py`

`send(message: str) -> tuple[bool, str | None]`

- POST form-encoded to the AutoRemote sendmessage endpoint.
- 10s timeout. Single retry on connection error only, not on non-200.
- **Never log the key.** Log the message payload and the response status.
- Returns `(dispatch_ok, error)`. The caller records both.

### 6.2 Scheduler job — `location_pull`

Registered on the **existing minute-tick enqueuer** from PR #29. Do not add an
APScheduler cron; that path was deliberately replaced.

Runs when all are true:
- `location_pull_enabled` (runtime setting, via `get_effective` — not `settings.X`)
- current local time is inside active hours (reuse the existing active-hours
  helper the freshness check already uses; do not re-derive it)
- minutes-since-last-`scheduled`-request ≥ `location_pull_interval_minutes`
  (default 15)

Catch-up semantics: the enqueuer's missed-run catch-up means a deploy or restart
across a slot produces **one** make-up request, not a burst. Assert this in test —
a burst of pulls is a battery event on the phone, which is exactly the kind of
side effect that erodes trust in the system.

Its own SQLAlchemy session. This is a background job, not request-scoped.

### 6.3 Timeout sweep

Same tick. Any `pending` request older than `location_pull_timeout_seconds`
(default 120) → `timeout`. This is what makes the responsiveness check readable;
without it, `pending` rows accumulate and nothing is ever false.

### 6.4 `/api/location` — accept and close

- Accept optional `nonce` and `trigger` alongside the existing forgiving
  lat/lon/accuracy handling (JSON, form-encoded, or query params — keep all three).
- If `nonce` present and matches a `pending` request: set `responded_at`,
  `status=fulfilled`, link the ping.
- If `nonce` present but already `fulfilled` or `timeout`: **still record the
  ping**, log at info, do not error. A late answer is a real location fix. It
  stays linked so a chronically-late phone shows up as `timeout` in
  responsiveness while the fix itself is still usable.
- If `nonce` absent: record the ping with null `request_id`, storing `trigger` as
  supplied (`manual`, or null for legacy clients). Unchanged behavior otherwise.
  An unsolicited ping is always accepted — it is a real fix.
- Token auth (`X-Jarvis-Token`) unchanged and still the only authentication. The
  nonce is a correlator, never a credential.

### 6.5 On-demand pull tool

`request_location_fix()` — available to the orchestrator.

- Mints an `on_demand` request, dispatches, polls for `fulfilled` up to ~20s.
- On fulfilment: return the fresh fix.
- On timeout: return the last known fix **with its age stated plainly**, plus the
  fact that the phone did not answer. Never present a stale fix as current, and
  never fabricate. This is the same discipline as the deferred secret-age check —
  no invented freshness.
- Not gated. It is a read, it is idempotent-ish, and its only cost is one FCM
  message and a GPS fix.

### 6.6 Manual push (retained fallback)

A Tasker task with **no profile** — run by hand or from a home-screen shortcut.
Posts a fix with **no nonce** and `trigger: "manual"`.

**Use case:** pre-seeding position before initiating a conversation, so JARVIS
already knows where Matt is rather than spending the ~20s round trip of §6.5
finding out. Cheap, deliberate, and under direct control.

**Why this is retained where the timed profile is not.** The timed profile's
defect was silent non-firing: it claimed a guarantee it could not keep, and its
pings were indistinguishable from healthy ones. A manually run task claims
nothing. It fires when pressed or not at all, and the failure is immediately
visible to the person pressing it. The rejected thing was the false guarantee,
not the phone-side task.

**Containment — the property that makes it safe:** responsiveness (§7.2) reads
`location_request` fulfilment, **not** ping recency. A manual push creates no
request. It is therefore structurally incapable of masking a phone that has
stopped answering pulls. This is the direct payoff of retiring the freshness-only
check rather than merely supplementing it, and it must be asserted in test (§11),
not left as an implementation property.

---

## 7. Health check changes

The current single `location_pings` freshness check reads one signal for two
possible faults. Split it. This is the whole attribution argument made concrete.

### 7.1 `location_pull_scheduler` — *is the server asking?*

Reads: most recent `location_request` with `trigger=scheduled`.

- `ok` — a scheduled request within the last `interval + 5` minutes, or outside
  active hours
- `down` — inside active hours, no scheduled request within `interval * 2`
- `unknown` — no requests at all, ever

Remediation: check `location_pull_enabled`, check scheduler heartbeat, check
`dispatch_error` on the most recent row.

> **Scope limit — read this before trusting a green here.** This check's
> `dispatch_failing` fault keys on `dispatch_ok`, which records only that the
> AutoRemote relay returned 200. It cannot see the relay→FCM→phone leg. A silent
> delivery failure reads **green** on this check while §7.2 correctly goes `down`.
> When responsiveness is `down` and this check is `ok`, the phone-side runbook is
> **not** automatically the right place to look. See §12.

### 7.2 `location_responsiveness` — *is the phone answering?*

Reads: trailing 6 requests (scheduled + on_demand), completed only.

- `ok` — ≥ 5 of 6 fulfilled
- `degraded` — 3–4 of 6
- `down` — ≤ 2 of 6
- `unknown` — fewer than 3 completed requests. **Not `ok`.** Honors the
  "no evidence → unknown" rule; a fresh deploy has no basis for a green.

Remediation runbook: AutoRemote installed and receiving; Tasker Event profile
enabled; Tasker location permission set to *Allow all the time*; battery
Unrestricted; test with a manual AutoRemote send from the web console.

### 7.3 Retire the old check

Remove the freshness-only `location_pings` component after the two above are
seeded and reading. Reconcile in `seed_agents()`-style startup reconciliation —
**tools and descriptions both**, per the standing lesson. A stale description on a
retired component is exactly the kind of thing that breaks routing later.

Until this ships, `location_pings: down` is a **true reading of a dead push
path**, not a check defect. Open item #2 from the 07-19 closeout is hereby
answered rather than merely acknowledged.

---

## 8. Phone configuration

Deliberately last. Steps 1–3 of the build order are fully testable server-side
with a stub, and yesterday's cost was largely phone-side blind debugging.

1. Install **AutoRemote**, register the device, capture the personal key.
2. New Tasker project **JARVIS Location Pull**.
3. **Profile:** Event → Plugin → AutoRemote → Message, filter `jarvis_locreq`.
   (Event, not Time. There is no schedule on the phone anymore. This is the
   entire point.)
4. **Task JARVIS Answer Location:**
   - Get Location v2, timeout 30s
   - HTTP Request → POST `https://jarvis-mdk.fly.dev/api/location`
     - Header `X-Jarvis-Token: <token>`
     - JSON body:
       `{"lat":%LOC_LAT,"lon":%LOC_LON,"accuracy":%LOC_ACC,"source":"tasker","nonce":"%arpar1","trigger":"pull"}`
5. **Task JARVIS Push Location (manual)** — no profile attached. Same two actions
   as above, but the body carries **no nonce** and `"trigger":"manual"`:
   `{"lat":%LOC_LAT,"lon":%LOC_LON,"accuracy":%LOC_ACC,"source":"tasker","trigger":"manual"}`
   Add a home-screen shortcut/widget for it. Duplicate the task rather than
   sharing one with a conditional — less clever, and readable in six months.
6. **Delete the old timed profile and its task.** The manual task above replaces
   its only useful property. Nothing that claims to fire on a schedule and
   doesn't stays on the phone.
7. Verify **both paths**: trigger a pull from the server → watch for
   `location ping` in `fly logs`, confirm the request row goes `fulfilled`. Then
   run the manual task → confirm a ping lands with null `request_id` and
   `trigger=manual`, and that no `location_request` row was created.
8. **Export the project**, scrub the token to `REPLACE_WITH_LOCATION_TOKEN`,
   commit as `devices/jarvis-location-pull.prj.xml`, then **paste the real token
   back into Tasker after the commit.** Order matters. This is the same sequence
   that was pending from the previous Tasker work.

### 8.1 Diagnostic reverts — do these now, as a checklist

Carried from 07-19 and still owed. Yesterday's diagnostic settings actively broke
the same day's diagnosis; that is the meta-lesson and it costs nothing to honor.

- [ ] Force High Accuracy → off
- [ ] Continue Task After Error → off
- [ ] Delete the `err=` flash action
- [ ] Delete stray tasks from the house project
- [ ] Tasker battery → Unrestricted (confirm, do not assume)
- [ ] Monitor check intervals → reverted **bottom-up**; they interlock
      (wifi min ≤ timeout − 15, check interval ≥ timeout), so a partial revert
      leaves you stuck

---

## 9. Secrets & settings

**New Fly secret:** `AUTOREMOTE_KEY`.

Generate on desktop, paste on desktop. Never transcribe from a phone screen —
standing lesson on visually ambiguous character sets (`0/O/1/l`). Fly secrets are
write-only after being set; the original is unrecoverable, so record it in the
password manager at creation time, not later.

**New runtime settings** (DB, read via `get_effective`, seeded not hardcoded):

| Setting | Default |
|---|---|
| `location_pull_enabled` | `true` |
| `location_pull_interval_minutes` | `15` |
| `location_pull_timeout_seconds` | `120` |
| `location_active_hours_start` / `_end` | reuse existing active-hours values |

---

## 10. Build order

| # | Work | Testable without phone |
|---|---|---|
| 1 | Migration 0021, models, `location_request` | ✅ |
| 2 | AutoRemote client + `/api/location` nonce close-out | ✅ (stub dispatcher) |
| 3 | Scheduler job + timeout sweep on the minute-tick | ✅ |
| 4 | Health check split; retire `location_pings` | ✅ (fixtures) |
| 5 | Phone: AutoRemote + Event profile + export/scrub/commit | ❌ |
| 6 | `request_location_fix()` on-demand tool | ✅ then live |

Merge-on-green authorized for 1–4 and 6 per the standing decision. Step 5 is
owner action.

---

## 11. Test plan

- **Nonce close-out** — pending → fulfilled; `responded_at` set; ping linked.
- **Late answer** — nonce arrives after `timeout`; ping is recorded, request stays
  `timeout`, no 4xx.
- **Unsolicited ping** — no nonce; recorded with null `request_id`.
- **Manual push is recorded** — no nonce, `trigger=manual`; ping stored, null
  `request_id`, **no `location_request` row created**.
- **Manual push cannot mask an unresponsive phone** — seed 6 `timeout` requests,
  then post a manual ping; assert `location_responsiveness` still reads `down`.
  This is the containment property from §6.6 and the whole basis on which the
  fallback was retained. Assert it explicitly.
- **Catch-up is not a burst** — simulate a 3-slot outage; assert exactly one
  make-up request.
- **Active hours** — no scheduled requests outside the window; scheduler check
  reads `ok` not `down` during that window.
- **Responsiveness thresholds** — 6/6, 4/6, 1/6, and 2-completed each map to
  `ok` / `degraded` / `down` / `unknown`.
- **Dispatch failure** — AutoRemote returns 500; `dispatch_ok=false`, error
  recorded, request still sweeps to `timeout`, scheduler check surfaces it.
- **No key in logs** — assert the secret never appears in log output or in
  `dispatch_error`. Asserted in test, not left as an implementation property —
  same standard as the evidence/tool-arguments exclusion.

---

## 12. Open questions

- **`dispatch_ok` cannot see the leg it appears to describe.** It records that the
  AutoRemote relay returned 200 — nothing about whether FCM delivered, whether the
  phone was reachable, or whether Tasker ever saw the message. This is what §5.1
  asked to be recorded, not an implementation gap, but the spec asked for the
  wrong thing.

  **Consequence, precisely.** `location_responsiveness` is unaffected: it scores
  request *fulfilment*, never dispatch, so a phone that never receives the nudge
  still produces `timeout` rows and the check goes `down` within six requests.
  Nothing goes undetected. But `location_pull_scheduler` keys its
  `dispatch_failing` fault on `dispatch_ok`, so during a delivery failure it reads
  **green**, and the operator is sent to the phone-side runbook — AutoRemote
  installed? profile enabled? battery unrestricted? — for a fault in the
  relay→FCM leg, which is neither the server nor the phone. **This is the
  misattribution the inversion was built to eliminate, reappearing one layer
  down.**

  **Not yet distinguishable in production.** All requests currently read `timeout`
  with `dispatch_ok=True`, which is equally consistent with "no Event profile
  exists yet" (the expected explanation) and "delivery is silently failing." Only
  a fulfilled request separates them. **If responsiveness stays `down` after the
  phone is configured and the Event profile is confirmed firing on a manual
  AutoRemote send, suspect this first, not last.**

  **Honest fixes, in order of preference:** rename to `relay_accepted`, which
  claims exactly what is known and stops the scheduler check from overclaiming;
  or close the loop with an AutoRemote delivery receipt, if one is exposed.
  Do not leave a column named for delivery that measures acceptance.
- **AutoRemote reliability under prolonged doze.** FCM high-priority should
  deliver, but this device has already violated one reasonable expectation.
  Mitigation: the responsiveness check measures exactly this, so if the
  assumption is wrong the system says so within six requests rather than
  silently. Join is the noted substitute. Note the entry above: the *instrumentation
  cannot see this leg*, which is a distinct problem from the leg being unreliable
  and would mask which half to blame.
- **Interval during known-stationary periods.** Pulling every 15 minutes
  overnight at a dock costs battery for no information. Candidate follow-up, not
  v1 — and it should be driven by measured battery cost, not assumed.
