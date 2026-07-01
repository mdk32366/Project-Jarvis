"""Scheduling handler — calendar access for the `scheduling` agent.

Phase: STUB. Returns a clear "not yet connected" message so delegation works
end-to-end now. Next increment wires Google Calendar (OAuth + Calendar API).
"""

from app.handlers.base import Context, Registry


def _calendar_lookup(args: dict, ctx: Context) -> str:
    when = args.get("range", "today")
    return (
        f"[calendar not yet connected] Would look up events for '{when}'. "
        "Google Calendar integration is coming in the next update."
    )


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "calendar_lookup",
            "description": "Look up the user's calendar events for a time range (e.g. 'today', 'this week').",
            "input_schema": {
                "type": "object",
                "properties": {"range": {"type": "string", "description": "Time range, e.g. today, tomorrow, this week"}},
            },
        },
        _calendar_lookup,
    )
