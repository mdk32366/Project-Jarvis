from app import agents
from app.agents import Agent, build_agents, run_agent
from app.handlers.base import Context, build_registry
from app.orchestrator import run as orchestrate
from fakes import ScriptedLLM, install_llm, response, text_block, tool_block, say


def test_roster_and_delegate_registered():
    names = set(build_agents())
    assert {"researcher", "finance", "archivist"} <= names
    # delegate present only at top level
    assert build_registry(include_delegate=True).has("delegate")
    assert not build_registry().has("delegate")


def test_run_agent_plain(db, monkeypatch):
    install_llm(monkeypatch, say("The Athletics have won 9 World Series titles."))
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    out = run_agent(db, build_agents()["researcher"], "How many titles?", ctx)
    assert "9 World Series" in out


def test_run_agent_uses_only_its_tools(db, monkeypatch):
    # finance agent tries a tool it doesn't have -> blocked, then answers
    llm = ScriptedLLM(
        response([tool_block("remember_fact", {"content": "x"})], stop_reason="tool_use"),
        response([text_block("done")], stop_reason="end_turn"),
    )
    install_llm(monkeypatch, llm)
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    out = run_agent(db, build_agents()["finance"], "do something", ctx)
    assert out == "done"
    # remember_fact must NOT have run for the finance agent
    from app.models import Memory
    assert db.query(Memory).count() == 0


def test_delegate_tool_end_to_end(db, monkeypatch):
    # main turn delegates to researcher; sub-agent answers; main synthesizes.
    llm = ScriptedLLM(
        response([tool_block("delegate", {"agent": "researcher", "task": "count titles"})],
                 stop_reason="tool_use"),
        response([text_block("9 titles.")], stop_reason="end_turn"),      # sub-agent
        response([text_block("The A's have 9 championships.")], stop_reason="end_turn"),  # main synth
    )
    install_llm(monkeypatch, llm)
    reply = orchestrate(db, channel="sms", thread_key="+1555", user_text="how many A's titles?", actor="+1555")
    assert "9 championships" in reply
    # delegation was audited at the top level
    from app.models import ActionAudit
    assert db.query(ActionAudit).filter(ActionAudit.tool == "delegate").count() == 1


def test_delegate_unknown_agent(db, monkeypatch):
    from app.agents import _delegate
    ctx = Context(db=db, channel="web", actor="me", thread_key="t")
    out = _delegate({"agent": "nobody", "task": "x"}, ctx)
    assert "Unknown agent" in out
