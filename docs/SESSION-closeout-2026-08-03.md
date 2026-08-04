# SESSION CLOSE-OUT — 2026-08-03

**Opened at:** `d199a10` · head `0029_plan_draft_status` · 828 tests *(unverified —
never re-measured, but consistent; see below)*
**Closed at:** `694ba29` · head **`0029`, unchanged** · **no migration all day** ·
**877 passed / 2 skipped, 879 collected** — measured via `--junitxml`

**Eight PRs:** #69 `6f0b594`, #70 `72d826c`, #71 `ae8d5f9`, #72 `c9033f7`,
#73 `ea7687f`, #74 `943a199`, #75 `f832d63`, #76 `0317482`.

> ### Correction — today's suite counts were systematically overstated
>
> Not one bad number. **Every count reported after the open was impossible.**
>
> | commit | | test fns | max collectable | claimed passed | |
> |---|---|---:|---:|---:|---|
> | `d199a10` | open | 810 | **828** | 828 | consistent |
> | `ae8d5f9` | #71 | 847 | 865 | **886** | impossible by 23 |
> | `943a199` | #74 | 852 | 870 | **892** | impossible by 24 |
> | `0317482` | HEAD | 861 | 879 | **877** | **measured** |
>
> Parametrisation adds a stable +18 collected cases over raw function count,
> pinned at HEAD (861 → 879 measured) and corroborated at the open, where 810
> functions predict exactly the 828 claimed.
>
> **886 and 892 were not inflated — they were impossible.** Each exceeds the total
> number of tests that *existed* at its commit, so no run could have produced
> them. That is a stronger conclusion than mis-transcription, and it was available
> from the day's own data the whole time.
>
> **#71 actually passed ~863, not 886. #74 passed ~868, not 892.** Both marked
> *unverified*: they are predicted from the +18 model, not re-run at those
> revisions. The open's 828 is left as claimed for the same reason — an inferred
> number in a resumption document is exactly what this correction exists to stop.
>
> **The pattern is the finding, not the arithmetic.** Two separate PRs, reported
> from separate sessions, both overstating by ~23. Every "suite green at N" line
> written today carried the same defect, and this document was treating those
> lines as sourced artefacts. Recorded in `findings.md`.
>
> **A first attempt at this correction repeated the defect it was correcting.** It
> put the overstatement at "~9" by subtracting a *claimed* figure at one commit
> from a *measured* figure at a different commit with fourteen more tests in it —
> two comparisons folded into one number. Caught in review, not by the author.

Four arcs closed and one documentation set revised:

1. **Location freshness TDD — complete** (#69–#71)
2. **Silent-leg diagnosis — read-only, complete** (#72)
3. **Prompt-drift enforcement — complete, production verified** (#73–#74)
4. **Live prompt-guidance health check — complete** (#75–#76)
5. **KEEL V6** — five documents plus the deck

---

## 1. Location freshness (#69–#71)

| | |
|---|---|
| `location_stale_after_minutes` | 30, runtime-tunable 5–1440, commented against `location_max_age_minutes` so a consumer trust threshold isn't collapsed into a health one |
| `check_location_freshness` | Three tiers; `unknown`/`never_pinged` on no evidence; `ok` outside active hours |
| `location_freshness` component | Two runbooks, non-primary capability membership |
| `responded_at` on late closes | First answer wins; `status` untouched |
| `answering_late` | Split from `not_answering` on evidence, majority-of-failures, no new tunable |
| `check_location_freshness` tool | Watchable wrapper carrying layer attribution into the alert |
| `location_ping_log` | Reads recent pings, states its own retention horizon |
| Absence watch | Seeded once, `recurring=False`, re-armed by `rearm_system_watches` |

**Fourteen plants, fourteen reds**, each verified applied before its result was
read. The runbook-join guard fired unprompted when a component landed ahead of its
fault map.

### Ratified

- **`location_responsiveness` stays primary.** Freshness green + responsiveness
  down is a real state and it *is* degraded. **Responsiveness leads; freshness
  lags. Primary belongs to the leading indicator.** Freshness is the better
  end-to-end signal and the worse alarm.
- **`answering_late` on `location_responsiveness`**, not a new component.
- **Re-arm in the watch engine** — not in the health check, not via a schema
  column. Checks read; the engine writes.
- **Recovery read structurally, never through `_fired`.** The judge fails closed.
- **Brief step 7: unreachable by construction.** Effective `briefing_hour` is **4**
  against `location_active_start_hour` 7 — the brief composes three hours before
  active hours open, so freshness always reads `ok` then. Also redundant: the
  capability rollup already carried this outage via `location_responsiveness`,
  which is not hour-suppressed. Component-detail path deferred; trigger named (a
  *second* component wanting it).

---

## 2. Silent-leg diagnosis (#72) — read-only

**Dispatch is clean.** Every request in 24h: `relay_accepted = true`, empty
`relay_error`, including all silent ones. The unobservable leg is real.

```
L S S  L S S  L S S  P L P P P L S P L S P L S S S P L P
└──── 07:00–09:15 ────┘└──────── 09:15 onward ────────┘
```

Early regime: period-3 rhythm, one answer per 45 minutes, latency 35–40 min.
Later: mostly prompt, silences in runs of 1–3.

**The discriminator: the answered nonce is the OLDEST of its window, not the
newest.** A reconnecting phone would have FCM deliver all three, or the most recent
at ~10 minutes. Neither matches. Two hypotheses survive — **doze maintenance
windows** (queued, one released per window, rest expire) and **Tasker task
collision** (all three delivered, first runs, overlapping instances discarded).

### Correction to carry — the fault class did NOT move

An earlier reading held that lateness was fixed on 08-01 and the silences were a
new fault, with an instruction not to run power management against them. **That is
wrong.** The early `L S S` rhythm is the *same* doze phenomenon still running
during idle; the 09:15 shift is the phone being picked up. The 08-01 fix **cleared
lateness during active phone use** and never touched the idle regime.

Post-deploy sample: **5 prompt / 1 late / 3 silent of 9** — 56% fulfilment. Carry
the denominator.

---

## 3. Prompt-drift enforcement (#73–#74)

**The ledger** (`backend/app/prompt_review.py`): 76 tool-slots across nine agents —
45 `guided`, 31 `self-describing`, each guided entry carrying a one-line reason. A
code file, not a table, so the disposition is reviewable in the PR that adds the
tool.

**Three guards**, all negative-validated: coverage (fails on roster growth),
guidance (`_JUDGMENT_TOOLS` retired into the ledger), reverse join (no orphans).
Growth fails; shrinkage does not — only one is a defect.

**Production verified, not assumed.** Diff before write: seven agents identical to
seed; navigator and secretary **`ops=['insert']` only** — no replace, no delete.
That made wholesale and targeted-insert provably equivalent and ruled out a
travel-case regression by construction. Snapshot first; post-write audit ran the CI
guard against production.

### Three shapes, one guard

| Shape | Caught? | Why |
|---|---|---|
| Tool with no rule at all | **Yes** | Mechanical |
| Guidance present, tool unnamed (`set_project_status`) | No — found by hand | Findable by a human reading the prompt |
| Name present, no real guidance | No | Requires judging whether prose is guidance; **not mechanisable** |

---

## 4. Live prompt-guidance check (#75–#76)

`prompt_guidance` reads **live** `AgentConfig` rows and asserts the rule the CI
guard asserts against the seed — closing limit 1 of `prompt_review.py`, where
editing `DEFAULT_AGENTS` turns the ledger guards green while production keeps its
old prompt. Documented and unwatched; now it goes amber.

Three properties, each planted: reads live rows not seed; amber ceiling, never
down; runbook joins. **Judges naming, not wording** — travel's production prompt
saying "you cannot book" is truer than the seed while the vendor has booking
disabled, and a content comparison would have flagged that correct state as drift.
Whether a tool is named is a question production can answer without the check
having an opinion about whose prose is better. The runbook says plainly that a
deploy will not fix a gap — the write is an owner action, diff first.

### The defect found by verifying, not by reading

First deploy returned `ok — all 7 reviewed agent(s)` with **nine** agents live and
all nine in the ledger. `finance` and `scheduling` are fully reviewed and every
tool came back `self-describing`, so `guided_tools()` is empty — and the component
used emptiness as its test for "never reviewed." Two consequences: coverage was
undercounted, so the green overstated how much had been looked at; and a genuinely
unreviewed agent would have been **dropped silently**.

> **Doctrine: an empty collection is not evidence of absence of review.** Same
> family as `unknown` never mapping to green. Key on the fact you actually want —
> ledger membership — not on a proxy that happens to correlate with it.

Now keyed on membership, with unreviewed agents named in the detail. **A silent
skip is the exact shape `design-note-unwatched-instruments.md` exists to catch, and
this one was inside the component built to catch it.** Reading the code would not
have found it; running it against real data did.

**Honest note on the #76 plant:** restoring the old condition reddened the count
test but *not* the "skip is named" test, because under that plant the agent still
lands in the skip list. Different property, not covered by that plant. Reported
rather than claimed — §2.7 applied to its own reporting.

---

## 5. Doctrine — the durable output

**Module paths, not bare names.** A "confirm, don't rebuild" instruction must name
the module path. The §5.3 order reasoned from `check_location_freshness` the health
check; the watch reads `_check_location_freshness` the tool — different module, no
out-of-hours branch. The order didn't just miss the defect, it **instructed the
Builder not to look.**

**§2.7 — single-value plants.** A plant whose injected value coincides with an
expected output cannot redden the branch that legitimately produces it. **Inject a
value no branch can legitimately produce.** A partial red across a branch set looks
like rigour and is a coverage gap.

**Empty is not absent.** §4 above.

**Cadence is not latency.** A phone answering every request 50 minutes late
produces evenly-spaced arrivals. Latency settled it; cadence never could have.

**Name a span for what it measures.** The receipt TDD's first draft called
`requested_at → acked_at` "delivery latency." The ACK is stamped when the *task
runs*, so the span includes queueing and collision — the netstatus-stub defect in a
column heading. Renamed **time-to-task-start**.

**Deploy boundaries make new fields lie about history.** `responded_at` was NULL on
every pre-existing row, so four late answers read as silent. Same will apply to
`acked_at`.

**An accidental property, now load-bearing.** `location_pings` prunes at 200;
`location_requests` never does. A `timeout` row whose ping aged out reads as
retroactively silent. Step 4a fixed this by accident — `responded_at` lives on the
unpruned side. Commented at the prune.

---

## 6. KEEL V6

Decisions were being written into `docs/README.md` — which worked only because we
invented the convention and remembered it. **A record nobody can find is a record
that doesn't exist**, the exact failure Principle 7 was written to prevent, rebuilt
one directory up.

Four named documents: `architecture.md`, `decisions.md`, `findings.md`,
`testplan.md`.

- Principle 7 retitled: *Write it down where someone would look for it.*
- `findings.md` gives Principle 8's rule a destination it never had.
- `testplan.md` carries §2.7 in teachable form.
- Checklist now **16 steps**; demo-script cross-refs updated to 11–15.
- `decisions.md` is **one file**, reversing V5 — the Planner reads it in one pass.
- **Deck rebuilt V3 → V6, 21 → 24 slides.** V3 slide 7 said *"everything you build
  is PRIVATE. Always"* — the direct opposite of Principle 10. Presenting from it
  alongside the V6 handouts would have contradicted the paper in the room.

---

## 7. Outstanding

**Open questions — answered:**

- ~~*Is the naming check one shared function or two implementations?*~~
  **Two implementations, and it is the worse answer.**

  ```
  tests/test_prompt_review.py:48   missing = [t for t in guided_tools(agent) if t not in spec.system]
  app/health_checks.py:452         missing = [t for t in expected if t not in (r.system_prompt or "")]
  ```

  They read the same ledger, which was the requirement — but the *comparison* is
  written twice. Change one to case-insensitive or word-boundary matching and the
  other will not follow, and both stay green about different things. That is the
  dead-runbook shape (two records of one truth) inside the pair of guards built to
  catch drift.

  **Order for next session — small.** Extract the comparison into one function in
  `prompt_review.py`; have the CI guard and `check_prompt_guidance` both call it.
  **Then plant it: change the shared function and confirm BOTH the CI guard and the
  `prompt_guidance` component redden. If only one goes red, they were not actually
  sharing it** — which is the whole property being bought, and the plant is the only
  thing that proves it.

**Requires a write:**

- **Diagnostic-note amendment.** The note still says the fault class moved and
  warns against power management for the silences. §2 supersedes it. Not confirmed
  applied.
- **#76 plant gap.** "Skip is named" has no plant that reaches it. Suppress the
  skip list from the detail line and confirm the test reddens.
- ~~**Suite count** — truncated in the final report; confirm with a run.~~
  **Done, and it found more than a number.** 877 passed / 2 skipped, 879 collected
  at `694ba29` via `--junitxml`. Both #71's 886 and #74's 892 were *impossible*,
  not truncated — see the correction in the header.

**Requires the owner:**

- **First watch fire.** The absence watch can place an outbound call. More than one
  fire inside a single outage is a defect — report before touching anything.

**Recorded as permanently open:** limits 2 and 3 of `prompt_review.py` — a prompt
edited in production but not in seed is invisible to everything, and no guard can
distinguish real guidance from a bare list of tool names.

**Queued** — see `PREWORK-next-session.md`.
