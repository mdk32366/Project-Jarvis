from app import briefing
from app.config import settings
from app.models import Job
from fakes import install_llm, say


def test_gather_context_runs_offline(db):
    ctx = briefing.gather_context(db)
    assert "Today's calendar" in ctx and "Not yet connected" in ctx  # portfolio omitted in demo mode


def test_compose_briefing(db, monkeypatch):
    install_llm(monkeypatch, say("Good morning! 3 meetings today. Portfolio flat."))
    out = briefing.compose_briefing(db)
    assert "Good morning" in out


def test_send_briefing_emails_owner(db, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "me@example.com")
    install_llm(monkeypatch, say("Briefing body."))
    sent = {}
    import app.notifier as notifier
    monkeypatch.setattr(notifier, "send_email",
                        lambda to, subject, body, **kw: sent.update(to=to, subject=subject, body=body))
    status = briefing.send_briefing(db)
    assert sent["to"] == "me@example.com" and "Briefing body." in sent["body"]
    assert "emailed" in status


def test_morning_briefing_job(db, monkeypatch):
    from app import jobs
    monkeypatch.setattr(settings, "owner_email", "me@example.com")
    install_llm(monkeypatch, say("Body."))
    import app.notifier as notifier
    monkeypatch.setattr(notifier, "send_email", lambda *a, **k: None)
    jobs.enqueue(db, "morning_briefing", {})
    jobs.process_available(db)
    j = db.query(Job).filter(Job.kind == "morning_briefing").first()
    assert j.status == "done"


def test_briefing_api(client, auth_headers, monkeypatch):
    install_llm(monkeypatch, say("Your day ahead."))
    r = client.get("/api/briefing", headers=auth_headers)
    assert r.status_code == 200 and "Your day ahead." in r.json()["briefing"]


def test_briefing_survives_failing_source(db, monkeypatch):
    from app.handlers import finance
    def boom(args, ctx): raise RuntimeError("alpaca down")
    monkeypatch.setattr(finance, "_get_portfolio", boom)
    ctx = briefing.gather_context(db)
    # a failing/absent portfolio does not raise and is quietly omitted
    assert "Today's calendar" in ctx and "alpaca down" not in ctx
