"""Regression tests for the 2026-07-17 audit fixes (H1, H3, H4, H5)."""
import os

import pytest


# ── H1: SPA path-traversal containment ───────────────────────────────────────
def test_safe_static_file_blocks_traversal(tmp_path):
    from app.main import safe_static_file

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html></html>")
    # A secret sibling OUTSIDE the static dir — the thing a traversal targets.
    secret = tmp_path / "config.py"
    secret.write_text("SECRET = 'do not serve me'")

    root = str(static)
    # Legit file inside the static dir resolves.
    assert safe_static_file(root, "index.html") == os.path.realpath(str(static / "index.html"))
    # Escapes are refused, however they're spelled.
    assert safe_static_file(root, "../config.py") is None
    assert safe_static_file(root, "../../config.py") is None
    assert safe_static_file(root, "../") is None
    # Empty path -> None (caller serves the SPA index).
    assert safe_static_file(root, "") is None
    # Non-existent file inside the dir -> None (not an error).
    assert safe_static_file(root, "nope.js") is None


# ── H4: voice_enabled is an honored kill switch ──────────────────────────────
def test_voice_disabled_hangs_up(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "voice_enabled", False)
    r = client.post("/api/voice/inbound",
                    data={"From": "+15551230000", "CallSid": "CA_OFF"})
    assert r.status_code == 200
    assert "<Hangup" in r.text
    assert "<Gather" not in r.text


def test_voice_enabled_by_default_greets(client, monkeypatch):
    # The default (no override) must still connect — the kill switch is opt-out.
    from fakes import install_llm, say
    install_llm(monkeypatch, say("hi"))
    r = client.post("/api/voice/inbound",
                    data={"From": "+15551230000", "CallSid": "CA_ON"})
    assert "<Gather" in r.text


# ── H3: unanswered outbound calls don't get stuck 'ringing' ──────────────────
@pytest.mark.parametrize("call_status,expected", [
    ("no-answer", "no_answer"),
    ("busy", "no_answer"),
    ("failed", "failed"),
    ("canceled", "failed"),
])
def test_unanswered_outbound_call_leaves_ringing(client, db, call_status, expected):
    from app.channels import outbound_voice as ov

    row = ov.schedule_call(db, opening="Test call.", kind="callback",
                           context="ctx", to_number="+15551230000")
    row.status = "ringing"
    row.call_sid = f"CA_{call_status}"
    db.commit()

    r = client.post("/api/voice/status",
                    data={"From": "+15550001111", "To": "+15551230000",
                          "CallSid": f"CA_{call_status}", "CallStatus": call_status})
    assert r.status_code == 200
    db.refresh(row)
    assert row.status == expected, f"{call_status} left row as {row.status}, not {expected}"


def test_answered_outbound_call_closes_done(client, db):
    from app.channels import outbound_voice as ov

    row = ov.schedule_call(db, opening="Test call.", kind="callback",
                           context="ctx", to_number="+15551230000")
    row.status = "answered"
    row.call_sid = "CA_DONE"
    db.commit()

    r = client.post("/api/voice/status",
                    data={"From": "+15550001111", "To": "+15551230000",
                          "CallSid": "CA_DONE", "CallStatus": "completed"})
    assert r.status_code == 200
    db.refresh(row)
    assert row.status == "done"


# ── H5: recall output carries the episode id forget_episode needs ────────────
def test_recall_output_includes_episode_id(db):
    from app.handlers.episodes import _format_episode

    class _Q:
        speaker = "owner"
        kind = "quote"
        quote = "hello there"

    class _Ep:
        id = 42
        title = "The boat call"
        summary = "Talked about the boat."
        topics = '["boats"]'
        quotes = [_Q()]

        class _D:
            @staticmethod
            def isoformat():
                return "2026-07-16"
        occurred_on = _D()

    out = _format_episode(_Ep())
    assert "#42" in out, "episode id must appear so forget_episode can target it"
