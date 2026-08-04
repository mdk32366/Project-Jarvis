"""The email confirmation latch — quoted replies, and a TTL that fits the transport.

2026-08-04: every gated tool was silently unreachable by email. Eleven confirmations
across two project creations, none resolved, and `actions_audit` read green
throughout. Two independent causes, each sufficient on its own:

  A. `_body_text` returns the whole `text/plain` part, quoted thread included, so
     `orchestrator._bare_match` — which requires EVERY token to be affirmative or
     filler — returned False on every reply. The turn fell through to normal
     handling, the model read the quoted request as a fresh one, and raised a NEW
     confirmation. That is the loop.
  B. A 900-second TTL against observed reply gaps of 76, 32 and 45 minutes.

Fixing one and shipping leaves the latch in place, so both are covered here.
"""
import email as email_lib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.channels import email_pipeline as ep
from app.channels.email_pipeline import strip_quoted_text
from app.config import settings
from app.models import ActionAudit, PendingConfirmation
from app.orchestrator import _expire_stale_pending, _ttl, run
from fakes import install_llm, say, use_tool_then

OWNER = "me@example.com"          # ALLOWED_SENDERS in conftest
JARVIS = "jarvis@example.com"

GMAIL_QUOTE = (
    f"On Tue, Aug 4, 2026 at 6:19 AM {JARVIS} wrote:\n"
    "> One more explicit confirm needed on this one — reply confirm and I'll\n"
    "> create the MANETMDK repo (public, from Idea #5).\n"
    "> \n"
    "> Nothing has been created yet.\n"
)


# ── §3 Step 1: the stripper, as a pure function ──────────────────────────────
def test_strips_gmail_attribution_and_quote_block():
    body = f"Confirm.\n\n{GMAIL_QUOTE}"
    assert strip_quoted_text(body) == "Confirm."


def test_strips_a_wrapped_gmail_attribution_line():
    """Gmail hard-wraps a long attribution, so 'wrote:' lands on the next line."""
    body = (
        "Confirm.\n\n"
        f"On Tue, Aug 4, 2026 at 6:19 AM Jarvis Majorus <{JARVIS}>\n"
        "wrote:\n"
        "> the readback\n"
    )
    assert strip_quoted_text(body) == "Confirm."


def test_strips_apple_mail_attribution():
    body = (
        "Yes\n\n"
        f"On Aug 4, 2026, at 6:19 AM, JARVIS <{JARVIS}> wrote:\n"
        "> the readback\n"
    )
    assert strip_quoted_text(body) == "Yes"


def test_strips_bare_quote_levels_with_no_attribution():
    body = "confirm\n\n> One more explicit confirm needed on this one.\n> Nothing yet.\n"
    assert strip_quoted_text(body) == "confirm"


def test_strips_outlook_original_message_separator():
    body = (
        "Confirm.\n\n"
        "-----Original Message-----\n"
        "From: JARVIS <jarvis@example.com>\n"
        "Sent: Tuesday, August 4, 2026 6:19 AM\n"
        "Reply confirm and I'll create the repo.\n"
    )
    assert strip_quoted_text(body) == "Confirm."


def test_strips_outlook_horizontal_rule():
    body = "Confirm.\n\n" + "_" * 32 + "\nFrom: JARVIS\nthe readback\n"
    assert strip_quoted_text(body) == "Confirm."


def test_strips_outlook_inline_header_block():
    body = (
        "Confirm.\n\n"
        "From: JARVIS <jarvis@example.com>\n"
        "Sent: Tuesday, August 4, 2026 6:19 AM\n"
        "To: Matt\n"
        "Subject: Re: MANETMDK\n\n"
        "Reply confirm and I'll create the repo.\n"
    )
    assert strip_quoted_text(body) == "Confirm."


def test_a_lone_header_shaped_line_is_prose_not_a_quote():
    """'To: pick up milk' is an instruction. Only a BLOCK of headers is a quote."""
    body = "Confirm.\nTo: the hardware store by six\n"
    assert strip_quoted_text(body) == body


def test_strips_signature_delimiter():
    body = "Confirm.\n\n-- \nMatt\nSent from my phone\n"
    assert strip_quoted_text(body) == "Confirm."


def test_an_unquoted_message_is_returned_unchanged():
    body = "Please add milk to the shopping list."
    assert strip_quoted_text(body) == body


def test_an_interleaved_reply_is_left_intact():
    """§3: an owner typing BETWEEN quote levels is writing a NEW instruction, not a
    bare confirmation. Leaving it intact is what makes it fall through to normal
    handling — reassembling it would manufacture a confirmation he did not give."""
    body = (
        "Confirm.\n\n"
        f"On Tue, Aug 4, 2026 at 6:19 AM {JARVIS} wrote:\n"
        "> One more explicit confirm needed on this one.\n"
        "and while you're at it, email Dave the parts list\n"
        "> Nothing has been created yet.\n"
    )
    assert strip_quoted_text(body) == body


# ── P5: the stripper cannot erase a message ──────────────────────────────────
def test_a_top_posted_reply_with_nothing_typed_returns_the_original_body():
    """PLANT P5: remove the empty-result fallback. An empty `user_text` would be a
    different bug, and a stripper that can silently erase a message is worse than
    one that occasionally under-strips."""
    body = f"\n\n{GMAIL_QUOTE}"
    assert strip_quoted_text(body) == body
    assert strip_quoted_text(body).strip() != ""


def test_a_quote_only_body_with_no_attribution_also_survives():
    body = "> just the readback, nothing typed above it\n"
    assert strip_quoted_text(body) == body


@pytest.mark.parametrize("body", ["", "   ", "\n\n"])
def test_empty_input_is_returned_unchanged(body):
    assert strip_quoted_text(body) == body


# ── The IMAP end-to-end path: P1 and P2 ──────────────────────────────────────
class _FakeIMAP:
    """Just enough IMAP4_SSL for process_inbox. No network, no Gmail."""

    def __init__(self, raw_messages):
        self._raw = raw_messages
        self.seen = []

    def login(self, user, password):
        return "OK", []

    def select(self, folder):
        return "OK", []

    def search(self, charset, criterion):
        ids = b" ".join(str(i + 1).encode() for i in range(len(self._raw)))
        return "OK", [ids]

    def fetch(self, mid, spec):
        return "OK", [(None, self._raw[int(mid) - 1])]

    def store(self, mid, cmd, flags):
        self.seen.append(mid)
        return "OK", []

    def logout(self):
        return "OK", []


def _raw_email(body: str, subject: str = "Re: MANETMDK") -> bytes:
    msg = email_lib.message.EmailMessage()
    msg["From"] = f"Matt <{OWNER}>"
    msg["To"] = JARVIS
    msg["Subject"] = subject
    msg["Message-ID"] = "<abc@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


def _deliver(monkeypatch, body: str) -> list:
    """Run one inbound message through the REAL process_inbox call site.

    Deliberately end-to-end rather than calling `strip_quoted_text` from the test:
    the defect was in what reached `orchestrate()`, so the test has to exercise the
    call site that feeds it. A plant that bypasses the stripper in
    `email_pipeline.process_inbox` must redden these tests, and it cannot if the
    test does the stripping itself.
    """
    monkeypatch.setattr(settings, "gmail_address", JARVIS)
    monkeypatch.setattr(settings, "gmail_app_password", "app-password")
    monkeypatch.setattr(ep.imaplib, "IMAP4_SSL", lambda host, port: _FakeIMAP([_raw_email(body)]))
    sent: list = []
    monkeypatch.setattr(ep, "send_email", lambda *a, **k: sent.append((a, k)))
    ep.process_inbox(send=True)
    return sent


def _pending_for_email(db, monkeypatch):
    """Raise a real gated action over the email channel and return its row."""
    monkeypatch.setattr(settings, "enable_trading", True)
    install_llm(monkeypatch, use_tool_then(
        "Buy 3 AAPL — reply confirm.", "place_stock_order",
        {"symbol": "AAPL", "qty": 3, "side": "buy"}))
    run(db, channel="email", thread_key=OWNER, user_text="buy 3 AAPL", actor=OWNER)
    row = db.query(PendingConfirmation).first()
    assert row is not None and row.status == "pending"
    return row


def test_a_reply_carrying_a_full_gmail_quote_block_resolves(db, monkeypatch):
    """PLANT P1: bypass the stripper in process_inbox (pass the raw body).

    This is the outage. Without the strip, `_bare_match` sees hundreds of quoted
    content words, returns False, and the confirmation is never resolved.
    """
    pend = _pending_for_email(db, monkeypatch)
    install_llm(monkeypatch, say("Understood."))

    _deliver(monkeypatch, f"Confirm.\n\n{GMAIL_QUOTE}")

    db.expire_all()
    assert db.get(PendingConfirmation, pend.id).status == "done"
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 1


def test_a_reply_carrying_a_new_instruction_does_not_confirm(db, monkeypatch):
    """PLANT P2: make the stripper truncate at the first BLANK LINE instead of the
    first quote marker — the body below then collapses to a bare "Confirm.".

    The most important test in this file. The 36-hour-old-email bug is the failure
    this system already made once, and a stripper that is too aggressive
    reintroduces it by a new route. The planted value is a blank-line cut, which no
    branch of the real stripper can produce.
    """
    pend = _pending_for_email(db, monkeypatch)
    install_llm(monkeypatch, say("I'll look at the parts list."))

    _deliver(
        monkeypatch,
        "Confirm.\n\nYes please also send Dave the parts list.\n\n" + GMAIL_QUOTE,
    )

    db.expire_all()
    assert db.get(PendingConfirmation, pend.id).status == "pending", (
        "a reply carrying a NEW instruction must not resolve the buffered action"
    )
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0


def test_the_trip_capture_path_still_sees_the_whole_message(db, monkeypatch):
    """The strip is at the orchestrate() call site, NOT inside `_body_text` — an
    airline confirmation is parsed from the full body and must not be truncated."""
    raw = _raw_email(f"Booking confirmed.\n\n{GMAIL_QUOTE}")
    msg = email_lib.message_from_bytes(raw)
    assert "MANETMDK repo" in ep._body_text(msg)


# ── §4 Step 2: the per-channel TTL — P3 and P4 ───────────────────────────────
def _backdate(db, pend_id: int, minutes: int) -> None:
    old = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(tzinfo=None)
    db.execute(text("UPDATE pending_confirmations SET created_at = :t WHERE id = :i"),
               {"t": old, "i": pend_id})
    db.commit()


def test_email_gets_a_longer_ttl_than_the_live_channels(db):
    assert _ttl(db, "email") == settings.email_confirmation_ttl_seconds == 14400
    for channel in ("voice", "sms", "web"):
        assert _ttl(db, channel) == settings.pending_confirmation_ttl_seconds == 900


def test_the_email_ttl_is_tunable_from_the_runtime_overlay(db):
    from app import runtime_settings

    runtime_settings.set_effective(db, "email_confirmation_ttl_seconds", 7200)
    assert _ttl(db, "email") == 7200
    with pytest.raises(ValueError):
        runtime_settings.set_effective(db, "email_confirmation_ttl_seconds", 60)
    with pytest.raises(ValueError):
        runtime_settings.set_effective(db, "email_confirmation_ttl_seconds", 999999)


def test_an_email_confirmation_45_minutes_after_the_readback_still_resolves(db, monkeypatch):
    """PLANT P3 / P4-a: point `_ttl("email")` at the global default.

    Reads the age check in `_resolve_pending`. 45 minutes is one of the three gaps
    actually observed on 2026-08-04 (76, 32, 45); every one of them exceeded 900s.
    """
    pend = _pending_for_email(db, monkeypatch)
    _backdate(db, pend.id, 45)

    reply = run(db, channel="email", thread_key=OWNER, user_text="Confirm.", actor=OWNER)

    db.expire_all()
    assert "Done" in reply
    assert db.get(PendingConfirmation, pend.id).status == "done"


def test_the_sweep_leaves_a_45_minute_old_email_pending(db, monkeypatch):
    """PLANT P4-b: point `_ttl("email")` at the global default.

    Reads the cutoff in `_expire_stale_pending`. P4 is the property that these two
    readers share ONE rule: the same plant must redden this test AND the
    `_resolve_pending` test above. If only one reddens they are not sharing it.
    """
    pend = _pending_for_email(db, monkeypatch)
    _backdate(db, pend.id, 45)

    assert _expire_stale_pending(db, OWNER, "email") == 0

    db.expire_all()
    assert db.get(PendingConfirmation, pend.id).status == "pending"


def test_the_sweep_still_expires_a_five_hour_old_email_pending(db, monkeypatch):
    """Don't over-correct: past the email window a stale 'confirm' must still die."""
    pend = _pending_for_email(db, monkeypatch)
    _backdate(db, pend.id, 300)

    assert _expire_stale_pending(db, OWNER, "email") == 1

    db.expire_all()
    assert db.get(PendingConfirmation, pend.id).status == "expired"


def test_the_sweep_uses_the_channel_it_is_given_not_the_thread_key(db, monkeypatch):
    """The thread key is an address; deriving the transport from its shape is the
    proxy-instead-of-the-fact mistake. Same row, same key, different channel ->
    different answer."""
    pend = _pending_for_email(db, monkeypatch)
    _backdate(db, pend.id, 45)

    assert _expire_stale_pending(db, OWNER, "sms") == 1
    db.expire_all()
    assert db.get(PendingConfirmation, pend.id).status == "expired"


def test_the_live_channels_did_not_get_wider(db, monkeypatch):
    """The TTL is a safety control. Fixing email must not widen voice/sms/web."""
    monkeypatch.setattr(settings, "enable_trading", True)
    install_llm(monkeypatch, use_tool_then(
        "buy 3 AAPL — reply yes", "place_stock_order",
        {"symbol": "AAPL", "qty": 3, "side": "buy"}))
    run(db, channel="sms", thread_key="+1555", user_text="buy 3 AAPL", actor="+1555")
    pend = db.query(PendingConfirmation).first()
    _backdate(db, pend.id, 45)

    install_llm(monkeypatch, say("Sure — what do you need?"))
    run(db, channel="sms", thread_key="+1555", user_text="yes", actor="+1555")

    db.expire_all()
    assert db.get(PendingConfirmation, pend.id).status == "expired"
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0
