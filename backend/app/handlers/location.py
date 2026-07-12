"""Location — where the phone says it is.

TRUST DIRECTION. The phone PUSHES; JARVIS receives. Nothing here lets a voice on
a phone line reach into the device. That asymmetry is deliberate and it is the
whole reason this is safe to build: a spoofed caller can *ask where you are*
(annoying), but cannot *make your phone do anything* (bad).

AUTH. The endpoint can't use Twilio signature validation — Tasker isn't Twilio.
So it takes a shared secret in a header. That secret lives on the phone and in
Fly, and nowhere else. It is not a whitelist and not caller ID: possession of the
secret IS the authentication, which makes it strictly stronger than the voice
channel's.

STALENESS IS THE WHOLE GAME. A location is only useful if you know how old it is.
A fix from three hours ago will confidently route you from a coffee shop you left
at breakfast. So every reading carries an age, and anything past
`location_max_age_minutes` is treated as unknown rather than trusted — falling
back to home, and SAYING SO.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import LocationPing

log = logging.getLogger(__name__)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:
        return ZoneInfo("UTC")


def record_ping(db, lat: float, lon: float, accuracy_m: float = 0.0,
                source: str = "phone", label: str = "") -> LocationPing:
    """Store a position report. Called by the /api/location route."""
    p = LocationPing(
        lat=lat, lon=lon,
        accuracy_m=accuracy_m or 0.0,
        source=source[:32],
        label=label[:120],
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    # Keep the table from growing without bound. We only ever care about the
    # latest fix; history is for debugging, not features.
    old = (
        db.execute(
            select(LocationPing)
            .order_by(LocationPing.id.desc())
            .offset(settings.location_keep_pings)
        )
        .scalars()
        .all()
    )
    for row in old:
        db.delete(row)
    if old:
        db.commit()

    return p


def latest(db) -> LocationPing | None:
    return (
        db.execute(select(LocationPing).order_by(LocationPing.id.desc()).limit(1))
        .scalars()
        .first()
    )


def age_minutes(p: LocationPing) -> float:
    ts = p.created_at
    if ts.tzinfo is None:             # SQLite hands back naive
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 60


def current_coords(db) -> str | None:
    """'47.123,-122.456' for the Maps API, or None if we don't reliably know.

    None means "don't guess." A stale fix will confidently route you from a place
    you left hours ago, which is worse than admitting you don't know — the caller
    can just say where they are.
    """
    p = latest(db)
    if p is None:
        return None
    if age_minutes(p) > settings.location_max_age_minutes:
        return None
    return f"{p.lat},{p.lon}"


def _where_am_i(args: dict, ctx: Context) -> str:
    p = latest(ctx.db)
    if p is None:
        return ("I don't have a location for you. Your phone needs to report one — "
                "see the Tasker setup.")

    age = age_minutes(p)
    when = (
        "just now" if age < 2
        else f"{round(age)} minutes ago" if age < 90
        else f"{round(age / 60)} hours ago"
    )

    out = f"Last position {when}"
    if p.label:
        out += f": {p.label}"
    out += f" ({p.lat:.4f}, {p.lon:.4f})"
    if p.accuracy_m:
        out += f", accurate to about {round(p.accuracy_m)} metres"
    out += "."

    if age > settings.location_max_age_minutes:
        out += (f" That's too old to rely on — I'll assume you're at home unless you "
                f"tell me otherwise.")
    return out


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "where_am_i",
            "description": (
                "The user's last reported position, from their phone, and how old it is. "
                "Use when they say 'from here', 'near me', 'where am I', or when a "
                "location would obviously help and they haven't given one."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        _where_am_i,
    )
