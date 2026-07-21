"""Tests — TDD #11: Current date/time awareness and stale-data sanity checks.

Test table from docs/TDD-datetime-awareness.md §5, plus edge cases for
resolve_relative_date patterns.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tests.fakes import install_llm, say


# ── helpers ───────────────────────────────────────────────────────────────────

def _ctx(db):
    from app.handlers.base import Context
    return Context(db=db, channel="test", actor="test", thread_key="test")


# ── §4.1: get_current_datetime ────────────────────────────────────────────────


def test_get_current_datetime_returns_both_timezones_explicitly_labeled(db):
    from app.handlers.datetime_tools import _get_current_datetime

    result = json.loads(_get_current_datetime({}, _ctx(db)))

    # Both times must be present and explicitly labelled — no bare timestamp.
    assert "jarvis_time" in result
    assert "user_time" in result
    assert "jarvis_tz" in result
    assert "user_tz" in result
    # Both must be tz-aware ISO strings (contain 'T' and a UTC offset after pos 10).
    for key in ("jarvis_time", "user_time"):
        val = result[key]
        assert "T" in val
        tail = val[10:]
        assert "+" in tail or "-" in tail, f"{key} has no UTC offset: {val!r}"


def test_jarvis_timezone_is_always_america_los_angeles(db):
    from app.handlers.datetime_tools import _get_current_datetime

    result = json.loads(_get_current_datetime({}, _ctx(db)))
    assert result["jarvis_tz"] == "America/Los_Angeles"


def test_jarvis_tz_follows_calendar_timezone(db, monkeypatch):
    """get_current_datetime.jarvis_tz tracks settings.calendar_timezone — one source of truth.

    Proves there is no independent jarvis_tz value that could silently diverge from
    the timezone the scheduler, quiet-hours logic, and rate-limit windows use.
    If this test passes by coincidence of matching defaults rather than by actual
    linkage, changing calendar_timezone here would NOT change jarvis_tz in the result.
    """
    import app.config as config_module
    from app.handlers.datetime_tools import _get_current_datetime

    monkeypatch.setattr(config_module.settings, "calendar_timezone", "America/New_York")
    result = json.loads(_get_current_datetime({}, _ctx(db)))

    assert result["jarvis_tz"] == "America/New_York", (
        "jarvis_tz did not follow calendar_timezone — two independent sources of truth"
    )
    # The reported time should reflect New York, not Pacific
    assert "-04:" in result["jarvis_time"] or "-05:" in result["jarvis_time"], (
        "jarvis_time offset did not shift to Eastern time"
    )


def test_dst_transition_is_handled_by_tz_database_not_fixed_offset(db):
    """UTC offset flips at DST boundaries without any code change.

    The tz database knows when PDT↔PST switches. A hardcoded offset
    (e.g. UTC-8) would be wrong for half the year and would only be
    caught when tested across both sides of the boundary — which is
    exactly what this test does.
    """
    from zoneinfo import ZoneInfo

    la = ZoneInfo("America/Los_Angeles")
    summer = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).astimezone(la)
    winter = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).astimezone(la)

    assert summer.strftime("%z") == "-0700", "PDT should be UTC-7"
    assert winter.strftime("%z") == "-0800", "PST should be UTC-8"
    assert summer.strftime("%z") != winter.strftime("%z"), "offset must flip at DST"


def test_user_tz_source_reflects_actual_basis_for_the_guess(db):
    """With no explicit signal, source must be 'default' — not a stronger claim."""
    from app.handlers.datetime_tools import _get_current_datetime

    result = json.loads(_get_current_datetime({}, _ctx(db)))
    assert result["user_tz_source"] == "default"


def test_user_tz_defaults_to_pacific_when_no_signal_exists(db):
    """No crash, no None — falls back to Pacific when nothing is known."""
    from app.handlers.datetime_tools import _get_current_datetime

    result = json.loads(_get_current_datetime({}, _ctx(db)))
    assert result["user_tz"] == "America/Los_Angeles"
    assert result["user_time"] is not None


def test_datetime_call_has_no_external_network_dependency(db, monkeypatch):
    """Even with all network blocked, get_current_datetime returns a valid result.

    It is a local clock read — it must never fail because the network is down
    or the NTP server is unreachable.
    """
    import socket

    def _blocked(*a, **kw):
        raise OSError("network blocked by test")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    from app.handlers.datetime_tools import _get_current_datetime

    result = json.loads(_get_current_datetime({}, _ctx(db)))
    assert "jarvis_time" in result
    assert "utc" in result


# ── §4.2: mandatory-call enforcement ─────────────────────────────────────────


def test_date_bearing_subagents_call_datetime_as_forced_first_turn(db, monkeypatch):
    """Date-bearing agents receive datetime context before the LLM loop — structural.

    The researcher has web_search in its roster (a DATE_BEARING_TOOL). Its initial
    task message must contain the datetime JSON before any LLM call is made. This
    is not a prompt instruction — the context is injected in run_agent() itself,
    so it cannot be skipped by the model.
    """
    from app.agents import DEFAULT_AGENTS, run_agent

    llm = say("Here is my research.")
    install_llm(monkeypatch, llm)

    researcher = DEFAULT_AGENTS["researcher"]  # has web_search — date-bearing
    run_agent(db, researcher, "What is the news today?", _ctx(db))

    assert llm.calls, "LLM was never called"
    first_content = llm.calls[0].messages[0]["content"]
    assert "jarvis_time" in first_content, (
        "datetime context was not injected into the first message for a date-bearing agent"
    )


def test_datetime_call_is_available_in_every_subagent_roster(db, monkeypatch):
    """get_current_datetime is in the tool list for every sub-agent — not top-level-only.

    It's injected structurally in run_agent(), so an admin cannot accidentally remove
    it by editing an agent's roster in the DB.
    """
    from app.agents import DEFAULT_AGENTS, run_agent

    for agent_name, agent in DEFAULT_AGENTS.items():
        llm = say("Done.")
        install_llm(monkeypatch, llm)
        run_agent(db, agent, "Do nothing.", _ctx(db))

        assert llm.calls, f"Agent {agent_name!r}: LLM was never called"
        tool_names = {t["name"] for t in (llm.calls[0].tools or [])}
        assert "get_current_datetime" in tool_names, (
            f"Agent {agent_name!r} is missing get_current_datetime in its tool list"
        )


# ── §4.3: flag_stale_dates ────────────────────────────────────────────────────


def test_stale_result_is_flagged_not_silently_dropped(db):
    """A past-dated item is annotated with [stale: ...] — not removed from output."""
    from app.handlers.datetime_tools import flag_stale_dates

    ref = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    text = "The event is on 2025-03-15 and should be attended."
    annotated, flags = flag_stale_dates(text, ref)

    assert "2025-03-15" in annotated, "stale date was silently dropped"
    assert "[stale:" in annotated
    assert flags


def test_stale_result_is_flagged_not_silently_passed_through(db):
    """A past-dated item does NOT reach the caller as if it were current."""
    from app.handlers.datetime_tools import flag_stale_dates

    ref = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    text = "Flight available on 2023-11-01."
    annotated, flags = flag_stale_dates(text, ref)

    # Present but annotated — not clean pass-through.
    assert "2023-11-01" in annotated
    assert "[stale:" in annotated
    assert annotated != text, "stale date passed through without annotation"


def test_future_dates_are_left_unflagged(db):
    """SCOPE (audit M7): only PAST dates are flagged. The never-wired
    'outside the request window' check was removed, so a future date — however
    far out — is left untouched rather than annotated."""
    from app.handlers.datetime_tools import flag_stale_dates

    ref = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    text = "The sale runs through 2026-08-15."  # future
    annotated, flags = flag_stale_dates(text, ref)

    assert annotated == text          # unchanged
    assert flags == []
    assert "[outside window:" not in annotated


def test_date_extraction_handles_relative_phrasing_in_source_content(db):
    """Relative phrases ('next Tuesday') in fetched content are left untouched.

    flag_stale_dates only processes parseable absolute dates. Relative phrasing
    has no year anchor and must not be resolved against JARVIS's 'now' — a page
    that says 'next Tuesday' means its author's next Tuesday, not ours. The safe
    behaviour is to pass it through unchanged.
    """
    from app.handlers.datetime_tools import flag_stale_dates

    ref = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    text = "The meeting is next Tuesday at 10am. See you soon!"
    annotated, flags = flag_stale_dates(text, ref)

    assert annotated == text, "relative phrase was incorrectly annotated"
    assert not flags


def test_flight_offer_expiry_is_not_double_handled_by_this_pipeline(db):
    """flag_stale_dates leaves Duffel offer IDs untouched — no interference.

    Flight offer expiry is Duffel's native responsibility (TDD §4.3 exception).
    An offer_id is not a date string; flag_stale_dates must not raise or annotate it.
    """
    from app.handlers.datetime_tools import flag_stale_dates

    ref = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    text = "offer_id: off_0001234567890abcdef, expires natively via the Duffel API."
    annotated, flags = flag_stale_dates(text, ref)

    assert annotated == text
    assert not flags


# ── §4.4: resolve_relative_date ───────────────────────────────────────────────


def test_wrong_year_produces_a_clarifying_question_not_a_past_search():
    """Regression for incident 2 (TDD §0): 'August 4th' with ref 2026-07-13 → 2026-08-04.

    Before this fix JARVIS searched four consecutive times for 2025-08-04 while the
    user repeated 'August 4th'. The future-bias invariant prevents that: no explicit
    year means nearest future occurrence, never past.
    """
    from app.handlers.datetime_tools import resolve_relative_date

    ref = datetime(2026, 7, 13, tzinfo=timezone.utc)
    result = resolve_relative_date("August 4th", ref)

    assert result is not None
    assert result.year == 2026, f"Expected 2026, got {result.year} — wrong-year regression"
    assert result.month == 8
    assert result.day == 4


def test_flight_search_with_ungrounded_date_resolves_to_future_year():
    """Future-bias invariant: this year if still future, next year if already past."""
    from app.handlers.datetime_tools import resolve_relative_date

    # Case 1: August 4th, reference July 13 2026 → still in the future → 2026
    ref_before = datetime(2026, 7, 13, tzinfo=timezone.utc)
    result_before = resolve_relative_date("August 4th", ref_before)
    assert result_before is not None
    assert result_before.year == 2026
    assert result_before.month == 8
    assert result_before.day == 4

    # Case 2: August 4th, reference September 1 2026 → already past → 2027
    ref_after = datetime(2026, 9, 1, tzinfo=timezone.utc)
    result_after = resolve_relative_date("August 4th", ref_after)
    assert result_after is not None
    assert result_after.year == 2027
    assert result_after.month == 8
    assert result_after.day == 4


# ── Additional resolve_relative_date edge cases ───────────────────────────────


def test_resolve_explicit_year_is_honoured():
    """'August 4th 2026' with any reference → 2026-08-04 exactly (user stated it)."""
    from app.handlers.datetime_tools import resolve_relative_date

    ref = datetime(2025, 1, 1, tzinfo=timezone.utc)
    result = resolve_relative_date("August 4th 2026", ref)

    assert result is not None
    assert result.year == 2026
    assert result.month == 8
    assert result.day == 4


def test_resolve_today_tomorrow_yesterday():
    from app.handlers.datetime_tools import resolve_relative_date

    ref = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)
    assert resolve_relative_date("today", ref).day == 14
    assert resolve_relative_date("tomorrow", ref).day == 15
    assert resolve_relative_date("yesterday", ref).day == 13


def test_resolve_next_weekday():
    """'next tuesday' from Monday July 13 2026 → July 14 (1 day ahead)."""
    from app.handlers.datetime_tools import resolve_relative_date

    ref = datetime(2026, 7, 13, tzinfo=timezone.utc)  # Monday
    result = resolve_relative_date("next tuesday", ref)
    assert result is not None
    assert result.month == 7
    assert result.day == 14


def test_resolve_next_weekday_same_day_rolls_forward():
    """'next monday' from Monday → next Monday, not today."""
    from app.handlers.datetime_tools import resolve_relative_date

    ref = datetime(2026, 7, 13, tzinfo=timezone.utc)  # Monday
    result = resolve_relative_date("next monday", ref)
    assert result is not None
    assert result.day == 20  # 7 days ahead


def test_resolve_in_n_days_weeks():
    from app.handlers.datetime_tools import resolve_relative_date

    ref = datetime(2026, 7, 14, tzinfo=timezone.utc)
    assert resolve_relative_date("in 3 days", ref).day == 17
    assert resolve_relative_date("in 2 weeks", ref).day == 28


def test_resolve_weekend():
    """'this weekend' from Monday → upcoming Saturday."""
    from app.handlers.datetime_tools import resolve_relative_date

    ref = datetime(2026, 7, 13, tzinfo=timezone.utc)  # Monday
    result = resolve_relative_date("this weekend", ref)
    assert result is not None
    assert result.weekday() == 5  # Saturday


def test_resolve_returns_none_for_garbage():
    """Unrecognisable input → None. Callers must handle None explicitly."""
    from app.handlers.datetime_tools import resolve_relative_date

    ref = datetime(2026, 7, 14, tzinfo=timezone.utc)
    assert resolve_relative_date("flurbazorp", ref) is None
    assert resolve_relative_date("", ref) is None


def test_resolve_preserves_timezone():
    """The returned datetime carries the same tzinfo as reference_dt."""
    from app.handlers.datetime_tools import resolve_relative_date
    from zoneinfo import ZoneInfo

    la = ZoneInfo("America/Los_Angeles")
    ref = datetime(2026, 7, 14, 9, 0, tzinfo=la)
    result = resolve_relative_date("tomorrow", ref)
    assert result is not None
    assert result.tzinfo == la


# ── Wiring: _resolve_flight_date in travel.py (§4.4 call site) ───────────────


def test_search_flights_rejects_past_date(db):
    """_resolve_flight_date refuses a past ISO date — the production regression.

    This is the direct guard against the 2026-07-13 incident: JARVIS searched
    for 2025-08-04 four consecutive times. Now search_flights refuses the date
    before the Duffel call is even made.
    """
    from app.handlers.travel import _resolve_flight_date

    ctx = _ctx(db)
    result = _resolve_flight_date("2025-08-04", ctx)

    assert result.startswith("["), f"Expected refusal, got: {result!r}"
    assert "past" in result.lower()
    assert "2025-08-04" in result


def test_search_flights_routes_dates_through_the_resolver(db):
    """A relative date expression is accepted and resolved to a future ISO date.

    When the LLM passes 'August 4th' directly instead of converting to ISO,
    _resolve_flight_date falls back to resolve_relative_date and returns a valid
    ISO date in the future — never 'not recognised' and never past.
    """
    import datetime as _dt
    from app.handlers.travel import _resolve_flight_date

    ctx = _ctx(db)
    result = _resolve_flight_date("August 4th", ctx)

    assert not result.startswith("["), f"Expected ISO date, got refusal: {result!r}"
    parsed = _dt.date.fromisoformat(result)
    assert parsed >= _dt.date.today(), f"Resolved to past date: {parsed}"
    assert parsed.month == 8
    assert parsed.day == 4


def test_resolve_flight_date_unrecognised_input_returns_error(db):
    from app.handlers.travel import _resolve_flight_date

    ctx = _ctx(db)
    result = _resolve_flight_date("flurbazorp", ctx)

    assert result.startswith("[")
    assert "not recognised" in result


# ── Wiring: flag_stale_dates in agents.py run_agent (§4.3 call site) ─────────


def test_date_bearing_agent_flags_stale_dates_in_output(db, monkeypatch):
    """flag_stale_dates is applied to date-bearing agent output before returning.

    The researcher has web_search (date-bearing). If it returns content with a
    stale date, run_agent annotates it with [stale: ...] before the string
    reaches the orchestrator.
    """
    from tests.fakes import ScriptedLLM, response, text_block
    from app.agents import DEFAULT_AGENTS, run_agent

    stale_text = "Breaking news from 2020-03-15: things have changed significantly."
    llm = ScriptedLLM(response([text_block(stale_text)]))
    install_llm(monkeypatch, llm)

    researcher = DEFAULT_AGENTS["researcher"]
    result = run_agent(db, researcher, "What happened recently?", _ctx(db))

    assert "[stale:" in result, f"Stale date was not flagged in output: {result!r}"
    assert "2020-03-15" in result  # still present, not dropped


def test_compose_briefing_injects_datetime_context_before_llm_call(db, monkeypatch):
    """compose_briefing prepends get_current_datetime context before the LLM sees the data.

    This is the §4.2 forced-first-call pattern applied to the briefing path.
    Without it, the LLM composing the spoken brief infers 'now' from training
    data — which is what produced the wrong-time briefing content (the
    scheduler's clock was correct; the LLM's internal reasoning was not).

    The test verifies structural injection, not LLM compliance: we confirm that
    the message sent to create_message contains the datetime JSON, proving the
    grounding cannot be skipped regardless of what the model decides to do with it.
    """
    from app.briefing import compose_briefing

    llm = say("Good morning. Today looks busy.")
    install_llm(monkeypatch, llm)

    compose_briefing(db)

    assert llm.calls, "LLM was never called by compose_briefing"
    msg_content = llm.calls[0].messages[0]["content"]
    assert "jarvis_time" in msg_content, (
        "compose_briefing did not inject current datetime context before the LLM call"
    )
    assert "Current date/time" in msg_content


def test_non_date_bearing_agent_output_is_not_processed(db, monkeypatch):
    """flag_stale_dates is NOT applied to agents without date-bearing tools.

    The finance agent (get_stock_price, get_portfolio) fetches live market data
    but has no DATE_BEARING_TOOLS in its roster. Its output passes through
    unchanged — no false annotations on price quotes that happen to contain year
    numbers.
    """
    from tests.fakes import ScriptedLLM, response, text_block
    from app.agents import DEFAULT_AGENTS, run_agent

    output = "AAPL closed at $215.40 on 2024-12-31 — a good year."
    llm = ScriptedLLM(response([text_block(output)]))
    install_llm(monkeypatch, llm)

    finance = DEFAULT_AGENTS["finance"]
    result = run_agent(db, finance, "How did AAPL do last year?", _ctx(db))

    assert "[stale:" not in result, "finance agent output was incorrectly annotated"


# ── §4.1: _resolve_user_tz — DST-by-location ─────────────────────────────────


def test_arizona_location_ping_gives_no_dst_timezone(db):
    """A fresh Scottsdale AZ LocationPing → 'America/Phoenix' (UTC-7, no DST).

    Matt's golf-in-Arizona-in-December scenario: the phone reports Scottsdale
    coords. In December, Pacific is PST (UTC-8). Phoenix is UTC-7 year-round
    (Arizona does not observe DST). Without location-aware tz resolution,
    get_current_datetime would report the user's time as one hour early.

    This test inserts a fresh ping at the exact Scottsdale Golf Club coordinates
    and asserts that _resolve_user_tz returns 'America/Phoenix' from
    'location_report' — NOT the default Pacific timezone.
    """
    pytest.importorskip("timezonefinder", reason="timezonefinder not installed")

    from app.handlers.location import record_ping
    from app.handlers.datetime_tools import _resolve_user_tz

    # Scottsdale, AZ (TPC Scottsdale — Stadium Course: 33.6261° N, 111.8923° W)
    record_ping(db, lat=33.6261, lon=-111.8923, accuracy_m=10.0,
                source="phone", label="Scottsdale")

    ctx = _ctx(db)
    tz_name, source = _resolve_user_tz(ctx)

    assert tz_name == "America/Phoenix", (
        f"Expected 'America/Phoenix' for Scottsdale AZ, got {tz_name!r}"
    )
    assert source == "location_report", (
        f"Expected source='location_report', got {source!r}"
    )


def test_arizona_timezone_has_no_dst_offset_shift(db):
    """America/Phoenix offset is UTC-7 in both July and December — no DST.

    Regression guard: if someone accidentally maps Scottsdale to a DST-observing
    timezone (e.g. Mountain Time 'America/Denver'), the December offset would be
    UTC-7 which coincidentally matches Phoenix — but the July offset would be
    UTC-6 and this test would catch the mistake.
    """
    from zoneinfo import ZoneInfo

    phoenix = ZoneInfo("America/Phoenix")
    july = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).astimezone(phoenix)
    december = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc).astimezone(phoenix)

    assert july.strftime("%z") == "-0700", "Phoenix July should be UTC-7"
    assert december.strftime("%z") == "-0700", "Phoenix December should be UTC-7 (no DST)"
    assert july.strftime("%z") == december.strftime("%z"), (
        "America/Phoenix offset changed between summer and winter — DST leak"
    )


def test_stale_location_ping_falls_back_to_default(db, monkeypatch):
    """A location ping older than location_max_age_minutes is ignored.

    _resolve_user_tz must fall back to 'default' rather than trust a stale fix —
    the same staleness rule that current_coords() in location.py enforces.
    """
    pytest.importorskip("timezonefinder", reason="timezonefinder not installed")

    import app.config as config_module
    from app.handlers.location import record_ping
    from app.handlers.datetime_tools import _resolve_user_tz

    # Insert an Arizona ping, then make it appear stale by reducing max_age to 0
    record_ping(db, lat=33.6261, lon=-111.8923, accuracy_m=10.0,
                source="phone", label="Scottsdale")
    monkeypatch.setattr(config_module.settings, "location_max_age_minutes", 0)

    ctx = _ctx(db)
    tz_name, source = _resolve_user_tz(ctx)

    assert source == "default", (
        f"Stale ping should fall back to 'default', got source={source!r}"
    )
    # With no fresh ping, must use the configured default (Pacific)
    assert tz_name == "America/Los_Angeles", (
        f"Expected Pacific fallback, got {tz_name!r}"
    )
