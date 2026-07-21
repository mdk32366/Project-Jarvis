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
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import LocationPing, LocationRequest

log = logging.getLogger(__name__)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.calendar_timezone)
    except Exception:
        return ZoneInfo("UTC")


def record_ping(db, lat: float, lon: float, accuracy_m: float = 0.0,
                source: str = "phone", label: str = "",
                request_id: int | None = None) -> LocationPing:
    """Store a position report. Called by the /api/location route.

    `request_id` links the fix to the ask it answers, when there was one. None is
    normal and not an error — a manual force-run is still a real position.
    """
    p = LocationPing(
        lat=lat, lon=lon,
        accuracy_m=accuracy_m or 0.0,
        source=source[:32],
        label=label[:120],
        request_id=request_id,
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


# ── The pull side: JARVIS asks, the phone answers ────────────────────────────
#
# TRUST DIRECTION, RESTATED. The inversion does not weaken the asymmetry the push
# design was built on. The server sends one content-free "send me a fix" nudge to
# a device that opted in by installing the receiver; it cannot make the phone do
# anything else. A spoofed caller still cannot reach into the device.


def new_request(db, trigger: str = "scheduled") -> LocationRequest:
    """Mint a request, dispatch it, and record whether the dispatch itself worked.

    The row is inserted and committed BEFORE dispatch: if the AutoRemote POST hangs
    or the process dies mid-call, the ask still exists to be swept to `timeout`. An
    un-recorded dispatch is indistinguishable from a scheduler that never ran, and
    telling those two apart is the whole reason this table exists.
    """
    from app.providers import autoremote

    req = LocationRequest(nonce=secrets.token_urlsafe(16), trigger=trigger, status="pending")
    db.add(req)
    db.commit()
    db.refresh(req)

    ok, err = autoremote.request_location(req.nonce)
    req.dispatch_ok = ok
    req.dispatch_error = (err or "")[:300]
    db.commit()
    if not ok:
        log.warning("location pull dispatch failed (request %s): %s", req.id, err)
    return req


def close_request(db, nonce: str) -> LocationRequest | None:
    """Resolve the ask a ping is answering. Returns the row, or None if unknown.

    A nonce that is already `fulfilled` or `timeout` is NOT an error and does not
    change the status: a late answer is a real location fix, and the caller records
    the ping regardless. Leaving the request `timeout` while still linking the ping
    is deliberate — a chronically-late phone should read as unresponsive even
    though its fixes remain usable.
    """
    req = db.query(LocationRequest).filter(LocationRequest.nonce == nonce).first()
    if req is None:
        return None
    if req.status == "pending":
        req.status = "fulfilled"
        req.responded_at = datetime.now(timezone.utc)
    else:
        log.info("location ping answered request %s late (status=%s)", req.id, req.status)
    return req


def sweep_timeouts(db) -> int:
    """Age out unanswered requests. Returns how many were swept.

    Without this, `pending` rows accumulate forever and the responsiveness check
    can never read anything but green — nothing would ever be false.
    """
    from app.runtime_settings import get_effective

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=get_effective(db, "location_pull_timeout_seconds")
    )
    stale = (
        db.query(LocationRequest)
        .filter(LocationRequest.status == "pending")
        .all()
    )
    n = 0
    for req in stale:
        ts = req.requested_at
        if ts is None:
            continue
        if ts.tzinfo is None:                     # SQLite hands back naive
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            req.status = "timeout"
            n += 1
    if n:
        db.commit()
        log.info("swept %d unanswered location request(s) to timeout", n)
    return n


def in_active_hours(db, now: datetime | None = None) -> bool:
    """Is it a time of day the owner expects to be moving?

    Shares the runtime window with the freshness check rather than re-deriving it —
    two different answers to "are we in active hours?" is a bug waiting to happen.
    """
    from app.runtime_settings import get_effective

    start = get_effective(db, "location_active_start_hour")
    end = get_effective(db, "location_active_end_hour")
    hour = (now or datetime.now(timezone.utc)).astimezone(_tz()).hour
    return (start <= hour < end) if start <= end else (hour >= start or hour < end)


def due_for_pull(db, now: datetime | None = None) -> bool:
    """Should a scheduled pull go out on this tick?

    Interval-since-last-request, NOT wall-clock slots: after an outage this yields
    exactly ONE make-up request rather than a burst of them. A burst is a battery
    event on the phone, which is precisely the sort of side effect that erodes
    trust in the system that caused it.
    """
    from app.runtime_settings import get_effective

    if not get_effective(db, "location_pull_enabled"):
        return False
    now = now or datetime.now(timezone.utc)
    if not in_active_hours(db, now):
        return False

    last = (
        db.query(LocationRequest)
        .filter(LocationRequest.trigger == "scheduled")
        .order_by(LocationRequest.id.desc())
        .first()
    )
    if last is None:
        return True
    ts = last.requested_at
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    interval = get_effective(db, "location_pull_interval_minutes")
    return (now - ts).total_seconds() / 60 >= interval


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
