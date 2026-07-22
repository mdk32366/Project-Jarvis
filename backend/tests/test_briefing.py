from app import briefing, jobs
from app.config import settings
from app.models import Job
from fakes import install_llm, say


def test_gather_context_runs_offline(db):
    ctx = briefing.gather_context(db)
    assert "Today's calendar" in ctx and "Not yet connected" in ctx


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
    # Retargeted from finance._get_portfolio (no longer gathered) onto a source
    # that IS gathered, so this keeps testing the survive-a-failing-source contract
    # rather than a call that no longer happens.
    from app.handlers import infra
    def boom(args, ctx): raise RuntimeError("fly api down")
    monkeypatch.setattr(infra, "_fleet_health", boom)
    ctx = briefing.gather_context(db)
    # a failing source does not raise and is quietly omitted, error text not leaked
    assert "Today's calendar" in ctx and "fly api down" not in ctx


def test_briefing_does_not_gather_portfolio(db, monkeypatch):
    """Portfolio is not gathered at all — the tool is never called, and neither
    'Portfolio' nor 'demo mode' appears in the assembled context."""
    from app.handlers import finance
    calls = []
    monkeypatch.setattr(finance, "_get_portfolio",
                        lambda args, ctx: calls.append(1) or "SHOULD NOT APPEAR")
    ctx = briefing.gather_context(db)
    assert calls == []
    assert "Portfolio" not in ctx
    assert "demo mode" not in ctx


def test_briefing_does_not_claim_project_status_unconnected(db):
    """Project tracking shipped in PR #40 — the brief must not list it under
    '## Not yet connected' (the audit-L12 stale-record contradiction, again)."""
    ctx = briefing.gather_context(db)
    assert "Project status" not in ctx


# ── Local network (Tailscale only; Proxmox/Kuma are Phase-1 stubs) ────────────
# gather_context imports _tailscale_status inside the function, so patch the
# handler module — the same pattern the traffic tests use for app.handlers.maps.

def test_briefing_omits_local_network_when_unconfigured(db, monkeypatch):
    """Tailscale unconfigured → returns the [tailscale not configured] sentinel →
    no ## Local network section, and no sentinel text leaks."""
    from app.handlers import tailscale
    monkeypatch.setattr(tailscale, "_tailscale_status",
                        lambda args, ctx: tailscale.NOT_CONFIGURED)
    ctx = briefing.gather_context(db)
    assert "## Local network" not in ctx
    assert "tailscale not configured" not in ctx


def test_briefing_includes_local_network_when_available(db, monkeypatch):
    """Tailscale answers → ## Local network present, carrying its content."""
    from app.handlers import tailscale
    monkeypatch.setattr(tailscale, "_tailscale_status",
                        lambda args, ctx: "All 4 devices are on the tailnet.")
    ctx = briefing.gather_context(db)
    assert "## Local network" in ctx
    assert "All 4 devices are on the tailnet." in ctx


def test_briefing_local_network_degrades_on_failure(db, monkeypatch):
    """Tailscale raises → section absent, error text not leaked, brief still built."""
    from app.handlers import tailscale
    monkeypatch.setattr(tailscale, "_tailscale_status",
                        lambda args, ctx: (_ for _ in ()).throw(RuntimeError("tailnet down")))
    ctx = briefing.gather_context(db)
    assert "Today's calendar" in ctx
    assert "## Local network" not in ctx
    assert "tailnet down" not in ctx


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


def test_briefing_traffic_suppresses_guidance_strings(db, monkeypatch):
    """_get_traffic returns guidance strings (no-route, unresolved place) that are
    NOT traffic reports. They must never appear as a ## Traffic section — the brief
    shows a real commute or nothing, never a confusing 'traffic' line."""
    monkeypatch.setattr(settings, "google_maps_api_key", "fake-key")
    monkeypatch.setattr(settings, "owner_home_address", "Stanwood WA")
    monkeypatch.setattr(settings, "owner_work_address", "Redmond WA")

    from app.handlers import maps
    monkeypatch.setattr(maps, "_get_traffic",
                        lambda args, ctx: "No route found from Stanwood WA to Redmond WA.")
    ctx = briefing.gather_context(db)
    assert "## Traffic" not in ctx
    assert "No route found" not in ctx


def test_briefing_traffic_shows_a_real_report(db, monkeypatch):
    """A genuine commute report (carries the ETA phrase) IS surfaced."""
    monkeypatch.setattr(settings, "google_maps_api_key", "fake-key")
    monkeypatch.setattr(settings, "owner_home_address", "Stanwood WA")
    monkeypatch.setattr(settings, "owner_work_address", "Redmond WA")

    from app.handlers import maps
    monkeypatch.setattr(
        maps, "_get_traffic",
        lambda args, ctx: ("38 minutes to Redmond WA, 22 miles via I-405. That's 12 "
                           "minutes slower than usual — heavy traffic. Leaving now puts "
                           "you there about 7:51."),
    )
    ctx = briefing.gather_context(db)
    assert "## Traffic" in ctx and "heavy traffic" in ctx


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


# ── M3: a failed briefing must not be read aloud on a call ────────────────────
def test_is_speakable_briefing_rejects_degraded_output():
    assert briefing.is_speakable_briefing("Good morning. Markets are up.") is True
    assert briefing.is_speakable_briefing("") is False
    assert briefing.is_speakable_briefing("(no briefing generated)\n\n<data>") is False
    assert briefing.is_speakable_briefing("(briefing failed) boom\n\nHere is the raw data: <dump>") is False


def test_briefing_call_emails_instead_of_ringing_on_compose_failure(db, monkeypatch):
    """The call path must never read an error + raw dump aloud (audit M3) — but a
    scheduled brief that comes up empty must still be VISIBLE, not silently dropped
    (health TDD §6). So it emails the owner instead of ringing."""
    from app.models import OutboundCall
    import app.notifier as notifier

    sent = []
    monkeypatch.setattr(notifier, "send_email",
                        lambda to, subj, body: sent.append((to, subj, body)))
    monkeypatch.setattr(settings, "owner_email", "me@example.com")
    monkeypatch.setattr(briefing, "compose_briefing",
                        lambda db: "(briefing failed) boom\n\nHere is the raw data:\n\n<dump>")

    result = jobs._handle_briefing_call(db, {})

    assert db.query(OutboundCall).count() == 0        # still does NOT ring
    assert len(sent) == 1 and "boom" in sent[0][2]    # but the owner is notified
    assert "emailed" in result
