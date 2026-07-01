"""SMS -> email-copy: a text reply is also mirrored to the owner's inbox."""
from app import jobs
from app.channels.sms_pipeline import handle_inbound
from app.config import settings
from app.models import Job
from fakes import install_llm, say


def test_inbound_enqueues_email_copy(db, monkeypatch):
    monkeypatch.setattr(settings, "sms_email_copy", True)
    monkeypatch.setattr(settings, "owner_email", "me@example.com")
    install_llm(monkeypatch, say("AAPL is ~$200."))
    handle_inbound(db, "+15551230000", "price of AAPL?")
    ec = db.query(Job).filter(Job.kind == "email_copy").all()
    assert len(ec) == 1
    import json
    payload = json.loads(ec[0].payload)
    assert payload["to"] == "me@example.com"
    assert "AAPL" in payload["body"]


def test_no_email_copy_when_disabled(db, monkeypatch):
    monkeypatch.setattr(settings, "sms_email_copy", False)
    install_llm(monkeypatch, say("hi"))
    handle_inbound(db, "+15551230000", "hi")
    assert db.query(Job).filter(Job.kind == "email_copy").count() == 0


def test_owner_email_falls_back_to_allowed_sender(monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "")
    # conftest sets ALLOWED_SENDERS=me@example.com
    assert settings.owner_email_resolved == "me@example.com"


def test_email_copy_handler_sends(db, monkeypatch):
    sent = {}
    import app.notifier as notifier
    monkeypatch.setattr(notifier, "send_email",
                        lambda to, subject, body, **kw: sent.update(to=to, subject=subject, body=body) or "mid")
    jobs.enqueue(db, "email_copy", {"to": "me@example.com", "subject": "S", "body": "B"})
    jobs.process_available(db)
    assert sent == {"to": "me@example.com", "subject": "S", "body": "B"}
    assert db.query(Job).filter(Job.kind == "email_copy").first().status == "done"
