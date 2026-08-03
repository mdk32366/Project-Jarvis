# BUILD ORDER — Location freshness, steps 5–7 (completes the TDD)

**For:** Builder (Claude Code, live repo)
**From:** Planner
**TDD:** `docs/TDD-location-freshness-alert.md`, build-order steps 5, 6, 7 — **with
one sequencing inversion and one design decision the TDD leaves open. Both in §0.**
**Follows:** PR #69 (`6f0b594`, steps 1–4) and PR #70 (`72d826c`).
**Merge policy:** merge-on-green. Nothing here changes a gate, a secret, or an
outward-facing switch. **One exception:** §5.4 seeds a watch that can place an
outbound call — read that section before seeding, not after.

---

## §0 — Read before starting

### 0.1 The TDD's step order is wrong. Build 6 before 5.

Step 5 seeds a `Watch` whose `tool` must resolve in the registry. `check_watch`
(`backend/app/handlers/watches.py:154`) does this:

```python
if not reg.has(w.tool):
    w.status = "error"
    w.error = f"tool {w.tool} no longer exists"
```

**`status="error"` is terminal.** Nothing sets it back to `active`. Seed the watch
before its tool is registered and the first worker tick bricks it permanently —
and it bricks *silently*, because an errored watch simply stops being due. The
dead-man's-switch would be dead on arrival and nothing would say so.

Build order in this document: **6 (tools) → 5 (watch) → 7 (brief)**.

### 0.2 `recurring` cannot express what step 5 needs. This is the decision.

The TDD asks for **fire once, then re-arm when a fresh ping restores health.**
The existing boolean offers neither:

| Setting | Behaviour | Why it fails here |
|---|---|---|
| `recurring=True` | Rings every `watch_min_interval_minutes` while stale | The nagging the TDD explicitly rejects. You disable it, and a disabled location alarm is how you get six hours stale again. |
| `recurring=False` | `status="done"` on first fire, forever | Fires once **ever**. The next outage never alerts. A dead-man's-switch that dies after one use. |

**Ratified: re-arm in the watch engine, keyed on the system watch, no migration.**

- Identity: `created_by == "system"` **and** `tool == "check_location_freshness"`.
  That pair is the stable identifier the TDD asks for. `created_by` already
  exists (`String(16)`, currently set from `ctx.channel`), so no schema change.
- Add `rearm_system_watches(db)` to `watches.py`, called from the worker tick
  **immediately before** the `due_watches` loop. It flips a `done` system watch
  back to `active` when the underlying condition has cleared.
- `recurring` stays `False`. The re-arm is what makes it repeatable; the
  one-shot semantics stay exactly as they are for every user watch.

**Rejected, and record why in the code:** re-arming from inside
`check_location_freshness`. A health check that mutates a watch row couples the
status surface to the alerting surface, and the next person to add a check would
reasonably copy the pattern. Checks read; the engine writes.

**Do not add a `rearm_on_clear` column.** One watch needs this. Generalising to a
schema change before a second case exists is speculative, and the migration is not
free — `created_by` already carries the distinction.

### 0.3 The re-arm's clear condition must not be the tool's prose

`rearm_system_watches` must read `check_location_freshness` **directly** (the
function, or `health_result` for `location_freshness`), not the LLM judge. The
judge is `_fired`, it fails closed, and routing recovery through it means a judge
hiccup leaves the watch permanently `done` — silent, and indistinguishable from
"no outage since." Recovery is a structural fact (`status == "ok"`); read it as one.

---

## Step 0 — Confirm state, and one read that resolves an open claim

1. `alembic heads` — expected `0029_plan_draft_status`. **No migration in this
   order.** If you find you need one, stop and report; that means §0.2 is wrong.
2. Confirm clean tree at `72d826c` or later.

### 0.3a The latency read — report, then continue (does not gate the build)

Two things are currently asserted in the repo on evidence that hasn't been
confirmed, and both are cheap to settle now that `responded_at` is being written:

```sql
SELECT id, trigger, status, requested_at, responded_at,
       ROUND(EXTRACT(EPOCH FROM (responded_at - requested_at))) AS latency_s
FROM location_requests
WHERE requested_at > now() - interval '12 hours'
ORDER BY id DESC;
```

**(a) Did the 08-01 fix actually work?** The appended diagnostic note says
bimodal → continuous is a success signature. That describes the distribution's
*shape*. It does not establish its *location* — a phone answering every request
uniformly 50 minutes late produces evenly-spaced arrivals and a unimodal
distribution, and would look identical from ping cadence alone. Three outcomes:

- Rows landing `fulfilled` → latency genuinely under 120s. **The note stands.**
- Rows landing `timeout` with `responded_at` populated → answering every time,
  still too late. **The fix changed the character of the lateness, not its
  presence.** Amend the note; the power-management checklist is still live.
- Still `not_answering` with pings arriving → new fault, back to first principles.

**(b) The 840/841 hole.** Pings 163–166 link to requests 839, 842, 843, 844. Are
840 and 841 `scheduled` or `on_demand`? If scheduled and unanswered, "answering
essentially every pull" is resting on three consecutive IDs with a two-request gap
immediately behind them.

Report both. Neither blocks steps 6/5/7.

---

## Step 6 — The tools (build first, per §0.1)

`backend/app/handlers/location.py`. Two tools, both read-only, both registered in
`register()` alongside `where_am_i`.

### 6.1 `check_location_freshness` — the watchable wrapper

Renders the health check as prose. This is what the watch reads, so its output has
two jobs and they are different:

- **The LLM judge (`_fired`) reads it against the condition string** — so the age
  and the active-hours state must be plainly stated.
- **The alert call carries it verbatim** as `Observed: {observation}` — so the
  **layer attribution from TDD §4.3 must be in this output.** `opening` is written
  at watch-creation time and is therefore static; the observation is the only
  dynamic channel into the call. If the layer isn't here, the alert can't name it.

Layer attribution, read from `location_requests`, **fact only, no root cause**:

| Observed | Layer named |
|---|---|
| No recent `scheduled` rows | JARVIS isn't sending location requests |
| Recent rows, `relay_accepted=false` | Requests going out, relay rejecting them |
| Recent rows accepted, none answered even late | Requests accepted, phone not answering |
| Recent rows answered past the timeout | Phone answering late — fixes arrive too stale to use |

The last two must not collapse. That distinction is the entire point of #69, and
here it reaches the owner's ear instead of the status page.

**The tool states the layer and stops.** Not "your Tasker profile is disabled" —
that is a guess wearing a fact's clothes, and it is the netstatus-stub defect in
alert form. The runbooks exist for the sub-layer; the alert points at the layer.

### 6.2 `location_ping_log(n=20)`

Reads recent `location_pings`. Per ping: age, coordinates, accuracy, label,
`trigger`, and whether it linked to a request.

**Must state its own retention horizon.** `record_ping` prunes to
`location_keep_pings = 200` on every write, and that setting is **deploy-only** —
it is not on the runtime allow-list. At the current 15-minute cadence that is
roughly 50 hours. But `location_pull_interval_minutes` floors at 5, and at
5-minute cadence 200 pings is about **16 hours** — shorter than the 24-hour window
the diagnostic order queries. Tightening the interval to chase a fault silently
shortens the evidence available to chase it with.

So when the log is showing the full 200, say so: *"showing the oldest of 200
retained pings — anything earlier has been pruned."* Otherwise "no older pings"
reads as "no older activity," which is a fabricated absence.

### 6.3 A comment at the prune

Add a line next to the prune in `record_ping` recording a property that is now
load-bearing and was never argued for: **`location_pings` prunes, `location_requests`
does not.** A `timeout` request whose ping aged out reads as retroactively silent
— the evidence is gone while the row needing explanation remains. Step 4a
incidentally fixed this, because `responded_at` lives on the unpruned side.

That was a side effect of putting the field where §4.5 wanted it. Write it down,
or someone tidies it away and re-arms the failure without knowing they have.

### 6.4 The prompt, not just the roster

**This is the Sunday finding applying to today's work.** `seed_agents` reconciles
rosters and never overwrites `system_prompt`, so adding these tools to `navigator`
ships the capability and none of the prose explaining it.

Run `design-note-prompt-drift.md` §5 against both tools:

- `check_location_freshness` — largely self-describing; the roster may carry it.
- `location_ping_log` — **needs a prompt line.** Nothing in its schema says *when
  to reach for it unprompted* ("has my phone been reporting?", "when did you last
  hear from me?"), and that is exactly the judgment the checklist says a roster
  cannot carry.

Amend the **seed**, and then write production separately — the seed does not reach
the live row. Report that the DB write is outstanding; **do not make it yourself.**

### 6.5 Guards + plant

Cover: freshness prose includes age and active-hours state; each of the four
attribution branches; the retention line appears at 200 rows and not below.

**Plant:** make the attribution helper return the same layer for all four inputs.
The four branch tests must go RED. If any stays green, that branch isn't reached
and the attribution table is a manifest rather than logic. Verify the patch
applied before reading the result.

---

## Step 5 — The absence watch

### 5.1 Seeding, idempotently

Seed alongside the other system seeds, keyed on `created_by == "system"` **and**
`tool == "check_location_freshness"`. Creating it twice on redeploy is a bug —
guard on that pair, not on the id, not on the condition text (which will be
edited).

- `tool` = `check_location_freshness`
- `condition` = plain English, per TDD: no fix has registered in over 30 minutes
  during active hours
- `every_minutes` = 15 (two checks inside the 30-minute window)
- `recurring` = **False** (§0.2 — the re-arm makes it repeatable)
- `created_by` = `"system"`

### 5.2 `rearm_system_watches(db)`

In `watches.py`, called from the worker tick immediately before `due_watches`.

For each `status == "done"` watch with `created_by == "system"`: if the underlying
condition has **cleared** (`location_freshness` reads `ok` — read structurally per
§0.3, never via `_fired`), set `status = "active"`. Leave `fire_count` and
`last_fired_at` intact; they are the history and they are the only record that the
alarm has ever gone off.

`watch_min_interval_minutes` still floors the ring rate underneath this. A watch
that re-arms and immediately re-fires is still rate-limited by the existing guard —
confirm that holds rather than assuming it.

### 5.3 Quiet hours are already handled — confirm, don't rebuild

`check_location_freshness` returns `ok` outside active hours (step 1), so the
condition cannot be true at 3am and the watch cannot fire then. The outbound quiet
-hours guard is a second layer underneath. **Confirm both hold with a test at
03:00**; do not add a third suppression. Two guards for one property is how they
drift.

### 5.4 Before you seed — this one places calls

This watch can trigger an outbound voice call. Everything else in this order is
read-only.

Verify **in the test suite, not in production**: it fires at most once per outage;
it does not fire outside active hours; the rate-limit floor holds; and a failed
call placement leaves it `active` to retry rather than consuming it (the existing
audit-M4 behaviour at `watches.py:181` — confirm the seeded watch inherits it).

Seed it in the same PR. **Report immediately after deploy whether it fires**, and
if it fires more than once inside one outage, treat that as a defect and report
before touching anything.

### 5.5 Guards + plants

**Plant 1:** make `rearm_system_watches` a no-op. The re-arm test must go RED. If
it stays green, the test is asserting the first fire and never reaching recovery —
the dead-man's-switch failure, defended by a passing test.

**Plant 2:** flip the seed guard to key on `condition` text instead of
`(created_by, tool)`. The idempotency test must go RED. Then confirm it catches an
*edited* condition, which is the realistic version of the bug — a text-keyed guard
looks correct until someone rewords the prose and gets a second watch.

**Plant 3:** set the seeded watch's `recurring=True`. The no-nag test must go RED.

---

## Step 7 — Brief integration

`location_freshness` feeds the brief's exception-first systems section like every
other component: **silent when fresh, one line when stale.** Age as fact — "location
fix 41 minutes old" — never judgment. Outside active hours it may appear as a
`degraded` note, never as an alarm.

Reuse the existing component→brief path. If it already picks up any component with
a non-ok result, this step is a test confirming the new component flows through
and nothing more — say so plainly rather than adding code to look busy.

**Plant:** force `location_freshness` to `ok` and confirm the brief line
disappears. A brief line that renders regardless of status is the exception-first
design quietly becoming a table read.

---

## Housekeeping — the heredoc

The failed-commit incident belongs in `design-note-unwatched-instruments.md` as
**§2.6**, not in a session note. It is §2.4's class with the failure inverted:
2.4 reported success having changed nothing; yours reported failure having changed
something. Both are *the tool's claim about itself is not evidence*. §4.1 already
states the rule; your instance shows it cuts both directions, and that is what
makes it worth a reader's time.

---

## Report back

Per step: what changed, the plant, whether it went red. A plant that stays green is
a finding — work §1 of the unwatched-instruments note and report before continuing.

At the end: `alembic heads` unchanged, suite count, confirmation the watch seeds
exactly once across two runs of the seeder, and the §0.3a read. Flag the navigator
prompt DB write as outstanding — that one is mine to authorise, not yours to make.
