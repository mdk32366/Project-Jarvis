# BUILD ORDER — Location freshness, steps 1–4 (+4a)

**For:** Builder (Claude Code, live repo)
**From:** Planner
**TDD:** `docs/TDD-location-freshness-alert.md` — **with two ratified amendments,
recorded in §0 below. The TDD as written is wrong on both points; build this
order, not the TDD.**
**Scope:** TDD build-order steps 1, 2, 3, 4, 4a. Steps 5 (absence watch), 6
(`location_ping_log`), 7 (brief integration) are NOT in this order.
**Merge policy:** merge-on-green within this order. No gate behaviour changes, no
secrets, no outward-facing switches — nothing here needs separate approval.

---

## §0 — Two amendments to the TDD, already ratified

Read these before anything else. Both came out of a live-bytes read on 2026-08-03
and both change what you build.

### 0.1 §4.5's latency signal does not exist where the TDD says it does

The TDD assumed the ingest path might *drop* a ping whose request already swept
to `timeout`, and made step 4a contingent on that. **It doesn't drop it.**
`close_request` (`backend/app/handlers/location.py:118`) returns the row for a
`timeout` nonce, the ping is persisted, and `LocationPing.request_id` is set. Late
answers are kept **and** correlated already.

But `responded_at` is written **only on the `pending` branch**. On a late close it
stays NULL. So §4.5's stated distinguisher — `responded_at − requested_at` — reads
NULL in exactly the case it was written to diagnose.

**Amendment:** step 4a is no longer an ingest change. It is a one-line write in
`close_request` plus its guard (§4a below). `status` answers *did it arrive in
time*; `responded_at` answers *when it arrived*. Two facts, two fields. Collapsing
them is what left the field blank.

### 0.2 Fault codes move — `answering_late` goes on `location_responsiveness`

TDD §5 hangs all four fault codes on a new `location_freshness` component,
including `not_answering`. That code is **already live** on
`location_responsiveness` (`backend/app/health.py:223`) with the Tasker-config
runbook. Building §5 as written puts the same fault name on two components with
two runbooks that will drift — the dead-runbook defect in a new costume.

**Amendment, ratified:**

| Component | Fault codes | Reads |
|---|---|---|
| `location_responsiveness` (existing) | `not_answering`, **`answering_late`** (new) | `location_requests` fulfilment + latency |
| `location_freshness` (new) | `stale_during_active`, `never_pinged` | newest `location_pings` row age |

`location_responsiveness` is already the check that reads `LocationRequest`.
`answering_late` belongs to it. `location_freshness` is scoped to newest-ping-age
and nothing else.

### 0.3 TDD step 3 collapses into step 1

"Active-hours severity cap" is not a separate code path — it is a branch inside
`check_location_freshness`, the same shape `check_location_scheduler` already
uses. Build it as part of step 1. There is no step 3 deliverable.

---

## Step 0 — Confirm live state before writing anything

Standing doctrine; draft migration numbers went stale five times in the last arc.

1. `alembic heads` — expected `0029_plan_draft_status`.
2. Confirm `main` is at `d199a10` or later.
3. Confirm `git status` is clean.

**Expected: no migration in this order.** Settings, components and remediations
are all existing tables reconciled by `seed_health_topology`. If you find a reason
a migration is needed, **stop and report** rather than authoring one — that would
mean an assumption in this order is wrong.

Report the head and move on.

---

## Step 1 — `location_stale_after_minutes` + `check_location_freshness`

### 1.1 The setting

`backend/app/config.py`, in the Location block (near `location_max_age_minutes`,
~line 165):

```python
location_stale_after_minutes: int = 30
```

**Comment it against the neighbour it will otherwise be merged with.**
`location_max_age_minutes` and `location_stale_after_minutes` share a default of
30 and will look redundant to the next reader. They are not:

- `location_max_age_minutes` = **don't trust this fix for navigation.** A
  consumer-side trust threshold. Answers "should I route from this position?"
- `location_stale_after_minutes` = **the feed has stopped.** A health threshold.
  Answers "has anything arrived recently at all?"

They tune in opposite directions for legitimate reasons — you might trust a fix
for 10 minutes while tolerating a 60-minute feed gap. Say so in the comment, or
someone will collapse them into one.

`backend/app/runtime_settings.py`, in the location block (~line 60):

```python
"location_stale_after_minutes":    _Key("int", min=5, max=1440),
```

Floor 5 because anything tighter flags normal jitter; ceiling 1440 because a
threshold longer than a day cannot fire inside an active-hours window anyway.

### 1.2 The check

`backend/app/health_checks.py`, alongside its two siblings:

```python
def check_location_freshness(db: Session, c: Component) -> CheckResult:
```

Behaviour, mirroring `check_location_scheduler`'s established shape:

- `latest(db)` returns None → **`unknown` / `never_pinged`**. No evidence is not
  health. Mirrors `no_requests` on the scheduler check — `unknown` status carrying
  a fault code is the existing pattern, not a new one.
- Outside active hours (`in_active_hours(db)` false) → **`ok`**, with the age
  stated as fact in the detail. An overnight gap while the phone charges is not a
  fault. Report always, escalate only in-hours (this is TDD step 3).
- In active hours, three tiers against `location_stale_after_minutes`:
  - `age <= stale_after` → `ok`
  - `age <= stale_after * 2` → `degraded` / `stale_during_active`
  - else → `down` / `stale_during_active`

Reuse `latest()` and `age_minutes()` from `app.handlers.location` — do not
re-derive either. Reuse `in_active_hours(db)` — its docstring already says it
exists so the freshness check and the pull loop cannot disagree about the window.

Detail line is fact only: `"newest fix 41m old (stale after 30m)"`. No judgment
words.

Register in `_CHECKS` (~line 455): `"location_freshness": check_location_freshness`.

### 1.3 Guard + plant

Tests in `backend/tests/test_health_checks.py`.

Cover: never-pinged → unknown; fresh in-hours → ok; stale in-hours → degraded;
very stale in-hours → down; stale **out of hours** → ok.

**Plant (required, per `design-note-unwatched-instruments.md`):** remove the
`in_active_hours` branch entirely and re-run. The out-of-hours test must go RED.
If it stays green your fixture is inside active hours by accident and the test is
an unwatched instrument — the suppression is doing nothing and you have not tested
it. Verify the patch applied (`assert t != original`) before interpreting the
result.

---

## Step 2 — Component row + runbooks

`backend/app/health.py`.

### 2.1 `_COMPONENTS` (next to its two siblings, ~line 92)

```python
{"name": "location_freshness", "kind": "data_feed", "depends_on": "",
 "check_type": "location_freshness",
 "description": "Is there a recent position fix at all?"},
```

### 2.2 `_REMEDIATIONS` — both codes, distinct runbooks

The join guard requires every fault code ship a runbook and no orphans, so this
lands complete or not at all.

- **`("location_freshness", "stale_during_active")`**, severity `warn` — run the
  tabled pull-silence diagnostic: which layer stopped (scheduler / dispatch /
  phone / ingest). Point at `docs/DIAGNOSTIC-location-stale-2026-08-03.md`.
  Deliberately does NOT name a cause: this check knows only that fixes stopped,
  and naming a layer it cannot see is the mis-route §4.3 of the TDD warns against.
- **`("location_freshness", "never_pinged")`**, severity `info` — first run: the
  phone has never been enrolled. Point at `docs/tasker-setup-and-recovery.md`.
  Expected on a fresh system; not a fault to chase.

### 2.3 Capability membership

Add to `_CAPABILITY_MEMBERS["location"]` (~line 409) as **non-primary**:

```python
("location_freshness", False),
```

Leave `location_responsiveness` primary. Freshness is arguably the more
end-to-end signal and there is a case for flipping primary — **do not flip it in
this order.** That is a Planner decision and it is not blocking. Note it in the PR
description if you agree it is worth raising.

### 2.4 Guard

Assert the new component seeds, both fault codes join a runbook, and no orphan
remediation exists for it. The existing runbook-join guard should cover the last
one — confirm it actually reaches the new rows rather than assuming it does.

---

## Step 4a — Write `responded_at` on a late close

**Build this BEFORE step 4. Step 4 reads what this writes.**

`backend/app/handlers/location.py`, `close_request`, the `else` branch (~line 133):

```python
else:
    if req.responded_at is None:      # first answer wins
        req.responded_at = datetime.now(timezone.utc)
    log.info("location ping answered request %s late (status=%s)", req.id, req.status)
```

**First answer wins** matters: a second late ping carrying the same nonce must not
overwrite the first arrival's timestamp, or the measured latency drifts upward
every time the phone retries.

`status` is untouched. The existing docstring already explains why a chronically
late phone must still read as unresponsive — keep that, and extend it to state the
new division of labour: **`status` = did it arrive in time; `responded_at` = when
it arrived.**

### 4a.1 The guard that matters most

Only two readers of `responded_at` exist today (`check_location_responsiveness`'s
`last_success_at`, and one test), and both scope to `fulfilled`, so this write is
safe now. The risk is the *next* refactor: once `responded_at` is populated on
`timeout` rows, `responded_at is not None` looks like a perfectly reasonable way
to count fulfilment, and it would be silently wrong.

Write a structural guard asserting the fulfilment rate in
`check_location_responsiveness` is computed from `status == "fulfilled"` and never
from `responded_at`.

**Plant:** change the fulfilment filter to `r.responded_at is not None` and re-run.
The guard must go RED. If it stays green, the guard is not reaching the fulfilment
path and you have found a coverage gap, not a pass.

### 4a.2 Extend the existing test

`test_late_answer_is_recorded_but_does_not_un_timeout` currently asserts `status`
stays `timeout` and the ping links. Add: `responded_at is not None` **while**
`status == "timeout"`. That pairing is the whole point of the change and nothing
asserts it today.

### 4a.3 No backfill in this order

Historical late answers still have NULL `responded_at`. **Do not backfill.** The
diagnostic order gets the same answer read-only by joining `location_pings`, and
reading beats writing when the question is "what happened." If a backfill turns
out to be wanted afterwards it is a separate, owner-approved, one-shot step.

---

## Step 4 — `answering_late` on `location_responsiveness`

`backend/app/health_checks.py`, `check_location_responsiveness` (~line 205).

### 4.1 Split the fault code

Today every non-ok result emits `not_answering`. Split it on the evidence:

- Of the window's **failed** rows (`status == "timeout"`), those with
  `responded_at is not None` **answered late**; those with NULL were **silent**.
- If late answers are the **majority of the failures** → fault `answering_late`.
- Otherwise → `not_answering` (unchanged).

Majority-of-failures deliberately, with **no new tunable**. A threshold nobody
ever tunes is a knob that rots; the counts go in the detail line so the reader
sees the real split regardless of which code fired.

Status tiers (`ok` / `degraded` / `down`) are **unchanged**. Only the fault code
and the detail line change. A phone answering 50 minutes late against a 120-second
timeout is still unresponsive — that was ratified when `close_request` was written
and this order does not reopen it.

### 4.2 The detail line

Must name both counts and the observed lateness as **fact**:

```
2 of the last 6 requests answered; 3 of 4 failures answered late (median 47m, timeout 120s)
```

Derive latency from `responded_at − requested_at`. Both are tz-aware via `_aware`;
SQLite hands back naive, so use the existing helper rather than comparing raw.

### 4.3 The runbook — different machine, different checklist

`("location_responsiveness", "answering_late")`, severity `warn`. **Power
management, not Tasker config:**

- Android battery optimization for Tasker and AutoRemote → Unrestricted
- Doze allowlist / "Allow background activity"
- Exact-alarm permission for Tasker
- Any OS update, battery-saver toggle or app update since the last clean cadence

State explicitly, in the runbook text, that this is **not** the `not_answering`
config checklist — if the config were wrong nothing would answer at all. Sending a
late-answer fault to the config runbook is precisely the mis-route this split
exists to prevent, and the runbook should say so, so a future reader does not
"helpfully" merge the two.

### 4.4 Guards + plants

Three tests: all-silent failures → `not_answering`; majority-late failures →
`answering_late`; mixed with silent in the majority → `not_answering`.

**Plant:** make the late/silent split read `status == "timeout"` alone (ignoring
`responded_at`), so everything reads late. The `not_answering` tests must go RED.
If they stay green, your fixtures never populate `responded_at` and all three
tests are measuring the same thing.

**Second plant, non-negotiable:** delete the `("location_responsiveness",
"answering_late")` remediation row and confirm the runbook-join guard fires. A
fault code that can be emitted with no runbook is the four-day-calendar-outage
failure, and it is the specific thing that guard exists for.

---

## Deploy note — the signal is honest but not instant

`responded_at` is NULL on every request already in the table. On deploy,
`check_location_responsiveness` will read `not_answering` for the current outage
until roughly `window` (6) new requests have cycled through, because the evidence
that would say otherwise was never recorded.

That is correct behaviour — no evidence falls to the conservative code rather than
guessing — but **it means the live outage will not self-diagnose on deploy day.**
Use the diagnostic order for today's question. Say this plainly in the PR
description so nobody reads the first post-deploy `not_answering` as a finding.

---

## Report back

Per step: what you changed, the plant you ran, and whether it went red. A plant
that stayed green is a finding, not a null result — work the table in
`design-note-unwatched-instruments.md` §1 and report before moving on.

At the end: `alembic heads` unchanged, full suite count, and confirmation that the
seed reconciled the new component and both remediation pairs on a fresh DB.
