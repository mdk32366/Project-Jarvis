"""Maps — traffic, directions, and places.

THE QUESTION THIS ANSWERS: "what time do I need to leave?"

That's a real question you ask most mornings, it has a real answer, and it
changes hour to hour. It's the highest-frequency thing JARVIS can do for you.

It also composes with everything else already built: she reads your calendar,
sees the 9am in Bothell, checks traffic from Stanwood, and CALLS YOU at 7:15 to
say leave now. That's the morning brief earning its keep, and it needs no new
machinery — just this.

NAMED PLACES. `OWNER_PLACES` lets you say "how long to work" rather than reciting
an address. Home is the default origin; anything unrecognized is passed to Google
as a free-text query, which handles "Skyline Marina Anacortes" perfectly well.

TRAFFIC MODEL. `departure_time=now` is what makes this live rather than a
timetable — without it Google returns free-flow duration, which is useless at
8am. `duration_in_traffic` is the number that matters, and the difference between
the two is the delay worth reporting.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.handlers.base import Context, Registry
from app.timefmt import clock

log = logging.getLogger(__name__)

_DIRECTIONS = "https://maps.googleapis.com/maps/api/directions/json"
_PLACES = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_TIMEOUT = 20.0

NOT_CONFIGURED = (
    "[maps not configured] I can't check traffic — that needs a Google Maps API key "
    "(GOOGLE_MAPS_API_KEY), with the Directions and Places APIs enabled."
)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:
        return ZoneInfo("UTC")


def _places() -> dict[str, str]:
    """Parse OWNER_PLACES into {name: address}."""
    out: dict[str, str] = {}
    for chunk in (settings.owner_places or "").split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            out[k.strip().lower()] = v.strip()
    if settings.owner_home_address:
        out.setdefault("home", settings.owner_home_address)
    if settings.owner_work_address:
        out.setdefault("work", settings.owner_work_address)
        out.setdefault("the office", settings.owner_work_address)
    return out


def _resolve(where: str) -> str:
    """Named place -> address. Anything else passes through to Google as-is,
    which copes fine with 'Skyline Marina Anacortes'.

    Strips a leading article: people say "the boat", not "boat". Without this,
    "how long to the boat" silently becomes a Google text search for the literal
    string "the boat", which returns nonsense.
    """
    if not where:
        return ""
    key = where.strip().lower()
    places = _places()
    if key in places:
        return places[key]
    for article in ("the ", "my "):
        if key.startswith(article) and key[len(article):] in places:
            return places[key[len(article):]]
    return where.strip()


def _mins(seconds: int) -> str:
    """Spoken-friendly. Never '4,812 seconds', never '1.34 hours'."""
    m = round(seconds / 60)
    if m < 60:
        return f"{m} minutes"
    h, rem = divmod(m, 60)
    if rem == 0:
        return f"{h} hour" if h == 1 else f"{h} hours"
    return f"{h} hour {rem} minutes" if h == 1 else f"{h} hours {rem} minutes"


def _get_traffic(args: dict, ctx: Context) -> str:
    if not settings.google_maps_api_key:
        return NOT_CONFIGURED

    dest = _resolve(args.get("destination") or "")
    if not dest:
        known = ", ".join(_places()) or "none saved"
        return f"Where to? Known places: {known}."

    origin = _resolve(args.get("origin") or "") or settings.owner_home_address
    if not origin:
        return "I don't know where you're starting from. Set OWNER_HOME_ADDRESS."

    # An arrival deadline is the actually-useful mode: "I need to BE there at 9"
    # is the real question, not "how long does it take."
    arrive_by = (args.get("arrive_by") or "").strip()

    params = {
        "origin": origin,
        "destination": dest,
        "key": settings.google_maps_api_key,
        "mode": (args.get("mode") or "driving"),
        # WITHOUT departure_time, Google returns free-flow duration — a timetable,
        # not traffic. This one parameter is the whole point of the feature.
        "departure_time": "now",
        "traffic_model": "best_guess",
    }

    import httpx

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(_DIRECTIONS, params=params)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        log.error("directions failed: %s", e)
        return f"Couldn't reach Google Maps: {e}"

    status = data.get("status")
    if status == "ZERO_RESULTS":
        return f"No route found from {origin} to {dest}."
    if status == "REQUEST_DENIED":
        return ("Google denied the maps request — check GOOGLE_MAPS_API_KEY and that the "
                "Directions API is enabled.")
    if status != "OK":
        return f"Maps error: {status}. {data.get('error_message', '')}"

    leg = data["routes"][0]["legs"][0]
    free = leg["duration"]["value"]
    live = (leg.get("duration_in_traffic") or leg["duration"])["value"]
    miles = leg["distance"]["text"]
    summary = data["routes"][0].get("summary", "")

    delay = live - free
    now = datetime.now(_tz())
    eta = now + timedelta(seconds=live)

    lines = [f"{_mins(live)} to {args.get('destination') or dest}, {miles} via {summary}."]

    # Only mention traffic when there IS traffic. "No delay" said every morning is
    # noise; a 25-minute delay is the entire reason you asked.
    if delay >= 300:
        lines.append(f"That's {_mins(delay)} slower than usual — heavy traffic.")
    elif delay >= 120:
        lines.append(f"About {_mins(delay)} of delay.")
    else:
        lines.append("Traffic is light.")

    lines.append(f"Leaving now puts you there about {clock(eta, ampm=False)}.")

    if arrive_by:
        leave = _leave_by(arrive_by, live)
        if leave:
            lines.append(f"To be there by {arrive_by}, leave by {clock(leave)}.")

    return " ".join(lines)


def _leave_by(arrive_by: str, travel_seconds: int) -> datetime | None:
    """'9am' / '09:00' / ISO -> when to leave. None if unparseable — and an
    unparseable time must be dropped, never guessed: a wrong leave-time is worse
    than none at all."""
    tz = _tz()
    now = datetime.now(tz)
    raw = arrive_by.strip().lower().replace(".", "")

    try:
        dt = datetime.fromisoformat(arrive_by)
        target = dt if dt.tzinfo else dt.replace(tzinfo=tz)
        return target - timedelta(seconds=travel_seconds)
    except ValueError:
        pass

    import re

    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < now:
        target += timedelta(days=1)     # they mean tomorrow
    return target - timedelta(seconds=travel_seconds)


def _find_place(args: dict, ctx: Context) -> str:
    """Search for a place — restaurants, shops, anything. Read-only.

    This is also the honest answer to "book me a table": there is no consumer API
    for OpenTable, so JARVIS RESEARCHES and you book. Same shape as flights.
    """
    if not settings.google_maps_api_key:
        return NOT_CONFIGURED

    query = (args.get("query") or "").strip()
    if not query:
        return "What are you looking for?"

    near = _resolve(args.get("near") or "") or settings.owner_home_address
    params = {
        "query": f"{query} near {near}" if near else query,
        "key": settings.google_maps_api_key,
    }
    if args.get("open_now"):
        params["opennow"] = "true"

    import httpx

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(_PLACES, params=params)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't reach Google Maps: {e}"

    if data.get("status") == "REQUEST_DENIED":
        return "Google denied the request — check the Places API is enabled."
    results = data.get("results") or []
    if not results:
        return f"Nothing found for {query}."

    lines = []
    for p in results[: int(args.get("limit") or 4)]:
        bits = [p.get("name", "?")]
        if p.get("rating"):
            bits.append(f"{p['rating']} stars")
        price = p.get("price_level")
        if price is not None:
            bits.append("$" * max(1, price))
        if p.get("opening_hours", {}).get("open_now") is False:
            bits.append("closed now")
        lines.append(", ".join(bits) + f" — {p.get('formatted_address', '')}")

    out = "\n".join(f"{i}. {l}" for i, l in enumerate(lines, 1))
    return (f"{len(results)} results:\n{out}\n"
            f"I can't book a table — no restaurant API allows that. Say the word and "
            f"I'll email you these and open a task to call.")


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "get_traffic",
            "description": (
                "Live driving time WITH CURRENT TRAFFIC between two places, and when to "
                "leave to arrive on time. Use for 'how long to X', 'what time should I "
                "leave', 'how's traffic'. Origin defaults to home. Named places like "
                "'work' or 'the boat' are understood."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "destination": {"type": "string",
                                    "description": "Named place ('work') or an address."},
                    "origin": {"type": "string", "description": "Defaults to home."},
                    "arrive_by": {"type": "string",
                                  "description": "e.g. '9am'. Gives a leave-by time — this "
                                                 "is usually what they actually want."},
                    "mode": {"type": "string",
                             "enum": ["driving", "walking", "bicycling", "transit"]},
                },
                "required": ["destination"],
            },
        },
        _get_traffic,
    )
    reg.register(
        {
            "name": "find_place",
            "description": (
                "Find restaurants, shops, or businesses near a location, with ratings and "
                "hours. Read-only — JARVIS cannot make a reservation (no restaurant "
                "booking API is available), but she can research and open a task."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "e.g. 'italian restaurant'"},
                    "near": {"type": "string", "description": "Defaults to home."},
                    "open_now": {"type": "boolean"},
                    "limit": {"type": "integer", "description": "Default 4."},
                },
                "required": ["query"],
            },
        },
        _find_place,
    )
