# DIAGNOSTIC — Location fixes stale again (2026-08-03)

**For:** Builder (Claude Code, production DB + `fly logs`)
**From:** Planner
**Type:** **Diagnostic only. Read-only. No code changes, no setting flips, no
secret re-sets, no phone changes until Step 1 names the layer.**
**Supersedes:** `docs/BUILD-ORDER-location-pull-silence-diagnostic.md` (2026-07-31)
for this incident. That order's SQL is wrong — see §0.2 — and it predates the
late-answer finding that makes Step 1 below decisive.

---

## §0 — What is different this time

### 0.1 We already know the previous fault

The 07-31 → 08-01 investigation landed on **`answering_late`**: the phone was
answering roughly 50 minutes late against a 120-second timeout, ~26% fulfilment,
bimodal latency. Recorded in `docs/design-note-answering-late.md`.

So the question today is **not** "why are fixes stale." It is narrower and it
splits three ways:

1. **Same fault, still running** — the phone is still answering late and nothing
   has changed. Boring, and the answer is power management on the Pixel.
2. **Degraded to silence** — the phone has stopped answering *at all*. Different
   layer, different checklist.
3. **Moved upstream** — JARVIS stopped asking, or the relay stopped accepting.
   Nothing to do with the phone.

Step 1 separates all three in one query. **Do not assume (1) because it was true
last week.** The prework's own warning applies: the phone was on clean cadence the
day before the last outage, so "same as last time" has already been wrong once.

### 0.2 The 07-31 order's query would have errored

It reads `FROM location_request`. The tables are **`location_requests`** and
**`location_pings`** — both plural, confirmed against `0021_location_request.py`
and `0009_location.py`. Use the query below, not that one.

### 0.3 The trap: `responded_at` is NULL for late answers

Confirmed by live-bytes read this morning. `close_request` only writes
`responded_at` on the `pending` branch; a ping that arrives after its request
swept to `timeout` is persisted and correlated via `LocationPing.request_id`, but
`responded_at` stays NULL.

**Therefore: do not measure latency from `responded_at`.** It will read as "never
answered" for every late answer, which is exactly the fault we are trying to
detect. Join through `location_pings` instead. Step 4a of the freshness build
order fixes this going forward; today you read around it.

---

## The architecture, so this is targeted rather than a fishing trip

```
worker tick → due_for_pull(db) → new_request(db) → autoremote.request_location(nonce)
   [L1/L2]        [L1/L2]            [L3 dispatch]         [L3 FCM]
     → FCM → phone Tasker matches nonce → phone POSTs /api/location → record_ping
                        [L4 phone]                    [L4 → L1 ingest]
```

- **L1 enablement** — `location_pull_enabled` (runtime setting, flippable without deploy)
- **L2 timing** — `in_active_hours` + `location_pull_interval_minutes`
- **L3 dispatch** — `new_request` commits the row, then POSTs via AutoRemote FCM;
  `relay_accepted` records whether the relay took it (**not** whether the phone
  received it — that leg is unobservable from here)
- **L4 phone** — Tasker profile, nonce filter, doze/exact-alarm

---

## Step 1 — The one query that splits the tree

Production DB: `fly postgres connect --app jarvis-db2 --database jarvis_mdk`
(prompt `jarvis_mdk=#` means ready; `jarvis_mdk-#` means you are mid-statement and
need a `;`).

```sql
SELECT r.id,
       r.trigger,
       r.status,
       r.requested_at,
       r.relay_accepted,
       p.id                                                   AS ping_id,
       p.created_at                                           AS ping_at,
       ROUND(EXTRACT(EPOCH FROM (p.created_at - r.requested_at))) AS latency_s
FROM location_requests r
LEFT JOIN location_pings p ON p.request_id = r.id
WHERE r.requested_at > now() - interval '24 hours'
ORDER BY r.id DESC;
```

And the unsolicited side, since a manual force-run carries no nonce and would not
appear above:

```sql
SELECT id, created_at, source, trigger, request_id, label
FROM location_pings
ORDER BY id DESC
LIMIT 20;
```

### Interpretation — the whole diagnosis is in the first result set

| What you see | Layer | Go to |
|---|---|---|
| **No `scheduled` rows in the stale window** | L1/L2 — JARVIS never asked | Step 2 |
| **`scheduled` rows, `relay_accepted = false`** | L3 — dispatch/relay | Step 3 |
| **Rows exist, `relay_accepted = true`, `ping_id` NULL throughout** | L4 — phone silent | Step 4 |
| **Rows exist, `ping_id` populated, `latency_s` >> 120** | L4 — phone late (**same fault as last week**) | Step 4 |
| **`ping_id` populated with sane `latency_s`, but newest ping still old** | ingest/linkage | Step 5 |

**Report the rows verbatim before interpreting them.** The rows are the evidence;
the interpretation follows the rows, not the other way round.

Note the fourth line specifically: **a populated `ping_id` on a `timeout` row is
the late-answer signature.** That correlation is why this query is decisive and
the July one was not.

---

## Step 2 — If upstream (L1/L2). Read, do not change.

1. **`location_pull_enabled`** — effective value via `get_effective`. If false,
   **report it, do not flip it.** A setting that is off for a forgotten reason
   from a prior debug session has happened before with monitor intervals;
   re-enabling blind masks *why* it went off.
2. **Active-hours window** — `location_active_start_hour`,
   `location_active_end_hour`, and the current hour in `calendar_timezone`. A pull
   loop that stops exactly at the window boundary is working as designed. Check
   whether the last fix lines up with the edge before calling it a fault.
3. **The worker itself** — `fly logs --app jarvis-mdk`: is `_location_pull_tick`
   running? Did the **morning brief and the health cycle** also go quiet at the
   same time? If so the finding is the worker process, not the location logic, and
   that is a much bigger and much simpler answer.

---

## Step 3 — If dispatch (L3)

`relay_accepted = false` means JARVIS could not hand the request to FCM. Known
class: `AUTOREMOTE_KEY` carrying a literal `key=` prefix, or separator trouble.

- Pull the `autoremote.request_location` error from `fly logs`.
- Confirm `AUTOREMOTE_KEY` is present and bare. **Read its shape; do not re-set
  it.** Fly secrets are write-only after being set — rotating during diagnosis
  destroys the evidence and a re-set is owner-approval anyway.
- `relay_error` is value-free by design; if it names nothing useful, say so rather
  than inferring.

---

## Step 4 — If phone (L4)

This is off-repo. The Builder cannot fix the Pixel; the valuable output is
**proving** it is the phone so the owner is not chasing server-side ghosts.

**Which phone checklist depends on what Step 1 showed, and they are different:**

- **Silent** (`ping_id` NULL throughout) → **Tasker config.** Profile enabled;
  event filter regex `^[A-Za-z0-9_-]{22}$` with Use Regex ON and Exact Message
  OFF; task reads `%arpar1`; AutoRemote installed and receiving (test from the
  AutoRemote web console); location permission "Allow all the time". See
  `docs/tasker-setup-and-recovery.md`.
- **Late** (`ping_id` populated, `latency_s` >> 120) → **power management.**
  Battery optimization Unrestricted for Tasker *and* AutoRemote; doze allowlist;
  exact-alarm permission. Config is demonstrably fine — something answered.

If it is the late case, also report **what changed since the last clean cadence**:
OS update, app update, battery-saver toggle, a "optimize battery" prompt the phone
may have applied on its own. Do not default to "doze exemption lapsed" — confirm
it. The phone was on clean cadence the day before the last outage, which makes a
*change* more likely than a slow drift.

Additionally, quantify it: median and spread of `latency_s`, and how many of the
last 24h of requests answered at all. "26% fulfilment, median 47m" is a finding.
"It's late again" is not.

---

## Step 5 — If ingest/linkage (rare)

A ping arrived but `latest()` is still stale. Check:

- Is `/api/location` 200-ing the phone's POSTs, or 403-ing them? A rotated
  `LOCATION_TOKEN` that reached Fly but not the phone produces exactly this
  symptom and is a clean, boring cause. `LOCATION_TOKEN` rotated on 07-31.
- Nonce shape faults: flip `location_log_nonce` on **only if** Step 1 shows pings
  landing unlinked (`request_id` NULL on solicited pings). It gates a log line,
  never an action, so it is safe — but it is still a change, so it comes after the
  read, not before.

---

## Guardrails

- **Diagnosis before code and before settings.** Nothing gets flipped, rotated or
  reconfigured until Step 1 names the layer. The monitor-interval incident is the
  cautionary case: changing things mid-diagnosis left interlocked state that then
  needed its own revert.
- **`unknown` is a valid answer.** If the rows cannot confirm a layer, say
  unknown. No evidence never maps to green, and it never maps to a confident
  guess either.
- **Do not read latency from `responded_at`** (§0.3). Join the pings.
- **This is parallel to the freshness build order.** It touches no files. Keep the
  branches separate — a diagnostic finding must not ride the build PR.

## Report back

The Step 1 rows verbatim, the layer they point to, the single most likely cause
with its evidence, and — if L4 — **which** of the two phone checklists applies and
why. That last distinction is the entire reason the fault codes are being split;
today it is a human reading it instead of the health check, but the question is
the same one.
