# TDD — Backlog Item #11: Current Date/Time Awareness & Stale-Data Sanity Checks

**Status:** Ready to build
**Repo:** `mdk32366/Project-Jarvis`
**Prereq:** flight booking TDD complete (263 tests green)
**Say "let's roll" and hand this to a fresh session.**

---

## 0. Triggering incidents

**Incident 1 — wake-up call crash (date unknown)**

A scheduled wake-up call didn't fire at 4am. When it eventually ran (late,
cause unknown — possibly related, possibly not), JARVIS started reading the
schedule, stopped mid-sentence, asked "are you still there?", then said "I'm
sorry, I can't do that, goodbye" and hung up.

That crash is a **separate bug** from the missing time primitive — something
threw partway through a proactive/scheduled turn and the failure path dumped
the call instead of degrading gracefully. This TDD does not fix that crash.
It's flagged here because the two are easy to conflate: a wake-up call is
inherently date/time-relative ("is it actually 4am, is this still today's
schedule"), so a missing clock primitive is a plausible contributor, but
"she hung up on me" needs its own root-cause pass on the scheduled-call
handler's exception path. **Open a separate bug ticket for the crash itself
— don't let this TDD absorb it.**

**Incident 2 — wrong-year flight search (2026-07-13, confirmed in logs)**

Voice call, `CA546c6aef0401ab8fc7a9408141dd789f`. User said:

> "Headed to SFO, August 4th, coming back August 9th from Sacramento."

JARVIS delegated to travel and searched for **2025-08-04** — past date, no
flights. The user corrected: *"No, I need it for 2026 and I'll always be
referring to flights in the future. Never the past."* JARVIS searched again.
Same result. This happened across four consecutive delegate calls and a
second call-back session before the user gave up.

Root cause: no `get_current_datetime` call, so no grounding for "August 4th"
to a year. With no year, `dateutil.parser.parse` defaults to the current
parse-time year. But the LLM's own internal reasoning was anchored to its
training data (sometime before 2026), so "August 4th" was resolved against
the wrong year. The absence of a system clock meant there was nothing to
catch it.

This is the regression test driver. `test_wrong_year_produces_a_clarifying_question_not_a_past_search`
must cover this case explicitly.

---

## 1. What we're building

JARVIS currently has no way to ask "what time is it, right now." She infers
"now" from ambient signals — an email timestamp, whatever's in context — which
is unreliable and frequently just absent. This gives her:

1. A real primitive: `get_current_datetime()`.
2. A mandatory habit: call it before any date-relative reasoning or phrasing.
3. A backstop: sub-agent outputs containing dates get checked against real
   "now" before they reach the user, so stale content doesn't get presented
   as current.

**The scope is deliberately narrow: this is a clock, not a calendar, not a
scheduler, not a health system.** Those are related but separate (see
TDD — JARVIS Self-Awareness, companion doc).

---

## 2. Root cause

- No system clock call exists in the orchestrator or sub-agent toolset.
- Date-relative reasoning ("upcoming," "this week," "latest," "still open")
  is delegated to LLM inference over stale training data or unverified
  search/email snippets, with nothing grounding it to actual current time.
- No post-hoc validation step compares dates found in returned content
  against real-world "now" or the request's intended time window.
- No distinction is made between **the user's current timezone** (Matt,
  wherever he is) and **JARVIS's own operating timezone** (the Fly.io
  container — Pacific, always, DST-observed). Conflating these silently is
  how you get a wake-up call that fires at the wrong hour.

---

## 3. The two timezones — stated plainly

This is the part worth being pedantic about, because it's the part that
silently breaks:

- **JARVIS's timezone** is `America/Los_Angeles`, always, hardcoded via the
  IANA tz database (not a fixed UTC offset — the tz database handles the
  PST/PDT transition dates correctly; a hardcoded `UTC-8` would be wrong half
  the year). This is where she runs, and it's the timezone scheduled jobs
  (wake-up calls, reminders) are anchored to unless told otherwise.
- **Matt's timezone** is wherever Matt currently is. Usually Pacific too
  (Stanwood/Bothell), but not always — travel changes it, and flight
  booking now means JARVIS is actively involved in itineraries that cross
  timezones. This should be inferable from context (location reporting via
  Tasker, an active trip's destination, explicit statement) but **must have
  a stated default and must never be silently assumed identical to her own**
  when there's a signal it isn't.

`get_current_datetime()` returns both, explicitly labeled, every time. No
caller should have to guess which one a bare timestamp refers to.

---

## 4. Implementation

### 4.1 `get_current_datetime` primitive

```python
def register_datetime(reg: Registry) -> None:
    """Available to orchestrator AND all sub-agent rosters — unlike gated
    financial/booking tools, this is pure read-only info with no side
    effects, so there's no reason to restrict it to top-level."""
    reg.register(
        {
            "name": "get_current_datetime",
            "description": (
                "Get the current real-world date and time. ALWAYS call this "
                "before any date-relative reasoning or phrasing — 'upcoming,' "
                "'this week,' 'latest,' 'still valid,' 'expires soon.' Never "
                "infer 'now' from an email timestamp, a search result, or "
                "training data. Returns both JARVIS's own operating time "
                "(Pacific) and the user's current local time if known."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        _get_current_datetime,
        gated=False,
    )
```

`_get_current_datetime` returns:

```python
{
    "jarvis_time": "2026-07-13T07:42:11-07:00",   # America/Los_Angeles, tz-aware
    "jarvis_tz": "America/Los_Angeles",
    "jarvis_utc_offset": "-07:00",                 # explicit, since PDT/PST varies
    "user_time": "2026-07-13T07:42:11-07:00",      # None if truly unknown
    "user_tz": "America/Los_Angeles",              # None if truly unknown
    "user_tz_source": "default",                   # "default" | "location_report" | "active_trip" | "stated"
    "utc": "2026-07-13T14:42:11Z",
}
```

`user_tz_source` matters: it tells the orchestrator (and, if surfaced,
the user) *why* it believes the user's timezone is what it says — a
default guess is a weaker basis for "so I'll call you at 4am your time"
than an active-trip destination or something Matt said outright. Prefer,
in order: explicit statement in this conversation > active trip / itinerary
destination (from `Trip` records) > last Tasker location report > default
(Pacific).

**Implementation notes:**
- Use `zoneinfo` (stdlib, Python 3.9+) against the IANA tz database, not a
  hardcoded offset. This is what makes DST handled for free.
- No external NTP dependency — system clock on Fly.io is fine. This is a
  cheap, local, no-network call. It should never be the thing that times out
  or 500s.
- Cache is inappropriate here by definition — every call must reflect actual
  "now," not a snapshot from turn start.

### 4.2 Mandatory-call enforcement

This is a *habit*, and habits enforced only by a system-prompt sentence rot.
Two backstops:

1. **Prompt-level instruction** in the orchestrator and every sub-agent
   preamble: call `get_current_datetime` before using or evaluating
   temporal language. This is necessary but not sufficient — it's the same
   category of control as "please don't hallucinate," i.e. weak on its own.
2. **Structural nudge**: any sub-agent whose registered toolset includes
   date-bearing external content (web search, email read) gets
   `get_current_datetime` auto-injected as a forced first call in that
   agent's turn — not optional, not something the LLM decides to skip. This
   mirrors the existing pattern where certain tools are structurally
   privileged rather than merely documented as important (cf. the gate for
   `book_flight` — a control that lives in code beats one that lives in a
   prompt, every time).

### 4.3 Post-processing sanity check

Applies to sub-agent outputs (web researcher first; email/calendar-adjacent
agents as a fast-follow) that contain parseable dates.

**Pipeline:**
1. After a sub-agent returns, extract candidate dates from its output
   (regex/dateutil pass over the response text — cheap, does not need an
   LLM call).
2. Compare each against `get_current_datetime()` and the request's intended
   window — explicit ("next 7 days," "before August 4th") or inferred from
   phrasing ("upcoming," "this week" → now through end of current week in
   the relevant timezone).
3. **Flag, don't silently drop, in the default case.** A date outside the
   window or in the past when framed as future/upcoming gets annotated —
   e.g. `[stale: dated 2023-11-01, requested "upcoming"]` — and surfaced to
   the orchestrator, which decides whether to mention it, re-search, or drop
   it from the final answer. Silent filtering risks quietly removing
   something the user actually wanted (a deliberately-past reference date in
   a research query, for instance). Silent pass-through risks presenting
   garbage as current. Flagging is the only option that fails toward the
   user finding out.
4. Exception: **flight offers already fail closed** via Duffel's own
   expiry rejection (TDD-flight-booking §4.1) — this sanity check does not
   duplicate that, it's for *content*, not transactional state.

### 4.4 Relative-date resolver

`resolve_relative_date(text: str, reference_dt: datetime) -> datetime | None`

**KEY INVARIANT:** when the expression carries no explicit year, the resolver
MUST produce the NEAREST FUTURE occurrence — **never a past date**.
`"August 4th"` with reference `2026-07-13` → `2026-08-04`.
`"August 4th"` with reference `2026-09-01` → `2027-08-04`.

This is the direct regression fix for incident 2 (§0): four consecutive
delegate calls searched for 2025 flights while the user said "August 4th."
A resolver that enforces the future-bias invariant would have surfaced
`2026-08-04` from the first call.

**Handled patterns:**
- `"today"` / `"tomorrow"` / `"yesterday"` — day arithmetic against `reference_dt`
- `"in N days/weeks/months"` — arithmetic delta
- `"next [weekday]"` — e.g. `"next Tuesday"` → always ≥ 1 day in the future
- `"[Month] [day]"` — e.g. `"August 4th"` → current year if future, else next year
- `"[Month] [day], [year]"` — explicit year honoured exactly (user stated it)
- `"this weekend"` → upcoming Saturday

Falls back to `dateutil.parser.parse` for ISO-ish strings and formats not
covered above; applies the future-bias invariant there too when no year is
present.

Returns `None` if the expression is not recognisable. Callers must handle
`None` explicitly — do not silently treat it as "now."

**Does NOT use an LLM call** (§7).

**The `user_tz_source` field on `get_current_datetime` output tells callers
whether to trust the user's timezone for scheduling purposes.** A `"default"`
source means JARVIS is guessing Pacific; an `"active_trip"` or
`"location_report"` source means she has real signal. Scheduled wake-up
times must never be anchored to a default guess if a stronger signal exists.

---

## 5. Tests that must exist

| Test | Property |
|---|---|
| `test_get_current_datetime_returns_both_timezones_explicitly_labeled` | No bare timestamp — always `jarvis_time` + `user_time`, both tz-aware, never ambiguous which is which. |
| `test_jarvis_timezone_is_always_america_los_angeles` | Hardcoded, not configurable, not inferred from container locale. |
| `test_dst_transition_is_handled_by_tz_database_not_fixed_offset` | Pick a date on each side of a DST boundary; assert the UTC offset flips correctly without code changes. |
| `test_user_tz_source_reflects_actual_basis_for_the_guess` | Explicit statement > active trip destination > last location report > default — assert precedence, not just presence. |
| `test_user_tz_defaults_to_pacific_when_no_signal_exists` | No silent `None`/crash when nothing is known — falls back to a stated default. |
| `test_date_bearing_subagents_call_datetime_as_forced_first_turn` | Web researcher (and others with external date-bearing content) cannot skip the call — structural, not prompt-only. |
| `test_stale_result_is_flagged_not_silently_dropped` | A past-dated item framed as "upcoming" is annotated and surfaced, not silently removed from the response. |
| `test_stale_result_is_flagged_not_silently_passed_through` | Same case — it does NOT reach the user unannotated as if current. |
| `test_date_outside_explicit_window_is_flagged` | "Next 7 days" query; a result 3 weeks out gets flagged. |
| `test_date_extraction_handles_relative_phrasing_in_source_content` | "next Tuesday" in a fetched page is resolved against the page's own dateline where available, not JARVIS's "now" (a page's "next Tuesday" means the page author's next Tuesday). |
| `test_flight_offer_expiry_is_not_double_handled_by_this_pipeline` | Confirms no interference with the existing Duffel-native expiry path. |
| `test_datetime_call_has_no_external_network_dependency` | Mocked/sandboxed network still returns a correct result — it's local clock + tz database only. |
| `test_datetime_call_is_available_in_every_subagent_roster` | Unlike gated tools, this one should NOT be top-level-only — assert presence everywhere. |
| `test_wrong_year_produces_a_clarifying_question_not_a_past_search` | `resolve_relative_date("August 4th", ref=2026-07-13)` → `2026-08-04`, never `2025-08-04`. The future-bias invariant. Regression for incident 2. |
| `test_flight_search_with_ungrounded_date_resolves_to_future_year` | When `resolve_relative_date` is called with today in July 2026 and an unqualified month/day that is still in the future this year, returns this year. When the day has already passed this year, returns next year. |

---

## 6. Config

```python
# Datetime awareness
jarvis_tz: str = "America/Los_Angeles"   # hardcoded, not overridden by env
user_tz_default: str = "America/Los_Angeles"
user_tz_source_priority: list[str] = [
    "stated", "active_trip", "location_report", "default"
]
```

No secrets, no new external dependencies. This should be one of the
cheapest TDDs in the project.

---

## 7. Things I would push back on, if asked

- **Don't make this an LLM call.** Extracting "what time is it" and "is this
  date stale" via regex/dateutil is fast, deterministic, and free. Routing
  it through Claude adds latency and a new way to be wrong, for a problem
  that doesn't need judgment.
- **Don't silently drop flagged content by default.** It's tempting because
  it's simpler, but a system that quietly removes things is a system you
  can't audit later. Surface it; let the orchestrator or the user decide.
- **Don't conflate this with the wake-up-call crash.** Fixing the missing
  clock primitive will not, by itself, fix a scheduled turn that throws
  mid-readback and hangs up. That's its own bug.
- **Don't skip the DST test.** This is exactly the kind of bug that passes
  in July and breaks in November.

---

## 8. Decisions (settled — do not re-litigate)

1. **JARVIS's own timezone is hardcoded `America/Los_Angeles`**, via the tz
   database, not a fixed offset. DST is handled for free; a manual offset
   would need twice-yearly maintenance and would silently drift wrong in
   the meantime.
2. **Flagging beats silent dropping or silent pass-through** for stale
   content. The failure mode of "the user finds out something was filtered"
   is strictly preferable to either "garbage presented as current" or
   "something real got silently removed."
3. **This is a read-only, ungated, universally-available tool** — no
   restriction to top-level registry, unlike financial/booking tools. There's
   no side effect to gate.

### Still open (small)

- Whether the post-processing sanity check runs synchronously in the
  sub-agent's turn (adds latency, but the orchestrator sees clean output) or
  async/best-effort (faster, but flags could arrive after the response was
  already spoken on a voice call). Recommend synchronous for text/SMS,
  and revisit for voice once the wake-up-call crash bug (see §0) is
  independently fixed — don't add complexity to the voice path while it's
  already misbehaving.

---

*This is a small, cheap primitive that a lot of other things were silently
assuming existed. The value isn't the clock itself — it's removing an
entire category of "she inferred 'now' from garbage" bugs in one pass.*
