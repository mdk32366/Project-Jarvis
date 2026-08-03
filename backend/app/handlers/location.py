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
                request_id: int | None = None,
                trigger: str | None = None) -> LocationPing:
    """Store a position report. Called by the /api/location route.

    `request_id` links the fix to the ask it answers, when there was one. None is
    normal and not an error — a manual push is still a real position.

    `trigger` records how the fix arrived ("pull" / "manual"). Descriptive only.
    """
    p = LocationPing(
        lat=lat, lon=lon,
        accuracy_m=accuracy_m or 0.0,
        source=source[:32],
        label=label[:120],
        request_id=request_id,
        trigger=(trigger[:16] if trigger else None),
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    # Keep the table from growing without bound. We only ever care about the
    # latest fix; history is for debugging, not features.
    #
    # ASYMMETRY WORTH KNOWING BEFORE YOU TIDY IT: `location_pings` prunes,
    # `location_requests` does NOT. So a `timeout` request whose answering ping
    # has aged out reads as retroactively SILENT — the evidence is gone while the
    # row that needs explaining remains, and "never answered" and "answered, then
    # the proof expired" become indistinguishable.
    #
    # Step 4a (2026-08-03) fixed that incidentally rather than deliberately:
    # `responded_at` lives on the request, which is the unpruned side, so the fact
    # of a late answer now outlives the ping that carried it. That was a side
    # effect of putting the field where §4.5 wanted it — recorded here because it
    # is now load-bearing and nobody argued for it.
    #
    # `location_keep_pings` is DEPLOY-ONLY (not on the runtime allow-list) while
    # `location_pull_interval_minutes` floors at 5. At 15-minute cadence 200 pings
    # is ~50 hours; at 5-minute cadence it is ~16 — shorter than the 24-hour
    # window the diagnostic order queries. Tightening the interval to chase a
    # fault silently shortens the evidence available to chase it with.
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
    req.relay_accepted = ok
    req.relay_error = (err or "")[:300]
    db.commit()
    if not ok:
        log.warning("location pull relay rejected (request %s): %s", req.id, err)
    return req


def close_request(db, nonce: str) -> LocationRequest | None:
    """Resolve the ask a ping is answering. Returns the row, or None if unknown.

    A nonce that is already `fulfilled` or `timeout` is NOT an error and does not
    change the status: a late answer is a real location fix, and the caller records
    the ping regardless. Leaving the request `timeout` while still linking the ping
    is deliberate — a chronically-late phone should read as unresponsive even
    though its fixes remain usable.

    TWO FACTS, TWO FIELDS — and collapsing them is what left the field blank:

        `status`       = did it arrive IN TIME?
        `responded_at` = WHEN did it arrive?

    `responded_at` used to be written only on the `pending` branch, so it read
    NULL for exactly the case it would have been most useful for: a late answer.
    `check_location_responsiveness` therefore could not tell "answered late" from
    "never answered" — the two faults that need different runbooks and different
    machines. It is now stamped on both branches.
    """
    req = db.query(LocationRequest).filter(LocationRequest.nonce == nonce).first()
    if req is None:
        return None
    if req.status == "pending":
        req.status = "fulfilled"
        req.responded_at = datetime.now(timezone.utc)
    else:
        # FIRST ANSWER WINS. A retrying phone can deliver the same nonce more than
        # once; overwriting would drift the measured latency upward on every
        # retry, making a late phone look worse the harder it tries.
        if req.responded_at is None:
            req.responded_at = datetime.now(timezone.utc)
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


def _attribute_layer(db, *, window_minutes: int = 90) -> str:
    """Which LAYER of the pull loop is quiet — stated as fact, never as cause.

    THE TOOL NAMES THE LAYER AND STOPS. Not "your Tasker profile is disabled":
    that is a guess wearing a fact's clothes, and it is the netstatus-stub defect
    in alert form. The runbooks own the sub-layer; the alert points at the layer
    so the owner starts in the right place instead of the plausible one.

    THE LAST TWO BRANCHES MUST NOT COLLAPSE. "Not answering" and "answering late"
    are different machines with different checklists — a phone answering late has
    a demonstrably working config, because something answered. Keeping them apart
    is the whole point of the #69 split, and here it reaches the owner's ear
    rather than the status page.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    rows = (
        db.query(LocationRequest)
        .filter(LocationRequest.trigger == "scheduled",
                LocationRequest.requested_at >= since)
        .all()
    )
    if not rows:
        return "JARVIS isn't sending location requests"

    accepted = [r for r in rows if r.relay_accepted]
    if not accepted:
        return "requests are going out but the relay is rejecting them"

    answered_late = [r for r in accepted
                     if r.status == "timeout" and r.responded_at is not None]
    answered_at_all = [r for r in accepted if r.status == "fulfilled"] + answered_late
    if not answered_at_all:
        return "requests are being accepted but the phone isn't answering"
    if answered_late:
        return ("the phone is answering late — fixes arrive, but too stale to use")
    return "requests are going out and being answered"


def _check_location_freshness(args: dict, ctx: Context) -> str:
    """Freshness as prose, for the absence watch to read.

    TWO READERS, TWO JOBS. The LLM judge matches this against the watch's
    condition string, so the AGE and the ACTIVE-HOURS state must be stated
    plainly. The alert call carries it VERBATIM as the observation, so the layer
    attribution has to be in here too — `opening` is written once at watch
    creation and is static, which makes this the only dynamic channel into the
    call. If the layer isn't in this string, the alert cannot name it.
    """
    from app.runtime_settings import get_effective

    newest = latest(ctx.db)
    active = in_active_hours(ctx.db)
    window = "during active hours" if active else "outside active hours"

    if newest is None:
        return (f"No position fix has ever been recorded ({window}). "
                f"The phone has not been enrolled yet.")

    age = age_minutes(newest)
    stale_after = get_effective(ctx.db, "location_stale_after_minutes")

    if age <= stale_after:
        return (f"Last position fix {int(age)} minutes ago ({window}); "
                f"fresh (stale after {stale_after} minutes).")

    layer = _attribute_layer(ctx.db)

    if not active:
        # REPORT ALWAYS, ESCALATE ONLY IN-HOURS — and the escalation here is the
        # PHRASING, because an LLM judge reads this against the watch condition.
        # §5.3 said "confirm, don't rebuild" on the grounds that the health check
        # already suppresses out of hours. It does; but the watch reads THIS
        # function, not that one, and this had no such branch. Stating the age
        # without the stale phrasing keeps the report honest while making the
        # condition unmatchable overnight — rather than leaving a structural fact
        # to a judge that merely fails closed.
        return (f"Last position fix {int(age)} minutes ago, outside active hours — "
                f"not treated as a fault. Where it would point: {layer}.")

    return (f"No position fix has registered in {int(age)} minutes ({window}); "
            f"stale after {stale_after} minutes. Where it stopped: {layer}.")


def _location_ping_log(args: dict, ctx: Context) -> str:
    """Recent position reports, newest first — the debugging view.

    STATES ITS OWN RETENTION HORIZON when it is showing the full table. Without
    that, "no older pings" reads as "no older activity", which is a fabricated
    absence — and the horizon moves with the pull interval, so the reader cannot
    infer it.
    """
    try:
        n = max(1, min(int(args.get("n") or 20), 100))
    except (TypeError, ValueError):
        n = 20

    rows = (
        ctx.db.query(LocationPing)
        .order_by(LocationPing.id.desc())
        .limit(n)
        .all()
    )
    if not rows:
        return "No position reports recorded yet."

    lines = []
    for p in rows:
        age = age_minutes(p)
        when = (f"{int(age)}m ago" if age < 90 else f"{age / 60:.1f}h ago")
        bits = [f"{when}: {p.lat:.4f}, {p.lon:.4f}"]
        if p.accuracy_m:
            bits.append(f"±{round(p.accuracy_m)}m")
        if p.label:
            bits.append(p.label)
        bits.append(f"[{p.trigger or 'unknown'}]")
        bits.append("linked" if p.request_id else "unsolicited")
        lines.append("  " + " · ".join(bits))

    total = ctx.db.query(LocationPing).count()
    out = f"{len(rows)} most recent position report(s):\n" + "\n".join(lines)
    if total >= settings.location_keep_pings:
        out += (f"\n\nShowing from {total} retained pings — anything earlier has been "
                f"pruned, so an absence above is not evidence of an absence of activity.")
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
    reg.register(
        {
            "name": "check_location_freshness",
            "description": (
                "Whether a recent position fix has registered at all, how old the newest "
                "one is, and — when it is stale — WHICH LAYER of the pull loop stopped "
                "(server not asking / relay rejecting / phone not answering / phone "
                "answering late). Reports the layer as fact and does not guess a cause."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        _check_location_freshness,
    )
    reg.register(
        {
            "name": "location_ping_log",
            "description": (
                "Recent position reports from the phone, newest first, with each one's "
                "age, accuracy, trigger, and whether it answered a request. Use when the "
                "user asks whether their phone has been reporting, when JARVIS last heard "
                "from it, or to check a suspected gap — this is the history behind "
                "where_am_i's single latest fix."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"n": {"type": "integer",
                                     "description": "How many to show (default 20, max 100)."}},
            },
        },
        _location_ping_log,
    )
