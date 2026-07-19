"""Runtime settings overlay (health TDD §7, roadmap R2).

get_effective returns the DB override if present, else the env/Settings default,
without mutating the settings singleton. Safety-critical keys need an explicit
confirm and are audited. Nothing off the allow-list can be read or written — the
overlay is never a secret surface. And every runtime reader that was switched
must actually honor an override.
"""

import pytest

from app.config import settings
from app.channels import outbound_voice as ov
from app.handlers.base import Context
from app.models import ActionAudit, RuntimeSetting
from app.runtime_settings import (
    ALLOWED_KEYS, get_all_effective, get_effective, set_effective,
)


def _ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t")


# ── get_effective: override vs default (TDD #21) ─────────────────────────────

def test_get_effective_returns_default_when_no_override(db, monkeypatch):
    monkeypatch.setattr(settings, "briefing_hour", 6)
    assert get_effective(db, "briefing_hour") == 6


def test_get_effective_returns_override_when_present(db, monkeypatch):
    monkeypatch.setattr(settings, "briefing_hour", 6)
    set_effective(db, "briefing_hour", 9)
    assert get_effective(db, "briefing_hour") == 9  # override wins over the default


def test_get_effective_coerces_bool(db):
    set_effective(db, "briefing_enabled", True)
    v = get_effective(db, "briefing_enabled")
    assert v is True and isinstance(v, bool)


def test_get_effective_does_not_mutate_settings_singleton(db, monkeypatch):
    monkeypatch.setattr(settings, "briefing_hour", 6)
    set_effective(db, "briefing_hour", 9)
    get_effective(db, "briefing_hour")
    assert settings.briefing_hour == 6  # the @lru_cache singleton is untouched


def test_corrupt_override_row_falls_back_to_default(db, monkeypatch):
    monkeypatch.setattr(settings, "briefing_minute", 30)
    db.add(RuntimeSetting(key="briefing_minute", value="not-an-int"))
    db.commit()
    assert get_effective(db, "briefing_minute") == 30  # never breaks the reader


# ── the allow-list is the boundary: never a secret ───────────────────────────

def test_get_effective_rejects_non_allowlist_key(db):
    with pytest.raises(KeyError):
        get_effective(db, "briefing_hour_typo")


def test_secrets_are_not_reachable(db):
    # A real secret attribute exists on settings, but is NOT on the allow-list,
    # so the overlay refuses to read or write it.
    assert "jwt_secret" not in ALLOWED_KEYS
    assert "anthropic_api_key" not in ALLOWED_KEYS
    with pytest.raises(KeyError):
        get_effective(db, "jwt_secret")
    with pytest.raises(ValueError):
        set_effective(db, "anthropic_api_key", "leak", confirm=True)


# ── set_effective: validation, bounds, audit ─────────────────────────────────

def test_set_effective_writes_and_audits(db):
    set_effective(db, "briefing_hour", 8, actor="tester")
    rows = db.query(ActionAudit).filter_by(tool="set_runtime_setting").all()
    assert len(rows) == 1
    assert rows[0].actor == "tester"
    assert "briefing_hour" in rows[0].result


def test_set_effective_out_of_range_rejected(db):
    with pytest.raises(ValueError):
        set_effective(db, "briefing_hour", 99)          # hour > 23
    with pytest.raises(ValueError):
        set_effective(db, "max_outbound_calls_per_hour", 50, confirm=True)  # cap max 20
    with pytest.raises(ValueError):
        set_effective(db, "max_outbound_calls_per_hour", 0, confirm=True)   # cap min 1


def test_set_effective_unknown_key_rejected(db):
    with pytest.raises(ValueError):
        set_effective(db, "not_a_setting", 1)


# ── safety-critical keys: confirm required + audited (TDD #25) ───────────────

def test_safety_critical_needs_confirm(db):
    with pytest.raises(PermissionError):
        set_effective(db, "outbound_calls_enabled", True)          # no confirm -> refused
    # with confirm it applies AND is audited
    set_effective(db, "outbound_calls_enabled", True, confirm=True, actor="tester")
    assert get_effective(db, "outbound_calls_enabled") is True
    aud = db.query(ActionAudit).filter_by(tool="set_runtime_setting").all()
    assert aud and aud[-1].status == "confirmed"


# ── get_all_effective: value + source for the Admin surface ──────────────────

def test_get_all_effective_reports_source(db, monkeypatch):
    monkeypatch.setattr(settings, "briefing_hour", 6)
    set_effective(db, "briefing_hour", 9)
    allv = get_all_effective(db)
    assert allv["briefing_hour"] == {"value": 9, "source": "override",
                                     "type": "int", "safety_critical": False}
    assert allv["briefing_minute"]["source"] == "default"
    assert allv["outbound_calls_enabled"]["safety_critical"] is True


# ── the readers actually honor the override (the whole point) ─────────────────

def test_in_quiet_hours_honors_override(db):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(settings.calendar_timezone)
    # Override a same-day window 13:00–14:00; 13:30 is inside, 15:00 is outside.
    set_effective(db, "quiet_hours_start", 13)
    set_effective(db, "quiet_hours_start_minute", 0)
    set_effective(db, "quiet_hours_end", 14)
    set_effective(db, "quiet_hours_end_minute", 0)
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=13, minute=30)) is True
    assert ov.in_quiet_hours(db, datetime.now(tz).replace(hour=15, minute=0)) is False


# ── the API surface ──────────────────────────────────────────────────────────

def test_settings_api_requires_auth(client):
    assert client.get("/api/settings").status_code == 401


def test_settings_api_get_and_put(client, auth_headers):
    r = client.get("/api/settings", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()["settings"]
    assert body["briefing_hour"]["source"] == "default"

    r = client.put("/api/settings/briefing_hour", json={"value": 9}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["value"] == 9

    r = client.get("/api/settings", headers=auth_headers)
    assert r.json()["settings"]["briefing_hour"] == {
        "value": 9, "source": "override", "type": "int", "safety_critical": False}


def test_settings_api_safety_critical_needs_confirm(client, auth_headers):
    # Without confirm -> 403
    r = client.put("/api/settings/outbound_calls_enabled", json={"value": True},
                   headers=auth_headers)
    assert r.status_code == 403
    # With confirm -> 200
    r = client.put("/api/settings/outbound_calls_enabled",
                   json={"value": True, "confirm": True}, headers=auth_headers)
    assert r.status_code == 200


def test_settings_api_rejects_bad_values(client, auth_headers):
    assert client.put("/api/settings/briefing_hour", json={"value": 99},
                      headers=auth_headers).status_code == 422
    assert client.put("/api/settings/not_a_setting", json={"value": 1},
                      headers=auth_headers).status_code == 404
