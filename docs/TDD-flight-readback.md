# TDD — Contextual flight readback

**Status:** draft, for ratification.
**Motivating request, 2026-08-04:** *"A flight entry in a schedule should be
summarized — give me the confirmation number, translate the city codes to city names,
tell me the departure time and the arrival time, and give me a drive time estimate for
arriving at the parking garage 3.5 hours before departure."*

---

## 1. The thing that makes this harder than it looks

**The departure and arrival times are not in the database.**

`backend/app/handlers/travel.py::parse_itinerary` extracts exactly five fields:
confirmation, flight number, carrier, origin/destination airport codes, and seat. **It
parses no dates at all.** `Trip.depart_at` and `Trip.arrive_at` exist as columns and
are NULL on every email-captured trip. `_list_trips` says so out loud — *"date not
parsed"* — which is honest, and is also the whole problem.

So the requested summary cannot be produced from `trips` alone. Two sources exist and
neither is sufficient:

| Source | Has | Lacks |
|---|---|---|
| Google Calendar event | start, end, timezone offsets | confirmation, seat |
| `trips` row | confirmation, seat, carrier, flight no. | **times** |

**This feature is a join, and the join is the design.** Everything else is formatting.

## 2. The join, and the error it must not make

The join key is fuzzy. A calendar entry is whatever the airline's calendar attachment
or the owner typed — `"Alaska 123 SEA-PHX"`, `"Flight to Phoenix"`, `"AS123"`.

**Attaching the wrong confirmation number to a flight is the one error here with a
real-world cost.** He reads it at the counter. A missing confirmation number is an
inconvenience; a confidently wrong one sends him to the wrong desk.

**Rule: the join fails to "no matching trip on file", never to a best guess.**

Match on, in descending strength:

1. **Confirmation code** appearing in the event summary or description — exact, and
   sufficient alone.
2. **Flight number** (`AS123`, `AS 123`) **plus** a same-day departure date match.
3. **Airport pair plus same-day date match** — accepted only when it yields exactly
   one candidate trip.

Anything yielding more than one candidate is **ambiguous and reports as ambiguous**,
naming the candidates. This is the `project_status` ambiguity rule applied to a new
surface: completing the wrong milestone is a silent data error that looks like
progress, and stamping the wrong confirmation on a flight is the same shape.

**Rule: never infer a trip from the airport pair alone with no date match.** He flies
SEA→PHX repeatedly; the pair is not an identifier.

## 3. Airport codes to city names

A tracked data file, not a dict literal in code — the same reasoning as
`app/scaffold/template/` being tracked files: a mapping regenerated or hand-extended
from memory drifts, and a reviewable diff is the point.

`backend/app/data/airports.csv` (or `.json`): IATA code, city name, IANA timezone,
and airport name. Sourced from a public IATA/OpenFlights dataset, committed once.

**An unrecognised code renders as the code, never as a guess.** This is `unknown`
never mapping to green, in a new costume. `SEA` → *Seattle*; `XYZ` → *XYZ*, with no
apology and no invention. A test asserts an unknown code round-trips unchanged.

The timezone column is not decoration — see §4.

## 4. Times, and the timezone defect this exposes

`backend/app/handlers/scheduling.py::_fmt_event` converts every event to `_tz()`, the
owner's zone. For a flight arrival **that is wrong**, and it is wrong in a way that
reads as right.

SEA→PHX departing 07:00 arriving 10:00: rendered in Pacific, the flight takes three
hours. It takes two. The owner reading "arrives 10:00" and planning a 10:30 meeting in
Phoenix is off by an hour in the direction that makes him late.

**Rule: state the zone whenever the departure and arrival zones differ.** Departure in
the origin's local time, arrival in the destination's local time, both labelled. Where
the zones match, no labels — an unnecessary zone on every domestic hop is noise, and a
readback full of noise is one he stops reading.

The zones come from the airport data file (§3), **not** from the calendar event's
offset — an event created in the owner's zone carries his offset regardless of where
the aircraft lands.

Where the zone is unknown because the code is unrecognised, **report the time
unlabelled and say the zone is unknown.** Do not silently fall back to the owner's
zone; that is the fabricated-green failure.

## 5. Drive time — three real code gaps

### 5.1 `get_traffic` cannot look into the future

`backend/app/handlers/maps.py::_get_traffic` hardcodes `"departure_time": "now"`. The
module docstring is explicit that this is deliberate and is *"the whole point of the
feature"* — for a commute question, it is exactly right.

For a flight three weeks out it produces today's traffic wearing a future timestamp.

**Add an optional future departure time**, passed to Google as a Unix timestamp with
`traffic_model=best_guess`. Do not change the default: `"now"` stays the behaviour for
every existing caller, and the commute path must not move.

### 5.2 The estimate must announce what kind of estimate it is

Google's `duration_in_traffic` for a future time is a **historical best-guess**, not a
live reading. Presenting it in the same words as a live commute estimate is fabricated
precision — the same family as `unknown` mapping to green and an empty collection
reading as absence.

**Rule: beyond a stated horizon, the estimate is labelled as typical rather than
current.** Recommend the horizon at **6 hours** (`airport_traffic_live_horizon_hours`,
runtime-tunable). Inside it: *"about 70 minutes in current traffic."* Outside it:
*"typically about 70 minutes at that hour."*

The distinction is not hedging. It tells him whether the number is worth re-checking on
the morning.

### 5.3 `_leave_by` is date-blind

`maps.py::_leave_by` parses `"9am"` and, if the time has passed, adds **one day**. It
has no concept of a date. For a flight on the 22nd it is unusable.

It needs to accept a full datetime. Keep the existing string parsing for the commute
callers; add the datetime path for this one.

## 6. The parking garage, and the origin

### 6.1 Never guess the garage

`OWNER_PLACES` already exists (`maps.py::_places`, parsed from `settings.owner_places`
as `name=address;name=address`). The garage is a named place — e.g.
`sea parking=<address>`.

**If it is unset, JARVIS says so and gives the flight summary without a drive
estimate.** She does not route to the terminal and call it parking. Routing to a
terminal for a man who intends to park is a wrong answer delivered confidently, and
the drop-off loop is not where the car goes.

Recommend a convention: `<code> parking`, so `SEA` resolves to `sea parking`. Where no
such place exists for the origin airport, that is the unset case above.

### 6.2 Origin defaults to home for anything not today

`_get_traffic` currently defaults the origin to the **current position fix**, and that
is the right default for *"how long to work"* asked from the marina.

It is the wrong default for a flight in three weeks. Routing from a fix at Skyline
Marina for a departure on the 22nd is nonsense dressed as personalisation.

**Rule: for a departure today, origin is the current fix (falling back to home). For
any departure not today, origin is home.** State the origin in the output either way —
a drive estimate whose starting point is unstated cannot be checked.

### 6.3 The 3.5 hours is a setting

`airport_arrival_lead_minutes`, default **210**, on
`app/runtime_settings.py::ALLOWED_KEYS` (min 30, max 480). Behavioural, not a secret,
and he will want to change it from the boat for an international departure.

**Two clocks, and they must not be collapsed:** *arrive at the garage* at
departure − lead; *leave home* at departure − lead − drive. Report both. The one he
acts on is the second; the one he checks against is the first.

## 7. Where this renders

| Surface | Behaviour |
|---|---|
| `calendar_lookup` | A flight-shaped event gets the enriched block; everything else is untouched |
| Morning brief `## Travel` | Enriched only when a flight is **within the brief's horizon**; a flight three weeks out stays a one-liner |
| Voice | Confirmation code spoken **character-grouped**, never as a word |
| On demand | A tool, so "tell me about my flight" works without a calendar range |

**The brief constraint is exception-first discipline.** `briefing.py` already suppresses
a quiet traffic report because *"no delay reported every morning is noise."* A full
drive-time block for a flight in three weeks is the same noise. Recommend enriching
inside **48 hours** of departure and leaving the existing one-liner beyond it.

**On voice:** `channels/voice_pipeline.py::_speakable` strips URLs. A confirmation code
like `HXKQ2R` read as a word is useless. Group it — *"H-X-K-Q-2-R"* — and consider
NATO alphabet for letters that collide over a phone line. This is the one place where
*more* verbosity is correct.

## 8. Failure modes, stated rather than discovered

| Missing | Behaviour |
|---|---|
| No matching trip | Times and route from the calendar; say the confirmation is not on file |
| No calendar event | Confirmation and route from the trip; say the times are not on file |
| Unknown airport code | Render the code; no city, no zone, no guess |
| Garage not configured | Full summary, no drive estimate, say why |
| Maps unconfigured | Existing `NOT_CONFIGURED` string |
| Ambiguous trip match | Name the candidates; attach nothing |

**Every one of these degrades to a partial summary. None of them fabricates a field,
and none of them suppresses the whole readback.** A summary missing one line is useful;
a summary that is silently absent is indistinguishable from having no flight.

## 9. What this deliberately does not do

- **No re-parsing of `Trip.raw` for dates.** Tempting — the raw email is retained
  precisely so a better parser can re-derive fields — but confirmation-email date
  formats are a swamp and a wrong departure time is worse than an absent one. Recorded
  as the trigger for a follow-on: **the calendar join failing often enough to be
  noticed.**
- **No check-in, no seat selection, no gate.** Read-only.
- **No new gated surface.** This is a read. It touches nothing irreversible.

## 10. Open questions for ratification

**10.1 — 48 hours as the brief-enrichment horizon?** Recommend yes.
**10.2 — 6 hours as the live-traffic horizon?** Recommend yes.
**10.3 — `<code> parking` naming convention in `OWNER_PLACES`?** Recommend yes; it
generalises without config changes when he flies from somewhere else.
**10.4 — Does the enriched readback go to voice at all, or written surfaces only?**
Recommend voice yes, with the grouped confirmation code — the question *"when do I
need to leave"* is asked from the car.

## 11. Test plan

| Property | Plant |
|---|---|
| Unknown IATA code renders as the code | Add a fallback that returns "Unknown City" |
| Zones are labelled when they differ | Force both zones to the owner's |
| Zones are NOT labelled when they match | Always label |
| Wrong-confirmation guard: ambiguous match attaches nothing | Take the first candidate |
| Airport-pair match without a date match is refused | Drop the date condition |
| Future departure time reaches the Maps call | Pin `departure_time` to `"now"` |
| Estimate beyond the horizon is labelled typical | Pin the label to "current traffic" |
| Origin is home for a non-today departure | Always use the current fix |
| Unset garage suppresses the estimate, not the summary | Fall back to the airport address |
| The commute path is unchanged | Assert `departure_time == "now"` with no explicit time |

**§2.7 throughout.** The airport-code test must plant a value no branch legitimately
produces — not `"SEA"`, which is a real code and coincides with a correct output.

**The last row is the regression guard that matters.** `_get_traffic` is on the morning
brief's critical path and is the only thing exercising `google_maps` liveness daily. A
change here that quietly altered the commute call would degrade a health substrate, and
the brief would keep composing cleanly while it happened.
