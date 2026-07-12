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
from app.timefmt import daytime, weekday_clock

log = logging.getLogger(__name__)

# Full calendar scope (not .readonly) so create_event can write. The service
# account must be re-shared on the calendar as "Make changes to events" — Reader
# is not enough. Read still works if only Reader is granted; writes will 403.
_SCOPES = ["https://www.googleapis.com/auth/calendar"]


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
    """Return a Google Calendar API client. Prefers OAuth over the service account.

    WHY THE PREFERENCE MATTERS. A service account CANNOT invite attendees on a
    consumer Google account. Google refuses outright:

        403 forbiddenForServiceAccounts
        "Service accounts cannot invite attendees without Domain-Wide
         Delegation of Authority."

    Domain-Wide Delegation is a Workspace feature. It does not exist for
    @gmail.com. So no amount of re-sharing the calendar fixes this — sharing
    grants the SA the right to WRITE, and it still cannot INVITE.

    OAuth has no such limit, because it acts as the owner, and a person is
    obviously allowed to invite people to their own meeting.

    So: OAuth if we have it, service account otherwise. Both are kept — the SA
    still works fine for reads and for events with no attendees, and a user who
    hasn't done the OAuth consent keeps everything they had.
    """
    from app import google_oauth

    svc = google_oauth.calendar_service()
    if svc is not None:
        return svc

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
            when = weekday_clock(dt)
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


def register_gated(reg: Registry) -> None:
    """Gated — top-level registry only. See secretary.register_gated."""
    reg.register(
        {
            "name": "create_event",
            "description": (
                "Create an event on the user's Google Calendar (a meeting, appointment, "
                "or block of time). This writes to their real calendar and, if attendees "
                "are given, EMAILS THEM AN INVITE — so the system will require the user's "
                "explicit confirmation before it executes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "string",
                              "description": "ISO datetime, e.g. 2026-07-15T14:00. "
                                             "Resolve relative dates yourself first."},
                    "duration_minutes": {"type": "integer", "description": "Default 60."},
                    "location": {"type": "string"},
                    "description": {"type": "string"},
                    "attendees": {"type": "string",
                                  "description": "Comma-separated emails. They WILL be "
                                                 "emailed an invite."},
                },
                "required": ["title", "start"],
            },
        },
        _create_event,
        gated=True,                # notional is None -> confirmation ALWAYS required
        summarize=_summarize_event,
    )


def _create_event(args: dict, ctx: Context) -> str:
    """Create a calendar event. GATED — see register()."""
    service = _service()
    if service is None:
        return ("[calendar not configured] Set GOOGLE_SERVICE_ACCOUNT_JSON and share "
                "your calendar with the service account (Make changes to events).")

    title = (args.get("title") or "").strip()
    start_raw = (args.get("start") or "").strip()
    if not title or not start_raw:
        return "Need at least a title and a start time."

    tz = _tz()
    try:
        start = datetime.fromisoformat(start_raw)
    except ValueError:
        return (f"Could not parse the start time {start_raw!r}. Give an ISO datetime "
                f"like 2026-07-15T14:00. Nothing was created.")
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)

    minutes = int(args.get("duration_minutes") or 60)
    end = start + timedelta(minutes=minutes)

    body = {
        "summary": title,
        "start": {"dateTime": start.isoformat(), "timeZone": str(tz)},
        "end": {"dateTime": end.isoformat(), "timeZone": str(tz)},
    }
    if args.get("location"):
        body["location"] = args["location"]
    if args.get("description"):
        body["description"] = args["description"]
    attendees = [a.strip() for a in (args.get("attendees") or "").split(",") if a.strip()]
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]

    try:
        ev = service.events().insert(
            calendarId=settings.google_calendar_id, body=body,
            sendUpdates="all" if attendees else "none",
        ).execute()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        # This one misdirects badly: it looks like a sharing problem and isn't.
        # A service account can never invite attendees on a consumer account, no
        # matter how the calendar is shared.
        if "forbiddenForServiceAccounts" in msg or "Domain-Wide Delegation" in msg:
            return ("I can create the event but I can't invite attendees — that needs "
                    "Google OAuth, which isn't connected. Either connect Google, or say "
                    "the word and I'll create it with no attendees.")
        if "insufficientPermissions" in msg or "403" in msg:
            return (f"Google refused: {e}\n"
                    f"Check the calendar is shared with the service account as 'Make "
                    f"changes to events', or connect Google via OAuth.")
        return f"Could not create the event: {e}"

    when = daytime(start.astimezone(tz))
    return f"Created: {title} — {when} ({minutes} min). {ev.get('htmlLink', '')}"


def _summarize_event(args: dict) -> str:
    """Readback line for the confirmation gate."""
    who = args.get("attendees")
    bit = f" with {who}" if who else ""
    return (f"create calendar event '{args.get('title', '?')}' at "
            f"{args.get('start', '?')} for {args.get('duration_minutes', 60)} minutes{bit}")
