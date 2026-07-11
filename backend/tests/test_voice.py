"""Voice channel tests — whitelist, TwiML, async turn loop, and the security
claims the TDD makes. The security tests are the point: an assertion that
something "fails closed" is worthless until it's demonstrated.
"""

import pytest

from app.channels.voice_pipeline import (
    VOICE_AGENTS_PHASE1,
    VOICE_TOOLS_PHASE1,
    get_turn,
    is_allowed,
    open_turn,
    run_turn,
    twiml_gather,
    twiml_hangup,
    twiml_working,
)
from app.handlers.base import Context, build_registry
from app.models import AgentConfig, ContactWhitelist, VoiceTurn
from fakes import install_llm, say


# ── Whitelist ────────────────────────────────────────────────────────────────
def test_whitelist_from_config(db):
    assert is_allowed(db, "+1 555 123 0000") is True     # ALLOWED_NUMBERS
    assert is_allowed(db, "+19998887777") is False


def test_whitelist_from_contacts_table_is_voice_scoped(db):
    # An SMS-channel whitelist row must NOT grant voice access.
    db.add(ContactWhitelist(channel="sms", identifier="+19998887777", label="sms only"))
    db.commit()
    assert is_allowed(db, "+19998887777") is False

    db.add(ContactWhitelist(channel="voice", identifier="+19998887777", label="voice"))
    db.commit()
    assert is_allowed(db, "+1 (999) 888-7777") is True


# ── TwiML ────────────────────────────────────────────────────────────────────
def test_twiml_is_wellformed_and_escaped():
    from xml.dom.minidom import parseString

    # The reply is LLM output and will contain hostnames, quotes, ampersands.
    xml = twiml_gather('rpi-02 is "down" & unreachable', turn=3)
    parseString(xml)                       # raises if malformed
    assert "&amp;" in xml and "&quot;" in xml
    assert 'action="/api/voice/gather?turn=3"' in xml

    parseString(twiml_working("CA1", turn=2, poll=0))
    parseString(twiml_hangup("Goodbye."))


def test_twiml_working_alternates_filler():
    # poll 0 speaks; later polls stay silent so the filler doesn't grate.
    assert "<Say" in twiml_working("CA1", 0, poll=0)
    assert "<Pause" in twiml_working("CA1", 0, poll=1)


# ── Async turn loop ──────────────────────────────────────────────────────────
def test_run_turn_stores_reply(db, monkeypatch):
    install_llm(monkeypatch, say("All nodes are online."))
    open_turn(db, "CA_TEST", 0, "status of the cluster?")
    run_turn(db, "CA_TEST", 0, "+15551230000", "status of the cluster?")

    row = get_turn(db, "CA_TEST", 0)
    assert row.status == "done"
    assert "online" in row.reply


def test_run_turn_records_error_rather_than_raising(db, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("anthropic exploded")

    monkeypatch.setattr("app.orchestrator.create_message", boom)
    open_turn(db, "CA_ERR", 0, "hello")
    run_turn(db, "CA_ERR", 0, "+15551230000", "hello")   # must not raise

    row = get_turn(db, "CA_ERR", 0)
    assert row.status == "error"
    assert "exploded" in row.error


def test_thread_key_is_call_sid_not_number(db, monkeypatch):
    """A PendingConfirmation from one call must not be resolvable by the next.

    This is why voice threads on CallSid rather than the phone number: calls are
    bounded sessions, and a stale gated action left pending must not be executed
    by an unrelated "confirm" on a later call.
    """
    from app.memory import get_or_create_conversation

    install_llm(monkeypatch, say("ok"))
    run_turn(db, "CA_ONE", 0, "+15551230000", "hello")
    run_turn(db, "CA_TWO", 0, "+15551230000", "hello")

    c1 = get_or_create_conversation(db, "voice", "CA_ONE", "")
    c2 = get_or_create_conversation(db, "voice", "CA_TWO", "")
    assert c1.id != c2.id          # separate calls => separate conversations


# ── Confirmation vocabulary (TDD 8.2) ────────────────────────────────────────
@pytest.mark.parametrize("word", ["ok", "okay", "yeah", "yep", "sure"])
def test_voice_rejects_loose_affirmatives(word):
    """'ok'/'yeah' are conversational filler on a phone line. STT will transcribe
    an idle 'yeah...' and it must NOT execute a pending gated action."""
    from app.orchestrator import _vocab

    affirmative, _ = _vocab("voice")
    assert word not in affirmative


@pytest.mark.parametrize("word", ["confirm", "affirmative", "execute", "roger"])
def test_voice_accepts_explicit_affirmatives(word):
    from app.orchestrator import _vocab

    affirmative, _ = _vocab("voice")
    assert word in affirmative


def test_other_channels_keep_loose_vocabulary():
    """The narrowing is voice-specific — SMS/email must be unaffected."""
    from app.orchestrator import _AFFIRMATIVE, _vocab

    assert _vocab("sms")[0] is _AFFIRMATIVE
    assert "ok" in _vocab("sms")[0]
    assert "yeah" in _vocab("email")[0]


# ── Security: what voice can reach (TDD 3.3) ─────────────────────────────────
def test_voice_registry_drops_trading_but_keeps_delegate():
    """delegate MUST survive — the top-level registry is a pure delegator and it
    is voice's only route to any tool. The allowlist's real job is dropping the
    trade tool."""
    reg = build_registry(include_delegate=True, allow=VOICE_TOOLS_PHASE1)
    assert reg.has("delegate")
    assert not reg.has("place_stock_order")


def test_unrestricted_registry_still_has_trading():
    reg = build_registry(include_delegate=True)
    assert reg.has("place_stock_order")


def test_netstatus_tools_registered_in_subagent_registry():
    reg = build_registry()          # sub-agent branch
    assert reg.has("get_node_status")
    assert reg.has("get_service_health")


def test_voice_cannot_delegate_to_unknown_agent(db):
    """An agent not in VOICE_AGENTS_PHASE1 must be unreachable from a phone call."""
    from app.agents import _delegate

    ctx = Context(db=db, channel="voice", actor="+15551230000", thread_key="CA_X")
    out = _delegate({"agent": "nonexistent_specialist", "task": "do a thing"}, ctx)
    assert "isn't available over voice" in out


def test_voice_can_reach_finance_but_finance_cannot_trade(db):
    """finance IS reachable from voice (prices are read-only) — but the trading
    tool is not in its roster and lives on the gated top-level registry, so a
    phone call cannot place an order no matter what it asks for."""
    from app.agents import DEFAULT_AGENTS

    assert "finance" in VOICE_AGENTS_PHASE1
    assert "place_stock_order" not in DEFAULT_AGENTS["finance"].tools
    assert "place_stock_order" not in VOICE_TOOLS_PHASE1


def test_admin_edited_agent_cannot_widen_voice_reach(db):
    """THE load-bearing test.

    build_agents() reads the roster LIVE from the DB. If someone edits the
    netstatus agent via /api/agents to include a write tool, it must become
    UNREACHABLE from voice — not become a new voice capability. Fail closed.
    """
    from app.agents import _delegate

    db.add(AgentConfig(
        name="netstatus",
        description="net",
        system_prompt="net",
        tools='["get_node_status", "place_stock_order"]',   # <- tool NOT in the voice allowlist
        enabled=True,
    ))
    db.commit()

    ctx = Context(db=db, channel="voice", actor="+15551230000", thread_key="CA_Y")
    out = _delegate({"agent": "netstatus", "task": "status"}, ctx)
    assert "isn't available over voice" in out


def test_voice_agent_allowlist_matches_default_rosters():
    """Every agent voice may reach must have tools that are a subset of the tool
    allowlist — otherwise it is permanently unreachable and the config is a lie."""
    from app.agents import DEFAULT_AGENTS

    for name in VOICE_AGENTS_PHASE1:
        agent = DEFAULT_AGENTS[name]
        extra = set(agent.tools) - VOICE_TOOLS_PHASE1
        assert not extra, f"agent {name!r} needs {extra} added to VOICE_TOOLS_PHASE1"


# ── Status tools speak like humans (TDD 7.1) ─────────────────────────────────
def test_node_status_renders_for_speech(db):
    from app.handlers.netstatus import _get_node_status

    ctx = Context(db=db, channel="voice", actor="x", thread_key="t")
    out = _get_node_status({}, ctx)

    assert "3221225472" not in out and "481203" not in out   # no raw bytes/epochs
    assert "(s)" not in out                                   # TTS reads parens aloud
    assert "gigabytes" in out and "days" in out
    assert "OFFLINE" in out


def test_unknown_node_asks_rather_than_guessing(db):
    """The Grok lesson: STT mangles identifiers silently and confidently. An
    unrecognized name must ask, never snap to the closest match."""
    from app.handlers.netstatus import _get_node_status

    ctx = Context(db=db, channel="voice", actor="x", thread_key="t")
    out = _get_node_status({"node": "P V 801"}, ctx)

    assert "do not guess" in out.lower()
    assert "pve-01" in out          # offers the known set
