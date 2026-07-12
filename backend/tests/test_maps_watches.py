"""Traffic, tailnet, watches, and the expanded owner profile."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.handlers.base import Context
from app.models import OutboundCall, Watch


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="voice", actor="+15551230000", thread_key="t")


@pytest.fixture
def owner_phone(monkeypatch):
    monkeypatch.setattr(settings, "owner_phone", "+15551230000")   # in ALLOWED_NUMBERS
    monkeypatch.setattr(settings, "outbound_calls_enabled", True)
    monkeypatch.setattr(settings, "voice_public_url_base", "https://jarvis-mdk.fly.dev")


@pytest.fixture
def maps_key(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "k")
    monkeypatch.setattr(settings, "owner_home_address", "Stanwood, WA")
    monkeypatch.setattr(settings, "owner_places",
                        "work=Pfizer, Bothell WA; boat=Skyline Marina, Anacortes WA")


def _directions(free_s, traffic_s):
    class R:
        status_code = 200
        def json(self):
            return {"status": "OK", "routes": [{
                "summary": "I-5 S",
                "legs": [{
                    "distance": {"text": "42 mi"},
                    "duration": {"value": free_s},
                    "duration_in_traffic": {"value": traffic_s},
                }],
            }]}
    return R()


def _client(resp):
    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **kw): return resp
    return C


# ── Traffic: the daily-use one ───────────────────────────────────────────────
def test_traffic_reports_the_delay_not_just_the_duration(ctx, maps_key, monkeypatch):
    """A free-flow duration is a timetable. The delay is why you asked."""
    import httpx
    from app.handlers.maps import _get_traffic

    monkeypatch.setattr(httpx, "Client", _client(_directions(2400, 3900)))   # 40 -> 65 min

    out = _get_traffic({"destination": "work"}, ctx)
    assert "1 hour 5 minutes" in out
    assert "25 minutes slower" in out and "heavy traffic" in out
    assert "42 mi" in out


def test_traffic_stays_quiet_when_there_is_no_traffic(ctx, maps_key, monkeypatch):
    """'No delay' announced every single morning is noise."""
    import httpx
    from app.handlers.maps import _get_traffic

    monkeypatch.setattr(httpx, "Client", _client(_directions(2400, 2450)))

    out = _get_traffic({"destination": "work"}, ctx)
    assert "Traffic is light" in out
    assert "slower" not in out


def test_leave_by_is_the_question_people_actually_ask(ctx, maps_key, monkeypatch):
    """'What time do I need to leave' — not 'how long does it take'."""
    import httpx
    from app.handlers.maps import _get_traffic

    monkeypatch.setattr(httpx, "Client", _client(_directions(2400, 3600)))   # 60 min

    out = _get_traffic({"destination": "work", "arrive_by": "9am"}, ctx)
    assert "leave by 8:00 AM" in out


def test_named_places_beat_reciting_an_address(ctx, maps_key, monkeypatch):
    import httpx
    from app.handlers.maps import _resolve

    assert _resolve("work") == "Pfizer, Bothell WA"
    assert _resolve("the boat") == "Skyline Marina, Anacortes WA"
    assert _resolve("Space Needle") == "Space Needle"      # unknown passes through


def test_traffic_requests_live_data_not_a_timetable(ctx, maps_key, monkeypatch):
    """WITHOUT departure_time, Google returns free-flow duration. That single
    parameter is the entire point of the feature."""
    import httpx
    from app.handlers.maps import _get_traffic

    captured = {}

    class C:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None, **kw):
            captured.update(params or {})
            return _directions(100, 100)

    monkeypatch.setattr(httpx, "Client", C)
    _get_traffic({"destination": "work"}, ctx)

    assert captured["departure_time"] == "now"
    assert captured["origin"] == "Stanwood, WA"


def test_find_place_is_honest_that_it_cannot_book(ctx, maps_key, monkeypatch):
    """There is no consumer reservation API. Say so rather than pretending."""
    import httpx
    from app.handlers.maps import _find_place

    class R:
        status_code = 200
        def json(self):
            return {"status": "OK", "results": [
                {"name": "Il Granaio", "rating": 4.5, "price_level": 2,
                 "formatted_address": "Stanwood, WA"},
            ]}

    monkeypatch.setattr(httpx, "Client", _client(R()))
    out = _find_place({"query": "italian"}, ctx)

    assert "Il Granaio" in out and "4.5 stars" in out
    assert "can't book a table" in out.lower()


# ── Tailscale ────────────────────────────────────────────────────────────────
def _device(name, minutes_ago, expires_days=180):
    now = datetime.now(ZoneInfo("UTC"))
    return {
        "hostname": name,
        "addresses": ["100.64.0.1"],
        "os": "linux",
        "lastSeen": (now - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
        "expires": (now + timedelta(days=expires_days)).isoformat().replace("+00:00", "Z"),
    }


def test_tailscale_summarizes_rather_than_listing(ctx, monkeypatch):
    from app.handlers import tailscale as T

    monkeypatch.setattr(settings, "tailscale_api_key", "k")
    monkeypatch.setattr(settings, "tailscale_tailnet", "me@example.com")
    monkeypatch.setattr(T, "_fetch_devices", lambda: [
        _device("rpi-01", 1), _device("rpi-02", 1), _device("laptop", 1),
    ])

    assert T._tailscale_status({}, ctx) == "All 3 devices are on the tailnet."


def test_tailscale_leads_with_what_is_wrong(ctx, monkeypatch):
    from app.handlers import tailscale as T

    monkeypatch.setattr(settings, "tailscale_api_key", "k")
    monkeypatch.setattr(settings, "tailscale_tailnet", "me@example.com")
    monkeypatch.setattr(T, "_fetch_devices", lambda: [
        _device("rpi-01", 1), _device("rpi-02", 999), _device("laptop", 1),
    ])

    out = T._tailscale_status({}, ctx)
    assert "rpi-02 is off the tailnet" in out
    assert "other 2 are up" in out


def test_tailscale_warns_before_a_key_expires(ctx, monkeypatch):
    """The silent killer: the node drops off and you find out when something
    breaks. Warn a week out."""
    from app.handlers import tailscale as T

    monkeypatch.setattr(settings, "tailscale_api_key", "k")
    monkeypatch.setattr(settings, "tailscale_tailnet", "me@example.com")
    monkeypatch.setattr(T, "_fetch_devices",
                        lambda: [_device("rpi-01", 1, expires_days=3)])

    out = T._tailscale_status({}, ctx)
    assert "key expires in 3 days" in out


# ── Watches: she acts while you're not thinking about her ────────────────────
def test_watch_only_polls_read_only_tools(ctx):
    """A watch runs unattended. Nothing unattended should send mail, book a
    meeting, or spend money."""
    from app.handlers.watches import WATCHABLE, _create_watch

    for forbidden in ("send_email", "create_event", "place_stock_order", "add_task"):
        assert forbidden not in WATCHABLE

    out = _create_watch({"tool": "send_email", "condition": "x", "opening": "y"}, ctx)
    assert "can't watch" in out.lower()


def test_watch_demands_an_opening_line(ctx):
    from app.handlers.watches import _create_watch

    out = _create_watch({"tool": "get_node_status", "condition": "rpi-02 down"}, ctx)
    assert "what should i say" in out.lower()


def test_watch_fires_and_calls(db, monkeypatch, owner_phone):
    from app.handlers import watches as W

    monkeypatch.setattr(W, "_fired", lambda cond, obs: True)
    w = Watch(tool="get_node_status", tool_args="{}",
              condition="a node is down", opening="It's JARVIS — rpi-02 is down.",
              every_minutes=5, status="active")
    db.add(w); db.commit(); db.refresh(w)

    result = W.check_watch(db, w)

    assert "fired" in result
    call = db.query(OutboundCall).one()
    assert call.kind == "alert"
    assert "rpi-02 is down" in call.opening
    assert w.status == "done", "a one-shot watch must not nag"


def test_a_one_shot_watch_does_not_nag(db, monkeypatch, owner_phone):
    """A watch that calls you every five minutes is worse than no watch — you'll
    turn the whole feature off."""
    from app.handlers import watches as W

    monkeypatch.setattr(W, "_fired", lambda c, o: True)
    w = Watch(tool="get_node_status", condition="down", opening="hi",
              every_minutes=5, recurring=False, status="active")
    db.add(w); db.commit(); db.refresh(w)

    W.check_watch(db, w)
    assert db.query(OutboundCall).count() == 1

    W.check_watch(db, w)                      # condition still true
    assert db.query(OutboundCall).count() == 1, "it nagged"


def test_recurring_watch_is_rate_limited(db, monkeypatch, owner_phone):
    from app.handlers import watches as W

    monkeypatch.setattr(settings, "watch_min_interval_minutes", 60)
    monkeypatch.setattr(W, "_fired", lambda c, o: True)
    w = Watch(tool="get_node_status", condition="down", opening="hi",
              every_minutes=5, recurring=True, status="active")
    db.add(w); db.commit(); db.refresh(w)

    W.check_watch(db, w)
    assert db.query(OutboundCall).count() == 1

    assert W.check_watch(db, w) == "fired but rate-limited"
    assert db.query(OutboundCall).count() == 1


def test_the_judge_fails_closed(db, monkeypatch):
    """A watch that rings you because the judge broke is far worse than one that
    stays quiet."""
    from app.handlers import watches as W

    def boom(*a, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.llm.create_message", boom)
    assert W._fired("anything", "anything") is False


# ── whoami: the expanded profile ─────────────────────────────────────────────
def test_whoami_knows_the_boat_and_the_plate(ctx, monkeypatch):
    """If it never changes and you've ever had to go look it up, JARVIS should
    just know it."""
    from app.handlers.contacts import _whoami

    monkeypatch.setattr(settings, "owner_boat",
                        "Serenity, hull WN1234AB, Skyline Marina, Anacortes")
    monkeypatch.setattr(settings, "owner_vehicle", "2021 F-150, plate ABC1234")
    monkeypatch.setattr(settings, "owner_home_address", "Stanwood, WA")

    out = _whoami({}, ctx)
    assert "Serenity" in out and "WN1234AB" in out
    assert "ABC1234" in out
    assert "Stanwood" in out


# ── Portability ──────────────────────────────────────────────────────────────
def test_no_glibc_only_strftime_anywhere():
    """REGRESSION: '%-I' and '%-d' are glibc extensions. They work on Linux and
    macOS and raise ValueError on WINDOWS.

    Production runs on Linux, so this never broke Fly — it broke the test suite on
    the dev machine, which is arguably worse: a bug that only appears where you
    develop costs you time on every single run.

    Use app.timefmt instead.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).parent.parent / "app"
    offenders = []
    for f in root.rglob("*.py"):
        if f.name == "timefmt.py":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"%-[IdmHMS]", line):
                offenders.append(f"{f.relative_to(root)}:{i}")
    assert not offenders, (
        "glibc-only strftime found (crashes on Windows) — use app.timefmt:\n  "
        + "\n  ".join(offenders)
    )


def test_clock_formats_the_way_a_person_says_a_time():
    """'oh seven fifteen' is not how anyone says 7:15."""
    from app.timefmt import clock, day, daytime

    dt = datetime(2026, 7, 14, 7, 15)
    assert clock(dt) == "7:15 AM"           # not "07:15"
    assert clock(dt, ampm=False) == "7:15"
    assert clock(datetime(2026, 7, 14, 0, 5)) == "12:05 AM"
    assert clock(datetime(2026, 7, 14, 12, 0)) == "12:00 PM"
    assert clock(datetime(2026, 7, 14, 13, 30)) == "1:30 PM"
    assert day(dt) == "Tue Jul 14"          # not "Tue Jul 04"
    assert daytime(dt) == "Tue Jul 14 at 7:15 AM"
