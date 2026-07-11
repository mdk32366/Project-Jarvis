"""Tests for the agent expansion: tasks, ideas, email, calendar write, travel.

The most important tests here are the gate ones. `send_email` and `create_event`
are irreversible, and the confirmation gate only runs at the top level — so the
tests prove a sub-agent CANNOT execute them, and that they never leak into the
sub-agent registry.
"""

import pytest

from app.handlers.base import Context, build_registry
from app.models import AgentConfig, Idea, Task, Trip


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t1")


# ── The gate is structural, not a convention ─────────────────────────────────
def test_gated_tools_are_top_level_only():
    """The confirmation gate lives in orchestrator.run(). Sub-agents call
    reg.execute() directly and bypass it. So gated tools must NOT be in the
    sub-agent registry at all — otherwise gated=True is silently inert."""
    sub = build_registry()
    assert not sub.has("send_email"), "send_email leaked into the sub-agent registry"
    assert not sub.has("create_event"), "create_event leaked into the sub-agent registry"

    top = build_registry(include_delegate=True)
    assert top.has("send_email")
    assert top.has("create_event")
    assert top.is_gated("send_email")
    assert top.is_gated("create_event")


def test_subagent_refuses_gated_tool_even_if_roster_lists_it(db, monkeypatch, caplog):
    """THE load-bearing test.

    If someone edits an AgentConfig roster to include a gated tool, run_agent
    must REFUSE it — not execute it unconfirmed. Relying on the convention
    'don't put gated tools in rosters' fails silently; this fails closed.
    """
    from app.agents import Agent, run_agent
    from fakes import install_llm, use_tool_then

    sent = []
    monkeypatch.setattr("app.notifier.send_email",
                        lambda *a, **kw: sent.append(a) or "msg-id")

    # A rogue agent whose roster claims a gated tool.
    rogue = Agent("rogue", "d", "s", ["send_email"])
    install_llm(monkeypatch, use_tool_then("done", "send_email",
                                           {"to": "victim@example.com", "body": "hi"}))

    ctx_ = Context(db=db, channel="web", actor="admin", thread_key="t")
    with caplog.at_level("ERROR"):
        run_agent(db, rogue, "email victim", ctx_)

    # THE assertion: the gated tool did not execute. No email left the building.
    assert sent == [], "GATED TOOL EXECUTED FROM A SUB-AGENT — no confirmation!"
    # And the refusal was fed back to the model as the tool result.
    assert any("refusing" in r.message for r in caplog.records)


def test_send_email_requires_confirmation_at_top_level(db, monkeypatch):
    """Top-level: a gated tool creates a PendingConfirmation, doesn't execute."""
    from app.models import PendingConfirmation
    from app.orchestrator import run as orchestrate
    from fakes import install_llm, use_tool_then

    sent = []
    monkeypatch.setattr("app.notifier.send_email",
                        lambda *a, **kw: sent.append(a) or "msg-id")
    install_llm(monkeypatch, use_tool_then("Ready to send.", "send_email",
                                           {"to": "dave@example.com", "subject": "Q3",
                                            "body": "numbers attached"}))

    orchestrate(db=db, channel="web", thread_key="t9", user_text="email dave the Q3 numbers",
                actor="admin")

    assert sent == [], "email sent without confirmation"
    pending = db.query(PendingConfirmation).filter_by(thread_key="t9").first()
    assert pending is not None
    assert pending.tool == "send_email"
    assert "dave@example.com" in pending.summary


# ── Tasks ────────────────────────────────────────────────────────────────────
def test_add_and_list_task(ctx, db):
    from app.handlers.tasks import _add_task, _list_tasks

    out = _add_task({"title": "Book flight to Seattle", "due": "tomorrow"}, ctx)
    assert "added" in out.lower()

    t = db.query(Task).first()
    assert t.title == "Book flight to Seattle"
    assert t.due is not None
    assert t.source == "web"

    assert "Book flight" in _list_tasks({}, ctx)


def test_unparseable_due_date_says_so_rather_than_guessing(ctx, db):
    """A wrong due date is worse than no due date — say so out loud."""
    from app.handlers.tasks import _add_task

    out = _add_task({"title": "x", "due": "sometime around the third quarter maybe"}, ctx)
    assert db.query(Task).first().due is None
    assert "couldn't parse" in out.lower()


def test_complete_task(ctx, db):
    from app.handlers.tasks import _add_task, _complete_task

    _add_task({"title": "ship voice"}, ctx)
    tid = db.query(Task).first().id
    assert "complete" in _complete_task({"task_id": tid}, ctx).lower()
    assert db.query(Task).first().status == "done"


# ── Ideas ────────────────────────────────────────────────────────────────────
def test_capture_idea_persists_before_any_network_call(ctx, db):
    """The idea must hit the DB immediately. A GitHub outage can delay the
    commit; it must never eat the thought."""
    from app.handlers.ideas import _capture_idea

    out = _capture_idea({"title": "Voice-first infra control",
                         "body": "JARVIS should read node status aloud.",
                         "tags": "jarvis,infra"}, ctx)
    assert "captured" in out.lower()

    i = db.query(Idea).first()
    assert i.title == "Voice-first infra control"
    assert i.committed_sha == ""        # not yet pushed — that's the job's work
    assert i.source == "web"


def test_capture_idea_derives_title_when_absent(ctx, db):
    from app.handlers.ideas import _capture_idea

    _capture_idea({"body": "Use Kuma as the status backend. It knows the laptops."}, ctx)
    assert db.query(Idea).first().title.startswith("Use Kuma")


# ── Travel: parsing is conservative ──────────────────────────────────────────
def test_parses_alaska_confirmation():
    from app.handlers.travel import parse_itinerary

    email = """Your trip is confirmed!
    Confirmation code: ABCDEF
    Alaska Airlines AS 1234
    SEA to LAX
    Seat: 12A
    """
    got = parse_itinerary(email)
    assert got["confirmation"] == "ABCDEF"
    assert got["flight_no"] == "AS1234"
    assert got["carrier"] == "Alaska Airlines"
    assert got["origin"] == "SEA" and got["destination"] == "LAX"
    assert got["seat"] == "12A"


def test_non_confirmation_email_creates_no_trip(db):
    """A trip parsed WRONG is worse than one not parsed — you'd show up on the
    wrong day trusting it. Unparseable => no Trip row."""
    from app.handlers.travel import record_trip_from_email

    assert record_trip_from_email(db, "Lunch tomorrow?", "want to grab lunch") is None
    assert db.query(Trip).count() == 0


def test_duplicate_confirmation_is_not_double_recorded(db):
    from app.handlers.travel import record_trip_from_email

    body = "Confirmation code: XYZ123\nAlaska Airlines AS 99\nSEA to PDX"
    a = record_trip_from_email(db, "Your itinerary", body)
    b = record_trip_from_email(db, "Your itinerary", body)
    assert a.id == b.id
    assert db.query(Trip).count() == 1


def test_search_flights_is_honest_about_being_unconfigured(ctx):
    from app.handlers.travel import _search_flights

    out = _search_flights({"origin": "SEA", "destination": "LAX", "date": "2026-08-01"}, ctx)
    assert "not configured" in out.lower()
    # and it should point at what DOES work
    assert "already booked" in out.lower() or "confirmation" in out.lower()


# ── Voice reach ──────────────────────────────────────────────────────────────
def test_voice_can_draft_but_not_send_email():
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1

    assert "draft_email" in VOICE_TOOLS_PHASE1
    assert "send_email" not in VOICE_TOOLS_PHASE1      # gated, top-level only
    assert "create_event" not in VOICE_TOOLS_PHASE1    # gated, top-level only
    assert "place_stock_order" not in VOICE_TOOLS_PHASE1


def test_voice_agents_rosters_are_all_within_the_tool_allowlist():
    """Canary: an agent voice may reach whose tools aren't allowlisted would be
    permanently broken, and the config would be lying."""
    from app.agents import DEFAULT_AGENTS
    from app.channels.voice_pipeline import VOICE_AGENTS_PHASE1, VOICE_TOOLS_PHASE1

    for name in VOICE_AGENTS_PHASE1:
        extra = set(DEFAULT_AGENTS[name].tools) - VOICE_TOOLS_PHASE1
        assert not extra, f"agent {name!r} needs {extra} added to VOICE_TOOLS_PHASE1"


# ── Duffel flight search ─────────────────────────────────────────────────────
def _duffel_offer(amount, dep, arr, orig="SEA", dest="SFO", stops=0, carrier="Alaska Airlines"):
    segs = [{
        "origin": {"iata_code": orig},
        "destination": {"iata_code": "PDX" if stops else dest},
        "departing_at": dep,
        "arriving_at": arr,
        "operating_carrier": {"name": carrier},
    }]
    if stops:
        segs.append({
            "origin": {"iata_code": "PDX"},
            "destination": {"iata_code": dest},
            "departing_at": dep,
            "arriving_at": arr,
            "operating_carrier": {"name": carrier},
        })
    return {"total_amount": amount, "total_currency": "USD", "slices": [{"segments": segs}]}


def test_search_flights_returns_cheapest_first(ctx, monkeypatch):
    import httpx
    from app.config import settings
    from app.handlers import travel

    monkeypatch.setattr(settings, "duffel_api_key", "duffel_test_x")

    offers = [
        _duffel_offer("312.40", "2026-08-04T09:15:00", "2026-08-04T11:30:00"),
        _duffel_offer("189.00", "2026-08-04T06:00:00", "2026-08-04T08:20:00"),
        _duffel_offer("245.99", "2026-08-04T14:00:00", "2026-08-04T18:45:00", stops=1),
    ]

    class R:
        status_code = 200
        def json(self): return {"data": {"offers": offers}}

    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw): return R()

    monkeypatch.setattr(httpx, "Client", C)

    out = travel._search_flights(
        {"origin": "SEA", "destination": "SFO", "date": "2026-08-04"}, ctx)

    assert "3 options" in out
    assert out.index("$189") < out.index("$246")     # cheapest first
    assert "direct" in out and "1 stop" in out
    assert "Alaska Airlines" in out                   # operating carrier (US reg)
    assert "can't book" in out.lower()                # honest about the limit


def test_search_flights_supports_open_jaw(ctx, monkeypatch):
    """Fly into SFO Aug 4, home from Sacramento Aug 9 — the exact trip he tested."""
    import httpx
    from app.config import settings
    from app.handlers import travel

    monkeypatch.setattr(settings, "duffel_api_key", "duffel_test_x")
    captured = {}

    class R:
        status_code = 200
        def json(self): return {"data": {"offers": []}}

    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, **kw):
            captured.update(kw.get("json") or {})
            return R()

    monkeypatch.setattr(httpx, "Client", C)

    travel._search_flights({
        "origin": "SEA", "destination": "SFO", "date": "2026-08-04",
        "return_date": "2026-08-09", "return_from": "SMF", "return_to": "SEA",
    }, ctx)

    slices = captured["data"]["slices"]
    assert len(slices) == 2
    assert slices[0] == {"origin": "SEA", "destination": "SFO", "departure_date": "2026-08-04"}
    assert slices[1] == {"origin": "SMF", "destination": "SEA", "departure_date": "2026-08-09"}


def test_search_flights_reports_a_bad_key_plainly(ctx, monkeypatch):
    import httpx
    from app.config import settings
    from app.handlers import travel

    monkeypatch.setattr(settings, "duffel_api_key", "bad")

    class R:
        status_code = 401
        def json(self): return {}

    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw): return R()

    monkeypatch.setattr(httpx, "Client", C)
    out = travel._search_flights(
        {"origin": "SEA", "destination": "SFO", "date": "2026-08-04"}, ctx)
    assert "DUFFEL_API_KEY" in out


def test_search_flights_never_crashes_the_loop(ctx, monkeypatch):
    """A tool that raises would kill the whole turn. It must degrade to a string."""
    import httpx
    from app.config import settings
    from app.handlers import travel

    monkeypatch.setattr(settings, "duffel_api_key", "x")

    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw): raise RuntimeError("network is on fire")

    monkeypatch.setattr(httpx, "Client", C)
    out = travel._search_flights(
        {"origin": "SEA", "destination": "SFO", "date": "2026-08-04"}, ctx)
    assert "couldn't reach" in out.lower()
