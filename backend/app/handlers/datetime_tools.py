"""Current date/time awareness — TDD #11.

§4.1  get_current_datetime — pure local read of system clock; no network,
      no cache. Returns both JARVIS's own operating time and the user's
      best-known local time, always explicitly labelled.

      JARVIS's own timezone is settings.calendar_timezone — the single
      source of truth shared with the scheduler, quiet-hours logic, and all
      other timezone-aware code. Do NOT introduce a separate jarvis_tz
      setting; that's how you get silent drift between the clock JARVIS
      speaks and the clock the scheduler fires on.

§4.4  resolve_relative_date — resolves "August 4th", "next Tuesday",
      "tomorrow" to absolute datetimes.
      KEY INVARIANT: when the expression carries no explicit year, the
      resolver MUST produce the NEAREST FUTURE occurrence — never a past
      date. This is the regression fix for the July 2026 production incident
      (TDD §0 incident 2) where JARVIS searched for 2025 flights four
      consecutive times while the user repeated "August 4th 2026."

§4.3  flag_stale_dates — post-processing pass over sub-agent text output;
      annotates past-dated content with [stale: …] rather than silently
      dropping or passing it through (TDD §8 decision 2).
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.handlers.base import Context, Registry

# Tools whose presence in an agent's roster marks it as "date-bearing" (§4.2).
# These agents get get_current_datetime auto-injected as context before the LLM
# loop, so date reasoning is grounded before they touch any external content.
DATE_BEARING_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "fetch_page",
    "calendar_lookup",
    "search_flights",
    "list_trips",
})


# ── §4.1: get_current_datetime ────────────────────────────────────────────────

def _resolve_user_tz(ctx: Context) -> tuple[str, str]:
    """Return (tz_name, source) for the user's current timezone.

    SUPPORTED sources, highest priority first:
      location_report > default

    Audit M6: two richer sources were once documented in the precedence but
    never implemented, which made the contract over-promise ("I'm in Phoenix
    right now" had zero effect). They are formally FUTURE WORK, not a live
    precedence, and each belongs in its own TDD (see below), so this now
    documents only what actually runs:

    - `stated` (a tz the user asserts in conversation) — needs tz extraction from
      the live conversation, city/state → IANA mapping, and a TTL decision.
      Belongs in a conversation-intent TDD.
    - `active_trip` (the destination tz of the trip you're currently on) — needs
      Trip.destination_tz (no such field), plus logic for which trip is "active".
      Belongs in a travel TDD alongside the Trip model extension.

    The phone's location report already covers the common travel case (you're
    physically there), so the gap is narrow.
    """
    # location_report — use the most recent LocationPing if fresh enough.
    #    We have lat/lon; convert to IANA timezone name via timezonefinder.
    #    This is what makes "Matt is in Scottsdale in December" produce
    #    "America/Phoenix" (UTC-7, no DST) instead of "America/Los_Angeles"
    #    (UTC-8 in December, wrong by one hour).
    if ctx.db is not None:
        try:
            from app.config import settings
            from app.handlers.location import latest, age_minutes
            from app.models import LocationPing  # noqa: F401 — ensures import is valid
            ping = latest(ctx.db)
            if ping is not None and age_minutes(ping) <= settings.location_max_age_minutes:
                from timezonefinder import TimezoneFinder
                tf = TimezoneFinder()
                tz_name = tf.timezone_at(lat=ping.lat, lng=ping.lon)
                if tz_name:
                    return tz_name, "location_report"
        except Exception:
            pass  # library missing or DB error — fall through to default

    # default — falls back to user_tz_default (same source of truth as the
    # scheduler and quiet-hours logic).
    from app.config import settings
    default_tz = getattr(settings, "user_tz_default", None) or settings.calendar_timezone
    return default_tz, "default"


def _get_current_datetime(args: dict, ctx: Context) -> str:
    """Return the current real-world date and time as a JSON string.

    Both JARVIS's own time (settings.calendar_timezone — single source of
    truth) and the user's best-known local time are returned, always
    explicitly labelled. No external calls. No cache. Every invocation
    reflects actual "now."
    """
    from app.config import settings
    jarvis_tz_name = settings.calendar_timezone  # single source of truth
    jarvis_zi = ZoneInfo(jarvis_tz_name)

    now_utc = datetime.now(timezone.utc)
    now_jarvis = now_utc.astimezone(jarvis_zi)

    # strftime gives ±HHMM; reformat as ±HH:MM
    raw_offset = now_jarvis.strftime("%z")       # e.g. "-0700"
    utc_offset = f"{raw_offset[:3]}:{raw_offset[3:]}"  # "-07:00"

    user_tz_name, user_tz_source = _resolve_user_tz(ctx)
    try:
        user_zi = ZoneInfo(user_tz_name)
        now_user = now_utc.astimezone(user_zi)
        user_time_str = now_user.isoformat(timespec="seconds")
    except Exception:
        # Bad tz name — fall back to JARVIS's own time silently
        user_time_str = now_jarvis.isoformat(timespec="seconds")
        user_tz_name = jarvis_tz_name
        user_tz_source = "default"

    return json.dumps({
        "jarvis_time": now_jarvis.isoformat(timespec="seconds"),
        "jarvis_tz": jarvis_tz_name,
        "jarvis_utc_offset": utc_offset,
        "user_time": user_time_str,
        "user_tz": user_tz_name,
        "user_tz_source": user_tz_source,
        "utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


# ── §4.4: resolve_relative_date ───────────────────────────────────────────────

_MONTH_NAMES: dict[str, int] = {
    "january": 1,  "jan": 1,
    "february": 2, "feb": 2,
    "march": 3,    "mar": 3,
    "april": 4,    "apr": 4,
    "may": 5,
    "june": 6,     "jun": 6,
    "july": 7,     "jul": 7,
    "august": 8,   "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0,    "mon": 0,
    "tuesday": 1,   "tue": 1,  "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3,  "thu": 3,  "thur": 3, "thurs": 3,
    "friday": 4,    "fri": 4,
    "saturday": 5,  "sat": 5,
    "sunday": 6,    "sun": 6,
}

# Matches "[Month] [day][ordinal][, year]" — e.g. "August 4th" or "August 4th, 2026"
_MONTH_DAY_RE = re.compile(
    r"^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:[,\s]+(\d{4}))?$"
)

# Matches "in N days/weeks/months"
_IN_N_RE = re.compile(
    r"^in\s+(\d+)\s+(day|days|week|weeks|month|months)$"
)

# Matches "next [weekday]"
_NEXT_WEEKDAY_RE = re.compile(r"^next\s+([a-z]+)$")

# Used when falling back to dateutil to detect explicit year in raw text
_EXPLICIT_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _nearest_future_date(month: int, day: int, ref: date) -> Optional[date]:
    """Return the nearest future date with the given month and day.

    KEY INVARIANT: if today is that date (ref == candidate), roll forward one
    year — "August 4th" when today IS August 4th still means a future event.
    """
    try:
        candidate = date(ref.year, month, day)
    except ValueError:
        return None  # invalid date (Feb 30 etc.)
    if candidate <= ref:
        try:
            candidate = date(ref.year + 1, month, day)
        except ValueError:
            return None
    return candidate


def resolve_relative_date(
    text: str, reference_dt: datetime
) -> Optional[datetime]:
    """Resolve a relative date expression to an absolute datetime.

    KEY INVARIANT: when the expression carries no explicit year, resolve to
    the NEAREST FUTURE occurrence — never silently produce a past date.

    Returns None if the expression is unrecognisable. Callers must handle
    None explicitly — do NOT silently treat it as "now."
    """
    s = text.strip().lower()
    ref = reference_dt.date() if isinstance(reference_dt, datetime) else reference_dt
    tz = reference_dt.tzinfo if isinstance(reference_dt, datetime) else None

    def _dt(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=tz)

    # "today" / "tomorrow" / "yesterday"
    if s == "today":
        return _dt(ref)
    if s == "tomorrow":
        return _dt(ref + timedelta(days=1))
    if s == "yesterday":
        return _dt(ref - timedelta(days=1))

    # "this weekend" → upcoming Saturday
    if s in ("this weekend", "the weekend", "weekend"):
        days_to_sat = (5 - ref.weekday()) % 7
        if days_to_sat == 0:
            days_to_sat = 7  # today is Saturday → next Saturday
        return _dt(ref + timedelta(days=days_to_sat))

    # "in N days/weeks/months"
    m = _IN_N_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).rstrip("s")  # normalise plural
        if unit == "day":
            return _dt(ref + timedelta(days=n))
        if unit == "week":
            return _dt(ref + timedelta(weeks=n))
        if unit == "month":
            # Month arithmetic: keep day, roll year if month overflows
            new_month = ref.month + n
            new_year = ref.year + (new_month - 1) // 12
            new_month = (new_month - 1) % 12 + 1
            clamped_day = min(ref.day, calendar.monthrange(new_year, new_month)[1])
            return _dt(date(new_year, new_month, clamped_day))

    # "next [weekday]"
    m = _NEXT_WEEKDAY_RE.match(s)
    if m:
        wday = _WEEKDAY_NAMES.get(m.group(1))
        if wday is not None:
            days_ahead = (wday - ref.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # "next Monday" when today is Monday → 7 days
            return _dt(ref + timedelta(days=days_ahead))

    # "[Month] [day][ordinal][, year]"
    m = _MONTH_DAY_RE.match(s)
    if m:
        month_name = m.group(1)
        day = int(m.group(2))
        explicit_year = int(m.group(3)) if m.group(3) else None
        month = _MONTH_NAMES.get(month_name)
        if month is not None and 1 <= day <= 31:
            if explicit_year is not None:
                # Explicit year: honour exactly — user said "August 4th 2026"
                try:
                    return _dt(date(explicit_year, month, day))
                except ValueError:
                    return None
            else:
                # KEY INVARIANT: no year → nearest future occurrence
                d = _nearest_future_date(month, day, ref)
                return _dt(d) if d is not None else None

    # Fallback: dateutil for ISO strings, full dates, etc.
    try:
        from dateutil import parser as _du
        from dateutil.relativedelta import relativedelta as _rd

        parsed = _du.parse(
            text,
            default=datetime(ref.year, ref.month, ref.day),
            fuzzy=False,
        )
        # Apply future-bias invariant if no explicit year in the raw string
        if not _EXPLICIT_YEAR_RE.search(text) and parsed.date() <= ref:
            parsed = parsed + _rd(years=1)
        return parsed.replace(tzinfo=tz)
    except Exception:
        return None


# ── §4.3: flag_stale_dates ────────────────────────────────────────────────────

# ISO date: 2024-03-15 or 2024/03/15
_ISO_DATE_RE = re.compile(r"\b(\d{4}[-/]\d{2}[-/]\d{2})\b")

# Written date: "March 15, 2024" or "March 15 2024"
_WRITTEN_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    re.IGNORECASE,
)


def flag_stale_dates(text: str, reference_dt: datetime) -> tuple[str, list[str]]:
    """Annotate PAST dates in sub-agent output so the model can't relay a stale
    date as current. Returns (annotated_text, list_of_flag_messages).

    - Past date → appended "[stale: YYYY-MM-DD]"
    - Present/future date → unchanged

    SCOPE (audit M7): only past-date staleness is flagged. The earlier
    "outside the request's intended window" check was never wired — it needs the
    caller to infer a window ("next 7 days") from the request, which isn't
    available at the run_agent call site — so it (and its dead `window_start` /
    `window_end` parameters) were removed rather than left as a false claim.
    Request-window flagging remains future work in TDD-datetime-awareness §4.3.

    Flight offer expiry is NOT handled here — Duffel's native rejection is the
    transactional control for that. This function is for *content* (web snippets,
    search results, calendar text).

    Silent filtering is explicitly rejected. Stale content is annotated and
    surfaced so the orchestrator or user can decide what to do. A system that
    quietly removes things cannot be audited (TDD §8 decision 2).
    """
    flags: list[str] = []
    ref_date = reference_dt.date()

    def _annotate(m: re.Match) -> str:
        raw = m.group(0)
        try:
            from dateutil import parser as _du
            parsed = _du.parse(raw, fuzzy=True).date()
        except Exception:
            return raw  # unparseable — leave untouched

        if parsed < ref_date:
            msg = f"stale: dated {parsed.isoformat()}, reference {ref_date.isoformat()}"
            flags.append(msg)
            return f"{raw} [stale: {parsed.isoformat()}]"

        return raw

    result = _ISO_DATE_RE.sub(_annotate, text)
    result = _WRITTEN_DATE_RE.sub(_annotate, result)
    return result, flags


# ── Registration ──────────────────────────────────────────────────────────────

def register(reg: Registry) -> None:
    """Register get_current_datetime — ungated, universal, no side effects.

    Must be called in BOTH the top-level orchestrator registry branch AND the
    sub-agent registry branch in build_registry() (TDD §4.1).
    """
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
