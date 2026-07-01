from app.channels.sms_pipeline import handle_inbound, is_allowed, to_twiml
from app.config import normalize_number
from app.models import ContactWhitelist
from fakes import install_llm, say


def test_normalize_number():
    assert normalize_number("+1 (555) 123-0000") == "+15551230000"
    assert normalize_number("555-123-0000") == "5551230000"
    assert normalize_number("") == ""


def test_whitelist_from_config(db):
    assert is_allowed(db, "+1 555 123 0000") is True   # matches ALLOWED_NUMBERS
    assert is_allowed(db, "+19998887777") is False


def test_whitelist_from_contacts_table(db):
    db.add(ContactWhitelist(channel="sms", identifier="+19998887777", label="burner"))
    db.commit()
    assert is_allowed(db, "+1 (999) 888-7777") is True


def test_inbound_non_whitelisted_returns_none(db, monkeypatch):
    install_llm(monkeypatch, say("should not be reached"))
    assert handle_inbound(db, "+19998887777", "hello") is None


def test_inbound_whitelisted_orchestrates(db, monkeypatch):
    install_llm(monkeypatch, say("Hi Matt, AAPL is ~$200."))
    reply = handle_inbound(db, "+15551230000", "price of AAPL?")
    assert "AAPL" in reply


def test_twiml_escaping():
    xml = to_twiml("A & B < C")
    assert "<Response><Message>" in xml
    assert "&amp;" in xml and "&lt;" in xml
    assert to_twiml("").endswith("<Response></Response>")
