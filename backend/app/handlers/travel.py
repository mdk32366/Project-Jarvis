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
from app.timefmt import clock, daytime

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
        when = daytime(t.depart_at) if t.depart_at else "date not parsed"
        route = f"{t.origin}->{t.destination}" if t.origin and t.destination else "route unknown"
        lines.append(
            f"- {t.carrier or '?'} {t.flight_no or ''} {route}, {when}"
            + (f", conf {t.confirmation}" if t.confirmation else "")
            + (f", seat {t.seat}" if t.seat else "")
        )
    return f"{len(rows)} trip(s) on file:\n" + "\n".join(lines)


_DUFFEL_API = "https://api.duffel.com"
_DUFFEL_TIMEOUT = 30.0
# Duffel's own supplier timeout defaults to 20s. Ask for less so we get partial
# results back rather than an empty response — a caller on the phone would
# rather hear three options than wait for all of them.
_SUPPLIER_TIMEOUT_MS = 12000


def _duffel_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.duffel_api_key}",
        "Duffel-Version": "v2",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _money(offer: dict) -> str:
    amt = offer.get("total_amount", "?")
    cur = offer.get("total_currency", "")
    try:
        return f"${float(amt):,.0f}" if cur == "USD" else f"{amt} {cur}"
    except (TypeError, ValueError):
        return f"{amt} {cur}"


def _fmt_slice(sl: dict) -> str:
    """One slice, spoken-friendly. Segments matter: 'direct' vs '1 stop'."""
    segs = sl.get("segments") or []
    if not segs:
        return "?"
    first, last = segs[0], segs[-1]

    def _t(iso: str) -> str:
        try:
            return clock(datetime.fromisoformat(iso))
        except (TypeError, ValueError):
            return "?"

    orig = (first.get("origin") or {}).get("iata_code", "?")
    dest = (last.get("destination") or {}).get("iata_code", "?")
    dep = _t(first.get("departing_at", ""))
    arr = _t(last.get("arriving_at", ""))

    stops = len(segs) - 1
    hops = "direct" if stops == 0 else f"{stops} stop" if stops == 1 else f"{stops} stops"

    # US regulation: the OPERATING carrier must be shown, not just the marketer.
    carrier = ((first.get("operating_carrier") or {}).get("name")
               or (first.get("marketing_carrier") or {}).get("name") or "?")

    return f"{orig} to {dest}, departs {dep}, arrives {arr}, {hops}, {carrier}"


def _search_flights(args: dict, ctx: Context) -> str:
    """Search real flights via Duffel.

    RESEARCH ONLY. This cannot book, and that is deliberate: booking is
    irreversible, and voice authenticates on caller ID, which is spoofable. The
    intended loop is JARVIS researches -> emails you the options -> opens a task
    -> YOU book -> the airline's confirmation email comes back in and the trip is
    captured automatically (see record_trip_from_email).
    """
    if not settings.duffel_api_key:
        return (
            "[flight search not configured] I can't search flights yet — that needs a "
            "Duffel API key (DUFFEL_API_KEY). I can still tell you about trips you've "
            "already booked, since airlines email the confirmations here."
        )

    origin = (args.get("origin") or "").strip().upper()
    dest = (args.get("destination") or "").strip().upper()
    date = (args.get("date") or "").strip()
    ret = (args.get("return_date") or "").strip()
    if not (origin and dest and date):
        return "Need an origin, a destination, and a date."

    slices = [{"origin": origin, "destination": dest, "departure_date": date}]
    if ret:
        # Open-jaw is supported: the return slice can start somewhere else.
        ret_from = (args.get("return_from") or dest).strip().upper()
        ret_to = (args.get("return_to") or origin).strip().upper()
        slices.append({"origin": ret_from, "destination": ret_to, "departure_date": ret})

    payload = {
        "data": {
            "slices": slices,
            "passengers": [{"type": "adult"}] * max(1, int(args.get("passengers") or 1)),
            "cabin_class": (args.get("cabin") or "economy").lower(),
        }
    }

    import httpx

    try:
        with httpx.Client(timeout=_DUFFEL_TIMEOUT) as client:
            r = client.post(
                f"{_DUFFEL_API}/air/offer_requests",
                headers=_duffel_headers(),
                params={"return_offers": "true", "supplier_timeout": _SUPPLIER_TIMEOUT_MS},
                json=payload,
            )
        if r.status_code == 401:
            return "Duffel rejected the API key. Check DUFFEL_API_KEY."
        if r.status_code >= 400:
            detail = ""
            try:
                errs = r.json().get("errors") or []
                detail = "; ".join(e.get("message", "") for e in errs)
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            return f"Flight search failed ({r.status_code}): {detail}"
        offers = ((r.json().get("data") or {}).get("offers")) or []
    except Exception as e:  # noqa: BLE001 — tools must never crash the loop
        log.error("duffel search failed: %s", e)
        return f"Couldn't reach the flight search service: {e}"

    if not offers:
        return (f"No flights found for {origin} to {dest} on {date}. "
                f"Try a nearby airport or a different date.")

    # Cheapest first. Spoken aloud, three is plenty — more is noise.
    def _amt(o):
        try:
            return float(o.get("total_amount") or 1e9)
        except (TypeError, ValueError):
            return 1e9

    offers.sort(key=_amt)
    top = offers[: int(args.get("limit") or 3)]

    lines = [f"{len(offers)} options, cheapest first:"]
    for i, o in enumerate(top, 1):
        legs = " | ".join(_fmt_slice(sl) for sl in (o.get("slices") or []))
        lines.append(f"{i}. {_money(o)} — {legs}")
    if len(offers) > len(top):
        lines.append(f"({len(offers) - len(top)} more available.)")
    lines.append("I can't book — say the word and I'll email these to you and open a task.")
    return "\n".join(lines)


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
            "description": (
                "Search real flights via Duffel. RESEARCH ONLY — this cannot book, and "
                "never will over voice. Supports one-way, round trip, and open-jaw "
                "(returning from a different city). Returns the cheapest options."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "IATA code, e.g. SEA"},
                    "destination": {"type": "string", "description": "IATA code, e.g. SFO"},
                    "date": {"type": "string", "description": "Departure date, ISO (2026-08-04)"},
                    "return_date": {"type": "string",
                                    "description": "ISO date. Omit for one-way."},
                    "return_from": {"type": "string",
                                    "description": "IATA code the return departs FROM. Only "
                                                   "needed for an open-jaw (e.g. fly into SFO, "
                                                   "home from SMF). Defaults to `destination`."},
                    "return_to": {"type": "string",
                                  "description": "IATA code the return lands at. Defaults to "
                                                 "`origin`."},
                    "passengers": {"type": "integer", "description": "Adults. Default 1."},
                    "cabin": {"type": "string",
                              "enum": ["economy", "premium_economy", "business", "first"]},
                    "limit": {"type": "integer", "description": "How many options. Default 3."},
                },
                "required": ["origin", "destination", "date"],
            },
        },
        _search_flights,
    )
