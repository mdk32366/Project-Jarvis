"""Scheduling handler — read-only Google Calendar for the `scheduling` agent.

Auth: a Google **service account** (no OAuth redirect). Put the service-account
JSON key in GOOGLE_SERVICE_ACCOUNT_JSON and share your calendar with the service
account's email (Reader). Read-only by design; creating/editing events is a
governed write action to be added later behind the confirmation gate.

Google libraries are imported lazily so the app/tests don't require them unless
the calendar is actually configured.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.handlers.base import Context, Registry

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _load_sa_info(raw: str) -> dict | None:
    """Parse the service-account key from raw JSON OR base64-encoded JSON.

    Base64 is accepted so the key can be set as a Fly secret without fighting
    quoting/newlines on the command line.
    """
    import json

    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.startswith("{"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    return json.loads(raw)


def _service():
    """Return a Google Calendar API client, or None if not configured."""
    info = _load_sa_info(settings.google_service_account_json)
    if info is None:
        return None

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:
        return ZoneInfo("UTC")


def _time_window(rng: str):
    """Map a natural range to (start, end, label). All tz-aware in the local tz."""
    tz = _tz()
    now = datetime.now(tz)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    r = (rng or "today").strip().lower()
    if "tomorrow" in r:
        start = midnight + timedelta(days=1)
        return start, start + timedelta(days=1), "tomorrow"
    if "today" in r:
        return midnight, midnight + timedelta(days=1), "today"
    if "week" in r:
        return now, now + timedelta(days=7), "this week"
    # default: the next 7 days of upcoming events
    return now, now + timedelta(days=7), "in the next 7 days"


def _fetch_events(service, cal_id: str, start: datetime, end: datetime):
    resp = (
        service.events()
        .list(calendarId=cal_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
              singleEvents=True, orderBy="startTime", maxResults=20)
        .execute()
    )
    return resp.get("items", [])


def _fmt_event(ev: dict) -> str:
    start = ev.get("start", {})
    when = start.get("dateTime") or start.get("date") or "?"
    if "T" in when:  # timed event
        try:
            dt = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(_tz())
            when = dt.strftime("%a %-I:%M %p")
        except Exception:
            pass
    else:  # all-day
        when = f"{when} (all day)"
    title = ev.get("summary", "(no title)")
    loc = ev.get("location")
    return f"- {when}: {title}" + (f" @ {loc}" if loc else "")


def _calendar_lookup(args: dict, ctx: Context) -> str:
    service = _service()
    if service is None:
        return (
            "[calendar not configured] Set GOOGLE_SERVICE_ACCOUNT_JSON and share your "
            "Google Calendar with the service account's email (Reader access)."
        )
    start, end, label = _time_window(args.get("range", "today"))
    try:
        items = _fetch_events(service, settings.google_calendar_id, start, end)
    except Exception as e:  # never crash the loop
        return f"Error reading calendar: {e}"
    if not items:
        return f"No events {label}."
    return f"Events {label}:\n" + "\n".join(_fmt_event(e) for e in items)


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "calendar_lookup",
            "description": "Look up the user's Google Calendar events for a time range "
                           "(e.g. 'today', 'tomorrow', 'this week').",
            "input_schema": {
                "type": "object",
                "properties": {"range": {"type": "string",
                    "description": "Time range: today, tomorrow, this week, or next 7 days"}},
            },
        },
        _calendar_lookup,
    )
