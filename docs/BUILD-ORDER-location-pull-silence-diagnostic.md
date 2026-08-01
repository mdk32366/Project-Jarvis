# BUILD ORDER — Location pull silence (6h gap) diagnostic

**For:** Builder (Claude Code, live repo + production DB/logs)
**From:** Planner
**Type:** Diagnostic-only. **No code changes, no setting flips, no phone changes**
until the layer is identified. Report back after each numbered block.
**Context:** Last ping 48.4937, -122.6835 (±11m), ~6h old. No newer fix. Owner
changed nothing since yesterday's close-out. This is a live-state question, so it
goes to you against production, not to the Planner's memory of the repo.

---

## The architecture (so the diagnosis is targeted, not a fishing trip)

Server-pull, four layers, each able to break silently:

```
worker tick → due_for_pull(db) → new_request(db) → autoremote.request_location(nonce)
   [L1/L2]        [L1/L2]            [L3 dispatch]         [L3 FCM]
     → FCM → phone Tasker matches nonce → phone POSTs /api/location → record_ping
                        [L4 phone]                    [L4 → L1 ingest]
```

- **L1 enablement:** `location_pull_enabled` runtime setting (flippable without deploy).
- **L2 timing:** `in_active_hours` + `location_pull_interval_minutes` — pulls only
  fire inside the active-hours window.
- **L3 dispatch:** `new_request` commits a `LocationRequest` row *then* POSTs via
  AutoRemote FCM. Prior known-bug class here: `AUTOREMOTE_KEY` prefix / `=:=`
  separator silently dropping delivery.
- **L4 phone:** Tasker profile enabled, nonce regex filter matching, exact-alarm
  permission. Prior known-bug class: phone-side scheduling / filter mismatch.

---

## Step 1 — The one query that splits the tree

Read the `LocationRequest` table, last ~8 hours, production DB:

```sql
SELECT id, nonce, trigger, status, requested_at, relay_accepted
FROM location_request
ORDER BY id DESC
LIMIT 30;
```

Interpretation — this is the whole diagnosis in one read:

- **No `scheduled` rows in the last 6h** → failure is **upstream of dispatch (L1/L2)**.
  JARVIS never asked. Go to Step 2.
- **`scheduled` rows exist, all `pending` or `timeout`, no answering ping** →
  failure is **dispatch or phone (L3/L4)**. JARVIS asked; nothing answered. Go to Step 3.
- **`scheduled` rows exist AND a recent `fulfilled`/answered one, but `latest()`
  ping is still 6h old** → a *linkage/ingest* problem (the ping arrived but didn't
  record, or recorded without updating latest). Rare; go to Step 4.

**Report the actual rows before interpreting.** The rows are the evidence; the
interpretation follows them.

---

## Step 2 — If upstream of dispatch (L1/L2)

Read, do not change:

1. **`location_pull_enabled`** — its effective value via `get_effective`. If
   false, that's the finding — but **report it, do not flip it yet**. A setting
   that turned itself off (or was off since a prior debug session and never
   restored — that exact failure has happened before with monitor intervals) is
   worth understanding before re-enabling, because re-enabling blind can mask
   *why* it went off.
2. **Active-hours window** — `location_active_start_hour`,
   `location_active_end_hour`, and what hour it is *now* in `calendar_timezone`.
   Confirm whether the 6h-ago last-fix lines up with the window's edge. A pull
   loop that stops exactly at the active-window boundary is working as designed,
   not broken — the question becomes whether the window is set where you want it.
3. **The worker tick itself** — `fly logs -a jarvis-mdk` for the `worker` process:
   is `_location_pull_tick` running at all? Is the worker process up? A dead
   worker stops pulls, watches, briefing, and the health cycle together — check
   whether *other* worker-driven things (morning brief, health) also went quiet
   ~6h ago, which would point at the process, not the location logic.

**Report:** effective `location_pull_enabled`, the active-hours window vs. now,
and whether the worker tick is alive.

---

## Step 3 — If dispatch or phone (L3/L4)

Requests are going out and dying. Split dispatch from phone:

1. **`relay_accepted` on the recent `scheduled` rows** (already in the Step 1
   query). This flag records whether AutoRemote *accepted* the dispatch POST.
   - **`relay_accepted=false`** → **L3 dispatch failure.** JARVIS couldn't hand
     the request to FCM. This is the `AUTOREMOTE_KEY` / separator class. Check
     `fly logs` for the `autoremote.request_location` error return, and confirm
     `AUTOREMOTE_KEY` is present and un-prefixed (no literal `key=`). **Do not
     re-set the secret yet** — read its shape first; a silently malformed secret
     is diagnosable without rotating it.
   - **`relay_accepted=true`** → dispatch worked, FCM accepted it. **The phone is
     the layer (L4).** The request left the building and nothing came back.
2. **If L4 (phone):** this is off-repo — Tasker on the Pixel. The checklist from
   `docs/tasker-setup-and-recovery.md`: profile enabled, the event filter regex
   `^[A-Za-z0-9_-]{22}$` with Use Regex ON / Exact Message OFF, exact-alarm
   permission, AutoRemote receiving. **Report that it's L4 and hand back to the
   owner** — the Builder can't fix the phone, and the owner has the recovery doc.
   The valuable output here is *proving* it's the phone, so the owner isn't
   chasing server-side ghosts.

**Report:** `relay_accepted` state, and therefore dispatch-vs-phone. If dispatch,
the logged error. If phone, say so plainly and point at the recovery doc.

---

## Step 4 — If linkage/ingest (rare)

A ping came back but `latest()` is still stale. Check `record_ping` committed, and
whether the `/api/location` route 200'd the phone's POST (auth header / shared
secret intact — a rotated `LOCATION_TOKEN` would 401 the phone silently). The
`LOCATION_TOKEN` rotated on 07-31 per the close-out — **confirm the phone has the
value the server now expects.** A token rotation that updated Fly but not the
phone is a clean, boring cause that produces exactly this symptom.

**Report:** whether the ingest route is rejecting the phone's POST, and whether
the 07-31 `LOCATION_TOKEN` rotation reached the phone.

---

## Guardrails

- **Diagnosis before code, and before settings.** No flip of
  `location_pull_enabled`, no secret re-set, no phone change until Step 1 names the
  layer. The prior monitor-interval incident is the cautionary case: changing
  things during diagnosis left interlocked state that then needed its own revert.
- **`unknown` is a valid answer.** If the data can't confirm a layer, say unknown —
  don't assume green. A 6h-old fix is exactly the "stale reads as unknown, not
  trusted" case the location module was built around.
- **This is parallel to the arc.** Code is mid-flight on TDD #3 steps 3–4 — this
  diagnostic doesn't touch that PR's files. Keep them separate; don't let a
  location fix ride the arc branch.

## Report back

The Step 1 rows verbatim, the layer they point to, and the single most-likely
cause with its evidence. That's enough to decide whether the fix is a setting, a
secret, or a phone — and which of those is gated (a secret re-set is
owner-approval; a setting flip we decide together once we know *why* it was off).
