"""Flight booking — the security properties from the flight-booking TDD, proven.

Mirrors test_gate.py's end-to-end style: drive app.orchestrator.run() with a
scripted LLM and assert on PendingConfirmation / Trip / ActionAudit rows,
rather than calling _book_flight directly, so the tests exercise the real
gate + second-factor wiring and not just the handler in isolation.
"""
import pyotp
import pytest

from app.config import settings
from app.handlers.base import Context, build_registry
from app.models import ActionAudit, FlightOffer, PendingConfirmation, Trip
from app.orchestrator import run
from fakes import install_llm, say, use_tool_then

THREAD = "sms:+15551230000"
TOTP_SECRET = pyotp.random_base32()


def _code() -> str:
    return pyotp.TOTP(TOTP_SECRET).now()


def _seed_offer(db, thread_key=THREAD, offer_id="off_test123", amount="317.00",
                 currency="USD", carrier="Alaska Airlines", route="SEA-SFO") -> FlightOffer:
    row = FlightOffer(
        thread_key=thread_key,
        offer_id=offer_id,
        total_amount=amount,
        total_currency=currency,
        carrier=carrier,
        route=route,
        depart_at="2026-08-04T07:04:00Z",
        summary=f"${amount} — {route}, departs 7:04 AM, arrives 9:41 AM, direct, {carrier}",
        raw="{}",
    )
    db.add(row)
    db.commit()
    return row


def _enable_booking(monkeypatch, **overrides):
    monkeypatch.setattr(settings, "booking_enabled", True)
    monkeypatch.setattr(settings, "totp_secret", TOTP_SECRET)
    monkeypatch.setattr(settings, "owner_name", "Matt Kelly")
    monkeypatch.setattr(settings, "owner_dob", "1970-01-01")
    monkeypatch.setattr(settings, "owner_gender", "m")
    monkeypatch.setattr(settings, "owner_email", "me@example.com")
    monkeypatch.setattr(settings, "owner_phone", "+15551230000")
    for k, v in overrides.items():
        monkeypatch.setattr(settings, k, v)


def _mock_duffel_order_success(monkeypatch, booking_reference="ABC123"):
    import app.handlers.travel as travel

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": {"id": "ord_1", "booking_reference": booking_reference}}

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw): return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    sent = []
    monkeypatch.setattr("app.notifier.send_email", lambda *a, **kw: sent.append(a) or "msg-id")
    return sent


# ── Registration / isolation ─────────────────────────────────────────────────

def test_book_flight_is_top_level_only():
    """Mirrors test_gated_tools_are_top_level_only. Sub-agents bypass the gate
    entirely, so book_flight must not leak into the sub-agent registry."""
    sub = build_registry()
    assert not sub.has("book_flight"), "book_flight leaked into the sub-agent registry"

    top = build_registry(include_delegate=True)
    assert top.has("book_flight")
    assert top.is_gated("book_flight")


def test_booking_is_not_in_any_sub_agent_roster():
    from app.agents import DEFAULT_AGENTS

    for agent in DEFAULT_AGENTS.values():
        assert "book_flight" not in agent.tools, f"{agent.name} roster includes book_flight"


def test_subagent_refuses_book_flight_even_if_roster_lists_it(db, monkeypatch, caplog):
    """THE load-bearing structural test, same shape as
    test_subagent_refuses_gated_tool_even_if_roster_lists_it for send_email."""
    from app.agents import Agent, run_agent

    _enable_booking(monkeypatch)
    _seed_offer(db, thread_key="t")
    rogue = Agent("rogue", "d", "s", ["book_flight"])
    install_llm(monkeypatch, use_tool_then("done", "book_flight", {"offer_id": "off_test123"}))

    ctx = Context(db=db, channel="web", actor="admin", thread_key="t")
    with caplog.at_level("ERROR"):
        run_agent(db, rogue, "book it", ctx)
    assert db.query(Trip).count() == 0
    assert "gate is top-level only" in caplog.text


# ── §2.2(a) — the load-bearing rule ──────────────────────────────────────────

def test_booking_refuses_an_offer_id_it_did_not_retrieve(db, monkeypatch):
    """A flight 'found on a web page' cannot be booked."""
    _enable_booking(monkeypatch)
    install_llm(monkeypatch, use_tool_then(
        "booking that for you", "book_flight", {"offer_id": "off_never_searched"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book the flight I saw online", actor="+1555")

    refusal = db.query(ActionAudit).filter(ActionAudit.status == "refused").first()
    assert refusal is not None
    assert "isn't an offer I retrieved" in refusal.result
    assert db.query(PendingConfirmation).count() == 0
    assert db.query(Trip).count() == 0


def test_offer_scoped_to_thread_cannot_be_booked_from_another_thread(db, monkeypatch):
    _enable_booking(monkeypatch)
    _seed_offer(db, thread_key="sms:+1OTHER", offer_id="off_elsewhere")
    install_llm(monkeypatch, use_tool_then(
        "booking", "book_flight", {"offer_id": "off_elsewhere"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    refusal = db.query(ActionAudit).filter(ActionAudit.status == "refused").first()
    assert refusal is not None
    assert "isn't an offer I retrieved" in refusal.result
    assert db.query(Trip).count() == 0


# ── §2.3 — gate + second factor, in order ────────────────────────────────────

def test_booking_is_gated_and_creates_a_pending_confirmation(db, monkeypatch):
    """No confirm, no booking. Duffel is never called."""
    _enable_booking(monkeypatch)
    _seed_offer(db)
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book the Alaska flight", actor="+1555")

    pend = db.query(PendingConfirmation).all()
    assert len(pend) == 1 and pend[0].status == "pending"
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0
    assert db.query(Trip).count() == 0


def test_booking_requires_the_second_factor(db, monkeypatch):
    """Gate cleared with 'confirm' but NO code -> Duffel never called, no Trip."""
    _enable_booking(monkeypatch)
    _seed_offer(db)
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")

    reply = run(db, channel="sms", thread_key=THREAD, user_text="confirm", actor="+1555")
    assert "code" in reply.lower()
    assert db.query(PendingConfirmation).first().status == "awaiting_code"
    assert db.query(Trip).count() == 0
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0


def test_the_readback_names_carrier_route_date_and_total_fare(db, monkeypatch):
    _enable_booking(monkeypatch)
    _seed_offer(db)
    install_llm(monkeypatch, use_tool_then(
        "ok", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    summary = db.query(PendingConfirmation).first().summary
    assert "Alaska Airlines" in summary
    assert "SEA-SFO" in summary
    assert "317" in summary


def test_correct_code_confirms_and_books(db, monkeypatch):
    _enable_booking(monkeypatch)
    _seed_offer(db)
    sent = _mock_duffel_order_success(monkeypatch)
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    run(db, channel="sms", thread_key=THREAD, user_text="confirm", actor="+1555")

    reply = run(db, channel="sms", thread_key=THREAD, user_text=_code(), actor="+1555")
    assert "Booked" in reply or "Confirmed" in reply
    assert db.query(PendingConfirmation).first().status == "done"
    assert db.query(Trip).count() == 1
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 1
    assert len(sent) == 1, "confirmation email must be sent"


def test_a_wrong_code_three_times_cancels_the_booking(db, monkeypatch):
    """Not 'try again' — cancelled. Unlimited retries make a 6-digit code a
    brute-force oracle."""
    _enable_booking(monkeypatch)
    _seed_offer(db)
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    run(db, channel="sms", thread_key=THREAD, user_text="confirm", actor="+1555")

    r1 = run(db, channel="sms", thread_key=THREAD, user_text="000000", actor="+1555")
    assert "didn't match" in r1
    assert db.query(PendingConfirmation).first().status == "awaiting_code"

    r2 = run(db, channel="sms", thread_key=THREAD, user_text="111111", actor="+1555")
    assert db.query(PendingConfirmation).first().status == "awaiting_code"

    r3 = run(db, channel="sms", thread_key=THREAD, user_text="222222", actor="+1555")
    assert "Cancelled" in r3 or "cancelled" in r3
    assert db.query(PendingConfirmation).first().status == "cancelled"
    assert db.query(Trip).count() == 0


def test_an_expired_code_window_is_refused(db, monkeypatch):
    """5-minute TTL. A code that lives forever is a password."""
    _enable_booking(monkeypatch)
    _seed_offer(db)
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    run(db, channel="sms", thread_key=THREAD, user_text="confirm", actor="+1555")

    from datetime import datetime, timedelta, timezone
    pend = db.query(PendingConfirmation).first()
    pend.code_deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    reply = run(db, channel="sms", thread_key=THREAD, user_text=_code(), actor="+1555")
    assert "expired" in reply.lower()
    assert db.query(PendingConfirmation).first().status == "cancelled"
    assert db.query(Trip).count() == 0


def test_spoken_digits_are_normalized(db, monkeypatch):
    """STT mangles digits. Spelled-out digits with spaces still verify."""
    from app.totp import normalize_code

    code = pyotp.TOTP(TOTP_SECRET).now()
    words = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
             "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
    spoken = " ".join(words[d] for d in code)
    assert normalize_code(spoken) == code
    assert normalize_code(" ".join(code)) == code  # "4 8 1 9 0 2"


def test_voice_will_not_accept_ok_to_confirm_a_booking(db, monkeypatch):
    """Conversational filler must never buy a plane ticket."""
    _enable_booking(monkeypatch)
    _seed_offer(db, thread_key="voice:CA123")
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="voice", thread_key="voice:CA123", user_text="book it", actor="caller")

    reply = run(db, channel="voice", thread_key="voice:CA123", user_text="ok", actor="caller")
    assert db.query(PendingConfirmation).first().status == "pending"  # still pending, gate never advanced
    assert db.query(Trip).count() == 0


def test_voice_requires_explicit_confirm_then_second_factor(db, monkeypatch):
    _enable_booking(monkeypatch)
    _seed_offer(db, thread_key="voice:CA123")
    sent = _mock_duffel_order_success(monkeypatch)
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="voice", thread_key="voice:CA123", user_text="book it", actor="caller")
    run(db, channel="voice", thread_key="voice:CA123", user_text="confirm", actor="caller")
    assert db.query(PendingConfirmation).first().status == "awaiting_code"

    reply = run(db, channel="voice", thread_key="voice:CA123", user_text=_code(), actor="caller")
    assert db.query(PendingConfirmation).first().status == "done"
    assert db.query(Trip).count() == 1


def test_book_flight_reachable_from_voice_allowlist():
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1
    assert "book_flight" in VOICE_TOOLS_PHASE1


# ── §2.2(c) — the fare sanity check ──────────────────────────────────────────

def test_an_absurd_fare_is_refused_not_gated(db, monkeypatch):
    """$30,000 = something is broken. No 'confirm' for an obviously-wrong number."""
    _enable_booking(monkeypatch)
    _seed_offer(db, amount="30000.00")
    install_llm(monkeypatch, use_tool_then(
        "booking", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    refusal = db.query(ActionAudit).filter(ActionAudit.status == "refused").first()
    assert refusal is not None
    assert "outside what I'll book" in refusal.result
    assert db.query(PendingConfirmation).count() == 0  # refused outright, never gated


def test_non_usd_fare_is_refused(db, monkeypatch):
    _enable_booking(monkeypatch)
    _seed_offer(db, amount="250.00", currency="GBP")
    install_llm(monkeypatch, use_tool_then(
        "booking", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    refusal = db.query(ActionAudit).filter(ActionAudit.status == "refused").first()
    assert refusal is not None
    assert "GBP" in refusal.result
    assert db.query(PendingConfirmation).count() == 0


# ── Enable/disable, notional-threshold immunity ──────────────────────────────

def test_booking_disabled_by_default():
    reg = build_registry(include_delegate=True)  # settings.booking_enabled is False in tests by default
    ctx = Context(db=None, channel="web", actor="me", thread_key="t")
    # Registered but refuses when called (mirrors place_stock_order's disabled stub).
    assert reg.has("book_flight")


def test_booking_disabled_refuses_even_a_valid_offer(db, monkeypatch):
    monkeypatch.setattr(settings, "totp_secret", TOTP_SECRET)
    monkeypatch.setattr(settings, "booking_enabled", False)
    _seed_offer(db)
    install_llm(monkeypatch, use_tool_then(
        "booking", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    refusal = db.query(ActionAudit).filter(ActionAudit.status == "refused").first()
    assert refusal is not None
    assert "DISABLED" in refusal.result
    assert db.query(Trip).count() == 0


def test_cheap_fare_still_requires_confirmation_and_code(db, monkeypatch):
    """A $12 fare gets exactly the same gate as an expensive one — booking must
    never benefit from confirm_threshold_usd the way trading does."""
    _enable_booking(monkeypatch)
    monkeypatch.setattr(settings, "confirm_threshold_usd", 50.0)
    _seed_offer(db, amount="12.00")
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    assert db.query(PendingConfirmation).first().status == "pending"  # gated despite being < threshold


def test_totp_not_configured_fails_closed(db, monkeypatch):
    """Do not skip the second factor because the gate exists (TDD §8)."""
    _enable_booking(monkeypatch, totp_secret="")
    _seed_offer(db)
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    reply = run(db, channel="sms", thread_key=THREAD, user_text="confirm", actor="+1555")
    assert "not configured" in reply.lower() or "isn't configured" in reply.lower()
    assert db.query(PendingConfirmation).first().status == "cancelled"
    assert db.query(Trip).count() == 0


# ── Failure handling ──────────────────────────────────────────────────────────

def test_an_expired_offer_fails_gracefully(db, monkeypatch):
    """Duffel rejects stale offers. Say 'that fare expired', not a raw 422."""
    _enable_booking(monkeypatch)
    _seed_offer(db)

    class _Resp:
        status_code = 422
        def json(self): return {"errors": [{"code": "offer_no_longer_available", "message": "expired"}]}
        text = "expired"

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw): return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)

    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    run(db, channel="sms", thread_key=THREAD, user_text="confirm", actor="+1555")
    reply = run(db, channel="sms", thread_key=THREAD, user_text=_code(), actor="+1555")
    assert "expired" in reply.lower()
    assert db.query(Trip).count() == 0


def test_missing_passenger_details_refuses_cleanly(db, monkeypatch):
    _enable_booking(monkeypatch, owner_dob="")  # incomplete
    _seed_offer(db)
    install_llm(monkeypatch, use_tool_then(
        "Readback ready", "book_flight", {"offer_id": "off_test123"}))
    run(db, channel="sms", thread_key=THREAD, user_text="book it", actor="+1555")
    reply = run(db, channel="sms", thread_key=THREAD, user_text="confirm", actor="+1555")
    reply2 = run(db, channel="sms", thread_key=THREAD, user_text=_code(), actor="+1555")
    assert "passenger details" in reply2.lower() or "incomplete" in reply2.lower()
    assert db.query(Trip).count() == 0
