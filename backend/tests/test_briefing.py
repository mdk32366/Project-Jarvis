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


# ── Weather ───────────────────────────────────────────────────────────────────

def test_briefing_omits_weather_when_not_configured(db, monkeypatch):
    """No owner_home_address → weather section absent, no error leaked."""
    monkeypatch.setattr(settings, "owner_home_address", "")
    ctx = briefing.gather_context(db)
    assert "## Weather" not in ctx
    assert "Today's calendar" in ctx


def test_briefing_includes_weather_when_configured(db, monkeypatch):
    """When _nws_weather returns real content the Weather section appears."""
    monkeypatch.setattr(settings, "owner_home_address", "123 Main St, Stanwood WA")
    monkeypatch.setattr(briefing, "_nws_weather",
                        lambda addr: "Today: Sunny, high near 74.\nTonight: Clear, low 52.")
    ctx = briefing.gather_context(db)
    assert "## Weather" in ctx
    assert "Sunny" in ctx


def test_briefing_weather_degrades_gracefully_on_failure(db, monkeypatch):
    """NWS failure is swallowed — briefing continues, error text not leaked."""
    monkeypatch.setattr(settings, "owner_home_address", "123 Main St, Stanwood WA")
    monkeypatch.setattr(briefing, "_nws_weather",
                        lambda addr: (_ for _ in ()).throw(RuntimeError("NWS down")))
    ctx = briefing.gather_context(db)
    assert "Today's calendar" in ctx
    assert "NWS down" not in ctx
    assert "## Weather" not in ctx


# ── Marine ────────────────────────────────────────────────────────────────────

def test_briefing_omits_marine_when_nws_fails(db, monkeypatch):
    """NWS unreachable → marine section silently absent."""
    monkeypatch.setattr(briefing, "_nws_marine",
                        lambda: (_ for _ in ()).throw(RuntimeError("api.weather.gov down")))
    ctx = briefing.gather_context(db)
    assert "Today's calendar" in ctx
    assert "api.weather.gov down" not in ctx
    assert "## Marine" not in ctx


def test_briefing_includes_marine_forecast_when_available(db, monkeypatch):
    """Marine section appears with forecast text when NWS responds."""
    monkeypatch.setattr(briefing, "_nws_marine",
                        lambda: "Today: SW wind 5 kt, waves 2 ft or less, 68°F.\nTonight: Calm.")
    ctx = briefing.gather_context(db)
    assert "## Marine" in ctx
    assert "SW wind" in ctx


def test_briefing_marine_surfaces_sca_prominently(db, monkeypatch):
    """An active Small Craft Advisory appears first, prefixed ADVISORY:."""
    monkeypatch.setattr(
        briefing, "_nws_marine",
        lambda: (
            "ADVISORY: Small Craft Advisory in effect until 8 PM PDT. N wind 20-25 kt.\n"
            "Today: N wind 20 to 25 kt, waves 4 to 6 ft."
        ),
    )
    ctx = briefing.gather_context(db)
    assert "## Marine" in ctx
    assert "ADVISORY" in ctx
    assert "Small Craft" in ctx


# ── Traffic ───────────────────────────────────────────────────────────────────

def test_briefing_omits_traffic_when_not_configured(db, monkeypatch):
    """No Maps key → traffic section absent."""
    monkeypatch.setattr(settings, "google_maps_api_key", "")
    ctx = briefing.gather_context(db)
    assert "## Traffic" not in ctx


def test_briefing_includes_traffic_when_delay_is_significant(db, monkeypatch):
    """Meaningful delay surfaces the traffic section."""
    monkeypatch.setattr(briefing, "_traffic_brief",
                        lambda db: "38 minutes to work. That's 14 minutes slower than usual — heavy traffic.")
    ctx = briefing.gather_context(db)
    assert "## Traffic" in ctx
    assert "heavy traffic" in ctx


def test_briefing_traffic_stays_quiet_when_no_delay(db, monkeypatch):
    """_get_traffic 'Traffic is light.' → _traffic_brief returns '' → section omitted."""
    monkeypatch.setattr(settings, "google_maps_api_key", "fake-key")
    monkeypatch.setattr(settings, "owner_home_address", "Stanwood WA")
    monkeypatch.setattr(settings, "owner_work_address", "Redmond WA")

    from app.handlers import maps
    monkeypatch.setattr(
        maps, "_get_traffic",
        lambda args, ctx: "23 minutes to work, 14 miles via I-5. Traffic is light. Leaving now puts you there about 7:43.",
    )
    ctx = briefing.gather_context(db)
    assert "## Traffic" not in ctx


# ── News ──────────────────────────────────────────────────────────────────────

def test_briefing_omits_news_when_not_configured(db, monkeypatch):
    """No Tavily key → news section absent."""
    monkeypatch.setattr(settings, "tavily_api_key", "")
    ctx = briefing.gather_context(db)
    assert "## News" not in ctx


def test_briefing_includes_news_when_configured(db, monkeypatch):
    """News section appears when _news_brief returns content."""
    monkeypatch.setattr(briefing, "_news_brief",
                        lambda: "Markets steady amid trade talks.\n- Pacific Northwest heat wave expected\n- Fed holds rates")
    ctx = briefing.gather_context(db)
    assert "## News" in ctx
    assert "Markets" in ctx


def test_briefing_news_degrades_gracefully_on_failure(db, monkeypatch):
    """Tavily failure is swallowed — briefing continues, error text not leaked."""
    monkeypatch.setattr(briefing, "_news_brief",
                        lambda: (_ for _ in ()).throw(RuntimeError("tavily down")))
    ctx = briefing.gather_context(db)
    assert "Today's calendar" in ctx
    assert "tavily down" not in ctx
    assert "## News" not in ctx
