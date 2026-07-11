"""Travel handler — know about trips, without touching an airline account.

THE TRUST BOUNDARY: JARVIS holds no airline credentials and scrapes nothing. The
airline mails the confirmation to the address JARVIS already watches; the email
pipeline already reads that inbox; this module turns the email into structure.
JARVIS knows about the trip *because the trip was mailed to it*.

That is not a workaround for the lack of an Alaska API — it is the correct
design. The alternative (store the user's airline password, drive a headless
browser) would put a system that can BOOK TRAVEL behind a phone line
authenticated by caller ID, which is spoofable. No.

Flight SEARCH is a separate, legitimate problem with real APIs (Amadeus, Duffel)
— see search_flights below, which is stubbed until a key is configured. Booking
stays manual by design: JARVIS researches, emails you the options, and opens a
task. You book.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import Trip

log = logging.getLogger(__name__)

# ── Parsing ──────────────────────────────────────────────────────────────────
# Deliberately conservative. A trip parsed WRONG is worse than a trip not parsed:
# you'd show up on the wrong day trusting it. Anything not confidently extracted
# is left empty and the raw email is kept for re-parsing.

_CONF_RE = re.compile(
    r"(?:confirmation(?:\s+code)?|record locator|reference)\s*[:#]?\s*([A-Z0-9]{5,7})\b",
    re.I,
)
_FLIGHT_RE = re.compile(r"\b([A-Z]{2})\s*(\d{1,4})\b")
_AIRPORT_PAIR_RE = re.compile(
    r"\b([A-Z]{3})\b\s*(?:to|-|–|→|>)\s*\b([A-Z]{3})\b"
)
_SEAT_RE = re.compile(r"\bseat\s*[:#]?\s*(\d{1,2}[A-F])\b", re.I)

_CARRIERS = {
    "AS": "Alaska Airlines", "DL": "Delta", "UA": "United", "AA": "American",
    "WN": "Southwest", "B6": "JetBlue", "NK": "Spirit", "F9": "Frontier",
}


def parse_itinerary(text: str) -> dict:
    """Extract what we can from a confirmation email. Empty fields mean 'unknown',
    never 'guessed'."""
    out: dict = {}

    m = _CONF_RE.search(text)
    if m:
        out["confirmation"] = m.group(1).upper()

    m = _FLIGHT_RE.search(text)
    if m:
        code = m.group(1).upper()
        out["flight_no"] = f"{code}{m.group(2)}"
        out["carrier"] = _CARRIERS.get(code, code)

    m = _AIRPORT_PAIR_RE.search(text)
    if m:
        out["origin"], out["destination"] = m.group(1), m.group(2)

    m = _SEAT_RE.search(text)
    if m:
        out["seat"] = m.group(1).upper()

    return out


def record_trip_from_email(db, subject: str, body: str) -> Trip | None:
    """Called by the email pipeline when a message looks like a confirmation.

    Returns the Trip, or None if nothing usable was found. Keeps the raw text so
    a better parser later can re-derive fields without the email being lost.
    """
    text = f"{subject}\n\n{body}"
    fields = parse_itinerary(text)
    if not fields.get("confirmation") and not fields.get("flight_no"):
        return None  # not a confirmation, or unparseable — don't invent a trip

    conf = fields.get("confirmation", "")
    if conf:
        existing = db.execute(
            select(Trip).where(Trip.confirmation == conf)
        ).scalars().first()
        if existing:
            return existing  # already captured

    trip = Trip(raw=text[:20000], **fields)
    db.add(trip)
    db.commit()
    db.refresh(trip)
    log.info("captured trip %s %s", trip.carrier, trip.confirmation)
    return trip


def looks_like_confirmation(subject: str, sender: str) -> bool:
    s = (subject or "").lower()
    f = (sender or "").lower()
    if any(k in s for k in ("confirmation", "itinerary", "e-ticket", "eticket",
                            "your trip", "booking confirmed", "flight confirmation")):
        return True
    return any(d in f for d in ("alaskaair", "delta.com", "united.com", "aa.com"))


# ── Tools ────────────────────────────────────────────────────────────────────
def _list_trips(args: dict, ctx: Context) -> str:
    rows = ctx.db.execute(
        select(Trip).order_by(Trip.depart_at.is_(None), Trip.depart_at, Trip.id.desc()).limit(10)
    ).scalars().all()
    if not rows:
        return ("No trips on file. JARVIS learns about trips from confirmation emails "
                "sent to its inbox — forward one and it'll be captured.")
    lines = []
    for t in rows:
        when = t.depart_at.strftime("%a %b %-d, %-I:%M %p") if t.depart_at else "date not parsed"
        route = f"{t.origin}->{t.destination}" if t.origin and t.destination else "route unknown"
        lines.append(
            f"- {t.carrier or '?'} {t.flight_no or ''} {route}, {when}"
            + (f", conf {t.confirmation}" if t.confirmation else "")
            + (f", seat {t.seat}" if t.seat else "")
        )
    return f"{len(rows)} trip(s) on file:\n" + "\n".join(lines)


def _search_flights(args: dict, ctx: Context) -> str:
    """STUB until an Amadeus/Duffel key is configured.

    Search is a legitimate API problem — unlike account access, which has no
    consumer API and would require credentials + scraping. Booking stays manual:
    JARVIS researches, emails options, opens a task. The user books.
    """
    if not settings.duffel_api_key and not settings.amadeus_api_key:
        return (
            "[flight search not configured] I can't search flights yet — that needs a "
            "Duffel or Amadeus API key (DUFFEL_API_KEY / AMADEUS_API_KEY). "
            "I can still tell you about trips you've already booked, since airlines "
            "email the confirmations here."
        )
    return "[flight search configured but not yet implemented]"


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "list_trips",
            "description": "List the user's upcoming trips, captured from airline "
                           "confirmation emails. Read-only.",
            "input_schema": {"type": "object", "properties": {}},
        },
        _list_trips,
    )
    reg.register(
        {
            "name": "search_flights",
            "description": "Search for available flights (requires a Duffel/Amadeus key). "
                           "This only RESEARCHES — it cannot book. Read-only.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Airport code, e.g. SEA"},
                    "destination": {"type": "string"},
                    "date": {"type": "string", "description": "ISO date"},
                },
                "required": ["origin", "destination", "date"],
            },
        },
        _search_flights,
    )
