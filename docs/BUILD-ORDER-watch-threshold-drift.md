# BUILD ORDER — the absence watch's frozen threshold

**Step 0 — `alembic heads`.** Expected `0029_plan_draft_status`. No migration here.

**Step 0b — OWNER ACTION, BEFORE ANY CODE.** Two reads, and the second one gates the
whole order. See §1. Do not start §3 until both are answered.

---

## 1. What must be answered first

### 1a. The first watch fire — how many calls?

The 08-03 close-out records this as the one outstanding item requiring the owner:
*"The absence watch can place an outbound call. More than one fire inside a single
outage is a defect — report before touching anything."*

2026-08-04 ~03:30 was that first fire. **Count the calls in that outage.** One is the
design working. More than one means `rearm_system_watches` or the `every_minutes`
floor is not doing what it claims, and that is a separate and more serious order than
this one.

**Retuning the threshold changes the conditions and destroys the observation.** Answer
this before touching `location_stale_after_minutes`.

### 1b. The active-hours window

Read `GET /api/settings` (or the Admin runtime-settings panel) and record the
**effective** values of `location_active_start_hour` and `location_active_end_hour`.

Why this gates the order: the defaults are **7 and 23**
(`app/config.py`). `app/handlers/location.py::due_for_pull` returns `False` outside
that window, and `app/handlers/location.py::_check_location_freshness` has an
out-of-hours branch returning *"not treated as a fault"* — phrasing chosen
specifically to be unmatchable by the LLM judge overnight.

**Under the defaults, a 03:30 call reporting a 30-minute-old fix is impossible.** No
pulls go out after 23:00, so the newest fix would be ~270 minutes old, and the tool
would refuse to call it a fault. A call saying *thirty minutes* means the pull loop
ran overnight, which means the window is overridden in production and is not where the
design believes it is.

If start is 0 (or end is 0/24), **that is the cause of the 03:30 call**, and the
threshold change in §2 is treating a symptom. Restoring the window is the fix; the
threshold change is then optional tuning.

---

## 2. The threshold number — 60, not 45

`location_stale_after_minutes` is already on `app/runtime_settings.py::ALLOWED_KEYS`
(int, 5–1440). **The owner can change it live with no build and no redeploy.**

**45 is the wrong number.** The 08-03 silent-leg diagnosis recorded the idle regime as
`L S S` — period-3, one answer per **45 minutes**, latency 35–40, silences in runs of
1–3. Forty-five minutes is the characteristic period of the known doze fault. A
threshold set to the period of the noise trips on every borderline run.

At the 15-minute pull interval: 30m = 2 consecutive misses, 45m = 3, **60m = 4.** The
observed runs are 1–3. Sixty clears the known fault and still catches a real outage
inside an hour.

**Change `location_stale_after_minutes`, NOT `location_max_age_minutes`.** They share
a default of 30 and are deliberately different questions — `app/config.py` documents
it at the definition. The first is a *health* threshold ("the feed has stopped"); the
second is a *consumer trust* threshold ("don't route from this fix"). They tune in
opposite directions. Raising the consumer one tells the navigator to route from a
stale position.

---

## 3. The defect this order actually fixes

**Changing the setting alone will not reduce the calls, and the reason is a genuine
defect.**

`backend/app/handlers/watches.py::seed_system_watches` writes the condition and the
spoken opening as literal prose with 30 baked in:

```python
condition=("no position fix has registered in over 30 minutes during active hours"),
opening=("This is JARVIS. Your phone has stopped reporting its position — "
         "I have not had a fix in over half an hour."),
```

The judge (`app/handlers/watches.py::_fired`) matches the **tool's prose output**
against **that stored condition string** — never against
`location_stale_after_minutes`.

So with the threshold at 60, `_check_location_freshness` starts returning *"Last
position fix 40 minutes ago; fresh (stale after 60 minutes)"* while the judge is
holding a condition that says *over 30 minutes*. Forty is over thirty. The judge may
well fire on a fix the system has just declared fresh.

And when it does fire correctly at 65 minutes, **the call still says "over half an
hour."**

Worse: `seed_system_watches` is idempotent on `(created_by, tool)` and returns 0 on
every run after the first, **deliberately never re-wording the condition**. The
comment explains why — keying on condition text would produce a second watch on an
edit, and two watches is a double-ring the owner disables. That reasoning is correct.
The consequence is that the prose is frozen at 30 **forever**, and no deploy will ever
move it.

**Two records of one truth, one of them unreachable.** Same family as the naming-check
duplication queued from #76, and worse, because nothing can update this one.

---

## 4. Step 1 — derive the condition and opening from the effective setting

Both strings become functions of `location_stale_after_minutes` read at **fire time**,
not at seed time.

Two shapes are acceptable and the Builder should pick after reading the live code:

- **(a) Render at fire time.** Keep the stored condition as a template and substitute
  the effective threshold in `check_watch` before handing it to `_fired` and before
  writing the call `context`/`opening`.
- **(b) Reconcile on startup.** Extend `seed_system_watches` to *update* the condition
  and opening of an existing system watch when the effective threshold no longer
  matches, keyed still on `(created_by, tool)` so no second row can appear.

**(a) is preferred.** It has no write path at all, so it cannot create a second watch,
and it is correct within one tick of a runtime change rather than at next restart —
matching the reschedule-on-change property `_briefing_tick` already has. (b) reads a
runtime setting during startup seeding, which is a new coupling.

**The `opening` is not cosmetic.** It is the sentence spoken to the owner when he picks
up at 03:30, and it currently states a threshold that is not the configured one. A
health surface that misstates its own threshold is the same class of defect as a fleet
report that does not name its scope.

**Do not touch `recurring`, `every_minutes`, `fire_count`, or `last_fired_at`.** The
one-shot semantics and the re-arm path are load-bearing and are not what is broken
here.

## 5. Step 2 — the plants

| # | Property | Plant | Must go red |
|---|---|---|---|
| P1 | The condition follows the setting | Pin the rendered condition to the literal 30 | A test that sets the threshold to 60 and asserts the condition names 60 |
| P2 | The opening follows the setting | Pin the rendered opening to "half an hour" | A test asserting the spoken opening names the effective threshold |
| P3 | **Condition and opening share one source** | Change the threshold accessor once and confirm **both** P1 and P2 redden | **If only one reddens, they are not sharing it** — that is the property being bought |
| P4 | Still exactly one system watch | Run `seed_system_watches` twice with different thresholds between runs | A test asserting exactly one row on `(created_by='system', tool)` |

P3 is the point of the order. P1 and P2 passing independently is compatible with two
separate hardcodings, which is the defect restated.

**§2.7 applies to P1 and P2:** inject a threshold no branch can legitimately produce.
Do not plant with 30 — 30 is the default and coincides with a legitimate output, so a
plant using it cannot redden the branch that produces it honestly. Use something like
77.

## 6. Step 3 — the out-of-hours branch is NOT in scope, but confirm it

Do not rebuild it. **Do confirm it is reached**, and name the module path when you do:
`backend/app/handlers/location.py::_check_location_freshness`, the tool — *not*
`backend/app/health_checks.py::check_location_freshness`, the health check. They are
different functions in different modules and the 08-03 close-out records that
confusing them once produced an order that instructed the Builder not to look at the
defective one.

A test asserting the tool returns the "not treated as a fault" phrasing outside active
hours should already exist from #69–71. Confirm it does. If it does not, that is a
finding, not a silent addition.

## 7. Living-document rule

`docs/ARCHITECTURE.md` §8 describes the absence watch and states the condition prose.
If the prose becomes a template, that section must say so — otherwise the document
becomes the third frozen record of the same truth.

## 8. Done means

- The call count from the first fire is recorded (§1a), and if it was more than one,
  **this order stops and a new one is written**.
- The effective active-hours window is recorded (§1b), and if it is not 7–23, that is
  reported before the threshold is touched.
- Condition and opening both track the effective threshold, proven by one plant
  reddening both.
- Exactly one system watch row survives repeated seeding.
- `ARCHITECTURE.md` §8 updated in the same PR.
