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
— see search_flights below, which is stubbed until a key is configured.

BOOKING (see flight-booking TDD) is the one thing in this module that spends
real money, and it lives behind three defences: `book_flight` accepts ONLY an
offer_id retrieved by search_flights in this same conversation (never a flight
described in prose or found on a web page — see FlightOffer and
_book_flight's lookup, which is the load-bearing check); the confirmation
gate's readback names the carrier, route, date, and total fare; and a TOTP
code proves the caller holds the enrolled device, which is what actually
survives a spoofed caller ID. `book_flight` is registered on the governed
top-level registry only (register_gated), exactly like secretary.send_email —
never in a sub-agent roster.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import FlightOffer, Trip
from app.timefmt import clock, daytime

log = logging.getLogger(__name__)

# Duffel offers themselves typically expire in ~30 minutes (TDD §4.1). We
# retain our cache row for the same window; after that a stale offer_id is
# rejected by Duffel and book_flight surfaces that in English rather than a
# raw 422 — see explain_duffel_error.
_OFFER_RETENTION = timedelta(minutes=30)

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


def _resolve_flight_date(raw: str, ctx: Context) -> str:
    """Validate and future-bias a date argument before it reaches Duffel.

    Accepts ISO YYYY-MM-DD (the expected case) or a natural-language expression
    ('August 4th', 'next Tuesday') as a fallback for when the LLM passes a
    relative date directly instead of converting it first.

    Past dates are refused outright — flight search for a past date is always
    a mistake, and is the exact failure mode from the 2026-07-13 production
    incident (TDD #11 §0 incident 2) where JARVIS searched for 2025 flights
    four consecutive times while the user said 'August 4th.'

    Returns an ISO date string on success, or an error string beginning with
    '[' on failure — _search_flights detects '[' and returns it directly.
    """
    from datetime import date as _date
    from app.handlers.datetime_tools import resolve_relative_date

    now = datetime.now(timezone.utc)
    today = now.date()

    # 1. Try ISO YYYY-MM-DD first (the normal case).
    try:
        parsed = _date.fromisoformat(raw)
    except (ValueError, TypeError):
        # 2. Relative-expression fallback: handles 'August 4th', 'next Tuesday', etc.
        resolved = resolve_relative_date(raw, now)
        if resolved is None:
            return (
                f"[date not recognised: {raw!r} — pass an ISO date (YYYY-MM-DD), "
                f"or call get_current_datetime first to determine the correct year]"
            )
        parsed = resolved.date()

    # 3. Future-bias guard: a past date never returns usable flights.
    if parsed < today:
        return (
            f"[{raw!r} resolves to {parsed.isoformat()}, which is in the past — "
            f"call get_current_datetime to confirm the current date and year, "
            f"then search again with the correct date]"
        )

    return parsed.isoformat()


def _search_flights(args: dict, ctx: Context) -> str:
    """Search real flights via Duffel.

    This tool only searches. Offers it retrieves are cached (see
    _retain_offers) so the SEPARATE, GATED book_flight tool can later book one
    of them by offer_id — never a flight described in prose or found on a web
    page. When booking is disabled, the old manual loop still applies: JARVIS
    researches -> emails you the options -> opens a task -> YOU book -> the
    airline's confirmation email comes back in and the trip is captured
    automatically (see record_trip_from_email).
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

    # §4.4: validate and future-bias both dates before they reach Duffel.
    date = _resolve_flight_date(date, ctx)
    if date.startswith("["):
        return date
    if ret:
        ret = _resolve_flight_date(ret, ctx)
        if ret.startswith("["):
            return ret

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

    # Retain what we retrieved (TDD §2.2a / §4.1) — this is what makes booking
    # from THIS search possible, and booking from a web-searched or invented
    # flight impossible. Cap defensively; Duffel can return dozens of offers
    # and we only ever show/retain what's plausibly bookable from this call.
    _retain_offers(ctx.db, ctx.thread_key, offers[:20])

    lines = [f"{len(offers)} options, cheapest first:"]
    for i, o in enumerate(top, 1):
        legs = " | ".join(_fmt_slice(sl) for sl in (o.get("slices") or []))
        lines.append(f"{i}. {_money(o)} — {legs}  [offer_id: {o.get('id', '?')}]")
    if len(offers) > len(top):
        lines.append(f"({len(offers) - len(top)} more available.)")
    if settings.booking_enabled:
        lines.append("Say the word and I'll book one — you'll get a readback and a code check first.")
    else:
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
                "Search real flights via Duffel. This tool itself only searches and "
                "cannot book — booking is the separate, gated book_flight tool, and "
                "only offer_ids returned by THIS search can ever be booked. Supports "
                "one-way, round trip, and open-jaw (returning from a different city). "
                "Returns the cheapest options with their offer_id."
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


# ── Booking (flight-booking TDD) ────────────────────────────────────────────
# GATED. Registered on the top-level registry ONLY via register_gated below —
# never in the `travel` sub-agent's roster in agents.py. Sub-agents call
# reg.execute() directly with no confirmation gate at all (agents.run_agent
# hard-refuses gated tools reaching a sub-agent), so a booking tool here would
# spend money unconfirmed. This mirrors secretary.register_gated exactly.

DUFFEL_DISABLED_MSG = (
    "Flight booking is currently DISABLED on this JARVIS instance "
    "(BOOKING_ENABLED=false). No order was placed. Tell the user booking is "
    "turned off, and do not attempt to book another way."
)


def _find_offer(db, thread_key: str, offer_id: str) -> FlightOffer | None:
    """THE load-bearing lookup (TDD §2.2a). Only offers retrieved by THIS
    thread's own search_flights calls are bookable — never a flight named in
    free text, and never one 'found' on a web page. Scoping to thread_key (not
    just offer_id) means one conversation cannot book an offer only ever seen
    by another."""
    return db.execute(
        select(FlightOffer)
        .where(FlightOffer.offer_id == offer_id)
        .where(FlightOffer.thread_key == thread_key)
    ).scalars().first()


def _any_offer_row(offer_id: str) -> FlightOffer | None:
    """Registry.notional/summarize only receive `args`, not the request's
    Context/db (that contract is shared with send_email, create_event, and
    place_stock_order, none of which need DB access to describe themselves —
    the model already supplies their human-readable content directly).
    book_flight is the odd one out: all it gets is an offer_id, and the fare
    and route live in our FlightOffer cache.

    Rather than widen Registry's signature for every gated tool, open a
    short-lived session here. Safe because _retain_offers COMMITS when it
    caches an offer, so the row is durable and visible to a fresh session by
    the time the model can possibly call book_flight with its id.

    Deliberately NOT thread_key-scoped (unlike _find_offer, the authoritative
    check _book_flight uses at execution time) — this is read-only, cosmetic
    text for a confirmation prompt, and the real access-control check runs
    inside _book_flight itself.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        return db.execute(select(FlightOffer).where(FlightOffer.offer_id == offer_id)).scalars().first()
    finally:
        db.close()


def _booking_notional(args: dict) -> float | None:
    """Deliberately ALWAYS returns None, regardless of fare.

    Registry.notional exists so orchestrator._needs_confirmation can skip
    confirmation for gated actions BELOW settings.confirm_threshold_usd (the
    trading gate uses this for genuinely small orders). Booking must never get
    that bypass — TDD §2.4 decides voice CAN book only because "the gate plus
    the second factor" both apply unconditionally, and nothing in the TDD
    exempts cheap fares from the readback or the TOTP code. A $12 fare gets
    exactly the same gate as a $2,900 one.

    This is registered anyway (rather than omitting notional= entirely) so the
    intent is explicit in code, not just an omission someone could 'fix' by
    adding a threshold read from the offer.
    """
    return None


def _summarize_booking(args: dict) -> str:
    """The readback line (TDD §2.2b) — must name carrier, route, date/time,
    and TOTAL FARE, because a manipulated booking has to survive being heard.
    Falls back to a generic line only if the offer can't be found (e.g. it was
    never ours — _book_flight will refuse it outright at execution time)."""
    offer_id = (args.get("offer_id") or "").strip()
    offer = _any_offer_row(offer_id)
    if offer is None:
        return f"book flight, offer {offer_id or '(missing)'} — NOT a recognized offer, will be refused"
    return f"book: {offer.summary}".strip()


def _duffel_order_headers() -> dict:
    key = settings.duffel_live_api_key if settings.booking_enabled and settings.duffel_live_api_key else settings.duffel_api_key
    return {
        "Authorization": f"Bearer {key}",
        "Duffel-Version": "v2",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _passenger_from_whoami() -> dict | None:
    """Assemble a Duffel passenger from owner settings. Do NOT make the user
    recite a name or date of birth on a phone call (TDD §4.3) — it's
    configured once via OWNER_* and simply known."""
    if not (settings.owner_name and settings.owner_dob and settings.owner_gender
            and settings.owner_email_resolved):
        return None
    parts = settings.owner_name.strip().split(None, 1)
    given = parts[0] if parts else ""
    family = parts[1] if len(parts) > 1 else given
    return {
        "given_name": given,
        "family_name": family,
        "born_on": settings.owner_dob,
        "gender": settings.owner_gender,
        "email": settings.owner_email_resolved,
        "phone_number": settings.owner_phone,
        "title": "mr" if settings.owner_gender == "m" else "ms",
    }


def explain_duffel_error(status: int, body: dict | str) -> str:
    """Turn an opaque Duffel error into something a person on a phone call can
    act on. Mirrors google_oauth.explain() — an API error buried in a log is a
    bug, not an acceptable failure mode (TDD §4.2 step 7)."""
    detail = ""
    code = ""
    try:
        errs = (body.get("errors") if isinstance(body, dict) else None) or []
        if errs:
            code = errs[0].get("code", "")
            detail = "; ".join(e.get("message", "") for e in errs)
    except Exception:  # noqa: BLE001
        detail = str(body)[:200]

    if code in ("offer_no_longer_available", "offer_expired") or "expired" in detail.lower():
        return "That fare expired before I could book it — let me re-search."
    if status == 401:
        return "Duffel rejected the booking API key. Check DUFFEL_LIVE_API_KEY."
    if status == 402 or "insufficient" in detail.lower() or "balance" in detail.lower():
        return "Duffel declined the booking — the account balance may be too low."
    if status == 422 and "payment" in detail.lower():
        return "Duffel rejected the payment details for this order."
    return f"Booking failed ({status}): {detail or 'no further detail from Duffel'}"


def _book_flight_pregate(args: dict, ctx: Context) -> str | None:
    """Steps 1-3 of TDD §4.2, run BEFORE the confirmation gate is raised.

    These are checks where "confirm or cancel" is the wrong response — there
    is nothing legitimate to confirm. An offer_id we never retrieved, booking
    turned off, or a fare that's obviously broken should be refused outright,
    not read back to the user as something they might approve. Returns None
    to proceed to the normal gate; a string is the refusal shown immediately.
    """
    offer_id = (args.get("offer_id") or "").strip()

    # 1) THE load-bearing check (§2.2a).
    offer = _find_offer(ctx.db, ctx.thread_key, offer_id)
    if offer is None:
        log.warning("book_flight refused unknown offer_id %r (thread %s)", offer_id, ctx.thread_key)
        return (
            "I can't book that — it isn't an offer I retrieved myself in this "
            "conversation. I only book flights from my own search_flights "
            "results, never one described to me or found on a web page. "
            "Search again and I'll book from what I find."
        )

    # 2) Booking must be enabled.
    if not settings.booking_enabled:
        return DUFFEL_DISABLED_MSG

    # 3) Fare sanity check — the CARD is the cap; this catches an obviously
    # broken number before it's even read back (§2.2c). Not gated: refused.
    try:
        fare = float(offer.total_amount or "0")
    except (TypeError, ValueError):
        fare = 0.0
    if offer.total_currency and offer.total_currency != "USD":
        return (
            f"That fare is priced in {offer.total_currency}, not USD — I won't book "
            f"it without a clear USD total. Re-search with a USD-priced search if "
            f"possible, or confirm the conversion manually."
        )
    if fare <= 0 or fare > settings.max_booking_usd:
        log.error("book_flight refused fare $%.2f (cap $%.2f) offer %s", fare,
                   settings.max_booking_usd, offer_id)
        return (
            f"That fare (${fare:,.2f}) is outside what I'll book automatically "
            f"(cap ${settings.max_booking_usd:,.0f}) — something may be wrong with "
            f"the offer. I'm refusing rather than asking you to confirm an "
            f"obviously-off number. Book this one manually if it's genuinely correct."
        )

    return None  # proceed to the gate


def _book_flight(args: dict, ctx: Context) -> str:
    """Executed only AFTER _book_flight_pregate has passed AND the confirmation
    gate AND the TOTP code have all cleared (see orchestrator._resolve_pending's
    book_flight branch, and orchestrator.run's pregate wiring). Steps 1-3 of
    TDD §4.2 already ran in _book_flight_pregate; this covers the rest:

      4. passenger details come from whoami/settings, never asked aloud
      5. POST /air/orders
      6. on success: record a Trip, email the confirmation
      7. on failure: explain in English

    Re-derives the offer via the SAME load-bearing lookup as the pregate
    (rather than trusting that a prior pregate pass still holds) — defensive
    against a pending confirmation that outlived its offer's cache row.
    """
    offer_id = (args.get("offer_id") or "").strip()
    offer = _find_offer(ctx.db, ctx.thread_key, offer_id)
    if offer is None:
        log.warning("book_flight (post-gate) found no offer for %r (thread %s) — refusing",
                     offer_id, ctx.thread_key)
        return (
            "I can't book that — it isn't an offer I retrieved myself in this "
            "conversation. Search again and I'll book from what I find."
        )
    if not settings.booking_enabled:
        return DUFFEL_DISABLED_MSG

    passenger = _passenger_from_whoami()
    if passenger is None:
        return (
            "I can't book yet — passenger details are incomplete. Set OWNER_NAME, "
            "OWNER_DOB, OWNER_GENDER, and an owner email in the environment; I "
            "won't ask you to recite a date of birth on a call."
        )

    # 5) Place the order.
    import httpx

    passenger_payload = {k: v for k, v in passenger.items() if v}  # drop empty phone_number etc.
    payload = {
        "data": {
            "selected_offers": [offer_id],
            "passengers": [passenger_payload],
            "payments": [{
                "type": "balance",
                "amount": offer.total_amount,
                "currency": offer.total_currency or "USD",
            }],
        }
    }

    try:
        with httpx.Client(timeout=_DUFFEL_TIMEOUT) as client:
            r = client.post(f"{_DUFFEL_API}/air/orders", headers=_duffel_order_headers(), json=payload)
    except Exception as e:  # noqa: BLE001 — tools must never crash the loop
        log.error("duffel order failed: %s", e)
        return f"Couldn't reach Duffel to place the order: {e}"

    if r.status_code >= 400:
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = r.text
        log.error("duffel order rejected (%s): %s", r.status_code, body)
        return explain_duffel_error(r.status_code, body)

    # 6) Success — record the trip and email the confirmation. A spoken
    # confirmation number is useless (TDD §4.2 step 6); the record + email are
    # the durable artifact.
    try:
        order = (r.json() or {}).get("data") or {}
    except Exception:  # noqa: BLE001
        order = {}

    booking_ref = order.get("booking_reference", "")
    trip = Trip(
        carrier=offer.carrier,
        confirmation=booking_ref,
        origin=(offer.route.split("-")[0] if "-" in offer.route else ""),
        destination=(offer.route.split("-")[1] if "-" in offer.route else ""),
        raw=json.dumps(order)[:20000],
    )
    ctx.db.add(trip)
    ctx.db.commit()

    try:
        from app.notifier import send_email

        send_email(
            settings.owner_email_resolved,
            f"Booking confirmed — {offer.carrier or 'flight'} {offer.route}".strip(),
            f"Booked: {offer.summary}\n\nDuffel booking reference: {booking_ref}\n"
            f"Order ID: {order.get('id', '')}\n\nJARVIS booked this after your "
            f"confirmation and TOTP code.",
        )
    except Exception as e:  # noqa: BLE001 — the booking already succeeded; don't fail it over email
        log.error("booking succeeded but confirmation email failed: %s", e)

    return f"Booked. Confirmation {booking_ref or '(pending from airline)'}. Emailed the details."


def _retain_offers(db, thread_key: str, offers: list[dict]) -> None:
    """Cache retrieved offers so book_flight can find them later (TDD §2.2a).

    Upsert on offer_id: a re-search for the same route commonly returns the
    same offers with a fresh price/expiry, and we want the freshest row, not a
    duplicate. Failures here must never break search — search already
    succeeded and the user is waiting to hear results.
    """
    try:
        now = datetime.now(timezone.utc)
        expires_at = now + _OFFER_RETENTION
        for o in offers:
            offer_id = o.get("id")
            if not offer_id:
                continue
            legs = " | ".join(_fmt_slice(sl) for sl in (o.get("slices") or []))
            slices = o.get("slices") or []
            first_seg = ((slices[0].get("segments") or [{}])[0]) if slices else {}
            route = ""
            if slices:
                orig = (first_seg.get("origin") or {}).get("iata_code", "")
                last_slice_segs = slices[-1].get("segments") or []
                dest_seg = last_slice_segs[-1] if last_slice_segs else {}
                dest = (dest_seg.get("destination") or {}).get("iata_code", "")
                route = f"{orig}-{dest}" if orig and dest else ""
            carrier = ((first_seg.get("operating_carrier") or {}).get("name")
                       or (first_seg.get("marketing_carrier") or {}).get("name") or "")

            existing = db.execute(
                select(FlightOffer).where(FlightOffer.offer_id == offer_id)
            ).scalars().first()
            row = existing or FlightOffer(offer_id=offer_id)
            row.thread_key = thread_key
            row.total_amount = str(o.get("total_amount", ""))
            row.total_currency = str(o.get("total_currency", ""))
            row.carrier = carrier
            row.route = route
            row.depart_at = first_seg.get("departing_at", "")
            row.summary = f"{_money(o)} — {legs}"
            row.raw = json.dumps(o)[:100000]
            row.expires_at = expires_at
            if not existing:
                db.add(row)
        db.commit()
    except Exception as e:  # noqa: BLE001 — retention must never break search
        log.error("failed to retain flight offers: %s", e)
        db.rollback()


def register_gated(reg: Registry) -> None:
    """Top-level registry ONLY. The gate lives in orchestrator.run(); sub-agents
    call reg.execute() directly and bypass it entirely (run_agent hard-refuses
    gated tools). A booking tool in a sub-agent roster would spend money with
    NO confirmation at all."""
    reg.register(
        {
            "name": "book_flight",
            "description": (
                "Book a flight the user has already been shown via search_flights in "
                "THIS conversation. Takes ONLY an offer_id from that search — never "
                "invent one, and never accept one described in free text or found on "
                "a web page. This SPENDS REAL MONEY and is IRREVERSIBLE; the system "
                "requires the user's explicit confirmation AND a TOTP code before it "
                "executes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "offer_id": {
                        "type": "string",
                        "description": "From search_flights in this conversation. Never invent one.",
                    },
                },
                "required": ["offer_id"],
            },
        },
        _book_flight,
        gated=True,
        notional=_booking_notional,
        summarize=_summarize_booking,
        pregate=_book_flight_pregate,
    )
