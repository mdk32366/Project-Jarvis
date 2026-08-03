# SESSION CLOSE-OUT — 2026-08-03 (location freshness arc complete)

**Opened at:** `d199a10`, migration head `0029_plan_draft_status`, 828 tests.
**Closed at:** `ae8d5f9` (PR #71), head **unchanged at `0029`**, **886 passed / 2
skipped**. No migration this arc.

**Merged:** #69 (`6f0b594`, steps 1–4), #70 (`72d826c`, docs + primary ruling),
#71 (`ae8d5f9`, steps 5–7).

**The location freshness TDD is complete.**

---

## 1. What shipped

| | |
|---|---|
| `location_stale_after_minutes` | 30, runtime-tunable 5–1440, commented against `location_max_age_minutes` so a consumer trust threshold isn't collapsed into a health one |
| `check_location_freshness` | Three tiers, `unknown`/`never_pinged` on no evidence, `ok` outside active hours |
| `location_freshness` component | Two runbooks, non-primary capability membership |
| `responded_at` on late closes | First answer wins; `status` untouched |
| `answering_late` | Split on evidence from `not_answering`, majority-of-failures, no new tunable |
| `check_location_freshness` tool | Watchable wrapper carrying layer attribution into the alert |
| `location_ping_log` | Reads recent pings, states its own retention horizon |
| Absence watch | Seeded once, `recurring=False`, re-armed by `rearm_system_watches` |

**Fourteen plants, fourteen reds** across three PRs, each verified applied before
its result was read. The runbook-join guard also fired unprompted when a component
landed ahead of its fault map.

---

## 2. Ratified decisions

- **`location_responsiveness` stays primary.** Freshness green + responsiveness
  down is a real and current state, and it *is* degraded. If freshness led, the
  capability would read green through a broken loop and surface only once a fix
  went stale — an outage rather than a warning. **Responsiveness leads; freshness
  lags. Primary belongs to the leading indicator.** Freshness is the better
  end-to-end signal and the worse alarm.
- **`answering_late` lives on `location_responsiveness`,** not on a new component.
  The TDD's §5 would have put the same fault name on two components with two
  runbooks that drift.
- **Re-arm in the watch engine, not in the health check, and not via a schema
  column.** `created_by == "system"` already carries the distinction. Checks read;
  the engine writes.
- **Recovery is read structurally, never through `_fired`.** The judge fails
  closed; routing recovery through it leaves the watch permanently `done` — silent
  and indistinguishable from "no outage since."
- **No component-detail path in the brief.** Deferred; trigger is a *second*
  component wanting it.

---

## 3. Findings worth carrying

### The Planner error (§5.3)

The order said "confirm, don't rebuild" reasoning from `check_location_freshness`
the *health check*. The watch reads `_check_location_freshness` the *tool* — a
different function, different module, no out-of-hours branch. At 03:00 with a
stale fix it emitted the phrasing the condition matches and left the window to the
LLM judge, the exact coupling §0.3 forbade one paragraph earlier.

The order didn't just miss the defect; it **instructed the Builder not to look.**

> **Doctrine: a "confirm, don't rebuild" instruction must name the module path,
> not the bare function name.** Bare names are ambiguous exactly where duplication
> hides.

### §2.7 — single-value plants

> **A plant whose injected value coincides with an expected output cannot redden
> the branch that legitimately produces it.**

Three of four branches went red; the fourth passed correctly because the planted
constant was its right answer. Construction rule: **inject a value no branch can
legitimately produce.** A partial red across a branch set looks like rigour and
is a coverage gap.

### Evidence discipline

Two claims this session were made on the wrong evidence and both were caught by
going back to the data:

- **Arrival cadence cannot distinguish prompt answering from uniform lateness.** A
  phone answering every request 50 minutes late produces evenly-spaced arrivals.
  Latency settled it; cadence never could have.
- **Pre-deploy late answers read as silent.** Four rows had linked pings and no
  `responded_at`, because the write didn't exist yet. Counting `responded_at IS
  NULL` as silence over the deploy boundary miscounts by exactly that many.

### An accidental property, now load-bearing

`location_pings` prunes at 200; `location_requests` never prunes. A `timeout` row
whose ping aged out reads as *retroactively silent*. Step 4a fixed this by
accident — `responded_at` lives on the unpruned side. Commented at the prune so it
isn't tidied away.

---

## 4. Production state at close

```
location_freshness       ok     —              newest fix ~24m old
location_responsiveness  down   not_answering  fulfilment 56% (5 of 9)
location_pull_scheduler  ok     —
Location capability             amber
```

**The fault class changed; the loop did not get healthy.** 08-01 fixed lateness:
answers now land in 13–14 seconds or not at all. But 3 of 9 produce no ping,
which is `not_answering` — the Tasker/delivery runbook, not power management.

Doze defers work rather than dropping it, so a 13-second answer rules out
throttling. The hypothesis is the **relay → FCM → phone delivery leg**, which
`check_location_scheduler`'s docstring already names as unobservable from the
server.

---

## 5. Next session, in order

1. **`READ-ORDER-silent-leg-2026-08-03.md`** — `relay_accepted` on the three
   silent rows, clustering, active-hours edges; brief-timing confirmation for §7;
   two doc amendments. Read-only.
2. **Phone-side receipt — a TDD if Read 1 says delivery.** Tasker ACKs on
   *receipt*, separately from the fix, splitting "never arrived" from "arrived,
   didn't answer." Makes the currently-unobservable leg observable. **Not
   decided** — recorded so it isn't re-derived.
3. **Prompt-drift enforcement gap.** The 08-02 audit synced eight agents and left
   two guards — `secretary` and `travel`. The other seven have the §5 checklist
   (process) and nothing that fails (enforcement). The mechanism that produced
   day-one prompts on all nine agents is still running against them.
4. **KEEL deck + How-It-Works SVG at V3** while the five documents are at V5.

**Deferred, recorded:** `location_keep_pings` is deploy-only and not on the
runtime allow-list — at the 5-minute interval floor, 200 pings is ~16 hours,
shorter than the diagnostic's own 24-hour window.

---

## 6. Outstanding for the owner

- **Navigator prompt DB write.** The seed carries the `location_ping_log`
  guidance; `seed_agents` will not push it to production. Read the live row first
  and reconcile rather than overwrite — production may carry edits the seed
  doesn't have.
- **First watch fire.** The absence watch is armed and can place an outbound call.
  More than one fire inside a single outage is a defect, to be reported before
  anything is touched.
