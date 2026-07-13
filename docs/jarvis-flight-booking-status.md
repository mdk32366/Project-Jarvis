# JARVIS flight booking — status

**Last updated:** July 12, 2026

## Code: done, tested, delivered
Full implementation of the flight-booking TDD is built and merged into your working copy — `book_flight` tool, TOTP second factor, `FlightOffer` retention (the load-bearing "only book what search_flights actually found" check), the two-stage confirmation gate (`confirm` → TOTP code → book), fare sanity cap, Duffel error translation, Trip recording + confirmation email. 263 tests passing, including 23 new ones covering every row in the TDD's test table.

New files: `app/totp.py`, `alembic/versions/0010_flight_booking.py`, `tests/test_flight_booking.py`.
Modified: `models.py`, `config.py`, `handlers/base.py`, `handlers/travel.py`, `orchestrator.py`, `agents.py`, `channels/voice_pipeline.py`, `requirements.txt`, `.env.template`.

## Secrets: set
- `TOTP_SECRET` — generated, QR scanned into your authenticator app, set as a Fly secret.
- `OWNER_DOB`, `OWNER_GENDER` — set.
- `BOOKING_ENABLED=false` — deliberately still off.

## Still open — Duffel live activation (TDD §3)
This is the only remaining blocker, and it's entirely on Duffel's side / yours to click through:

1. **Request live-mode access** on the Duffel dashboard. Be plain about what this is — a single-user personal assistant booking your own flights — since it's an unusual profile and they may follow up with questions.
2. **Top up the Duffel balance** once live access is granted. This is what your dedicated card actually funds; JARVIS spends against the balance, not the card directly. Fund it with an amount you're fine losing outright — that's the practical ceiling, on top of `MAX_BOOKING_USD`.
3. **Get the live token** (`duffel_live_...`), separate from the existing test-mode key.
4. Set it: `fly secrets set DUFFEL_LIVE_API_KEY=duffel_live_xxxxx --app jarvis-mdk`
5. **Sanity-check the live token/balance manually** (Duffel dashboard or a raw API call) before letting JARVIS near it.
6. Only then: `fly secrets set BOOKING_ENABLED=true --app jarvis-mdk` — this is the single switch that arms `book_flight`. Everything else is inert until this flips.

## Worth double-checking at flip time
- Does the dedicated card's real limit match `MAX_BOOKING_USD` (defaults to 3000.0)? Set it as a secret if different.
- Is `OWNER_EMAIL` (or the `ALLOWED_SENDERS` fallback) an inbox you'll actually see the booking confirmation land in — that's the durable record, not the spoken "Booked" reply.

## To resume
Paste this note into a new chat in the JARVIS project, or just say "resume the Duffel flip" — the context carries forward either way.
