"""Unit-level checks on the email channel helpers (no live IMAP)."""
import email as email_lib

from app.channels.email_pipeline import _body_text, _decode, _is_allowed
from app.models import ContactWhitelist


def test_is_allowed_config_and_case(db):
    assert _is_allowed(db, "me@example.com") is True
    assert _is_allowed(db, "ME@EXAMPLE.COM".lower()) is True
    assert _is_allowed(db, "stranger@x.com") is False


def test_is_allowed_contacts_table(db):
    db.add(ContactWhitelist(channel="email", identifier="friend@x.com"))
    db.commit()
    assert _is_allowed(db, "friend@x.com") is True


def test_body_text_prefers_plain():
    raw = (
        "From: a@b.com\r\nTo: c@d.com\r\nSubject: hi\r\n"
        "Content-Type: multipart/alternative; boundary=BB\r\n\r\n"
        "--BB\r\nContent-Type: text/plain\r\n\r\nHELLO PLAIN\r\n"
        "--BB\r\nContent-Type: text/html\r\n\r\n<p>HELLO HTML</p>\r\n--BB--\r\n"
    )
    msg = email_lib.message_from_string(raw)
    assert "HELLO PLAIN" in _body_text(msg)


def test_decode_handles_none():
    assert _decode(None) == ""
