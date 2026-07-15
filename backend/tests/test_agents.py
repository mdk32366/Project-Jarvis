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


def test_seed_reconciles_tools_onto_an_existing_agent(db):
    """REGRESSION: THE bug that made JARVIS say "I don't have that capability"
    about tools she demonstrably had.

    seed_agents() was purely additive — it skipped any agent that already
    existed. But build_agents() reads the roster LIVE FROM THE DB, so a tool
    added to an existing agent in DEFAULT_AGENTS never reached production. The
    code said the secretary could sync contacts; the database said she couldn't;
    the database won.

    This is the actual prod state that was found:
        secretary | ["draft_email","add_task","list_tasks","complete_task",
                     "cancel_task","capture_idea","list_ideas"]
    ...frozen from the first deploy, missing everything added since.
    """
    import json

    from app.agents import DEFAULT_AGENTS, build_agents, seed_agents
    from app.models import AgentConfig

    # The exact frozen row from production.
    db.add(AgentConfig(
        name="secretary",
        description="old",
        system_prompt="old",
        tools=json.dumps(["draft_email", "add_task", "list_tasks", "complete_task",
                          "cancel_task", "capture_idea", "list_ideas"]),
        enabled=True,
    ))
    db.commit()

    seed_agents(db)

    tools = set(build_agents(db)["secretary"].tools)
    # Everything added since the first deploy must now be reachable.
    for t in ("whoami", "lookup_contact", "save_contact", "sync_google_contacts",
              "google_status", "call_me_back"):
        assert t in tools, f"{t} still invisible in production"
    # ...and nothing was lost.
    assert "draft_email" in tools


def test_seed_does_not_clobber_an_admin_added_tool(db):
    """Union, not overwrite. An admin who adds a tool via /api/agents keeps it."""
    import json

    from app.agents import build_agents, seed_agents
    from app.models import AgentConfig

    db.add(AgentConfig(name="archivist", description="d", system_prompt="s",
                       tools=json.dumps(["remember_fact", "some_custom_tool"]),
                       enabled=True))
    db.commit()

    seed_agents(db)

    tools = set(build_agents(db)["archivist"].tools)
    assert "some_custom_tool" in tools     # admin's edit survives
    assert "remember_fact" in tools


def test_every_default_agent_tool_actually_exists_in_the_registry():
    """CANARY. An agent whose roster names a tool the registry doesn't have is a
    silent dead end: run_agent refuses it, and JARVIS reports a missing
    capability that looks like a model failure rather than a config error."""
    from app.agents import DEFAULT_AGENTS
    from app.handlers.base import build_registry

    reg = build_registry()          # sub-agent registry
    for name, agent in DEFAULT_AGENTS.items():
        for tool in agent.tools:
            assert reg.has(tool), f"agent {name!r} claims {tool!r}, which isn't registered"


def test_seed_reconciles_the_description_because_it_is_the_routing_signal(db):
    """REGRESSION, layer two.

    Fixing the frozen TOOL roster wasn't enough. The agent `description` is what
    the orchestrator reads (through the delegate tool's roster) to decide where
    to send a request. It is the ROUTING SIGNAL, not documentation.

    The secretary's roster gained sync_google_contacts, but her description still
    said only "Drafts emails, and manages the user's tasks and captured ideas."
    The orchestrator read that, saw nothing about Google, and never routed there.
    From the prod logs:

        delegating to secretary: Do you have any access to Google Contacts?

    A QUESTION, not a task — and the secretary, unable to introspect, answered
    from her prompt: "no." The tool was right there, wired correctly, and never
    called.
    """
    import json

    from app.agents import DEFAULT_AGENTS, build_agents, seed_agents
    from app.models import AgentConfig

    db.add(AgentConfig(
        name="secretary",
        description="Drafts emails, and manages the user's tasks and captured ideas.",
        system_prompt="tuned by an admin — must NOT be clobbered",
        tools=json.dumps(["draft_email"]),
        enabled=True,
    ))
    db.commit()

    seed_agents(db)

    sec = build_agents(db)["secretary"]
    assert "GOOGLE CONTACTS" in sec.description, "capability still invisible to the router"
    assert "CALL THE USER BACK" in sec.description
    assert sec.description == DEFAULT_AGENTS["secretary"].description

    # system_prompt is TUNING, not routing — an admin's wording survives.
    row = db.query(AgentConfig).filter_by(name="secretary").one()
    assert row.system_prompt == "tuned by an admin — must NOT be clobbered"


def test_delegate_tells_the_model_to_send_an_action_not_a_question(db):
    """A sub-agent cannot introspect its own capabilities. Asking one "can you do
    X?" gets a guess, usually wrong. Tell it to DO X."""
    from app.agents import register_delegate
    from app.handlers.base import Registry

    reg = Registry()
    register_delegate(reg, db)
    desc = reg.anthropic_tools()[0]["description"]

    assert "never a question" in desc.lower()
    assert "it HAS it" in desc


def test_every_agent_description_mentions_its_headline_capability():
    """CANARY. A tool the description doesn't advertise is unroutable — the
    orchestrator will never send work its way, and the failure is silent."""
    from app.agents import DEFAULT_AGENTS

    must_mention = {
        "secretary": ["GOOGLE CONTACTS", "CALL THE USER BACK", "address book",
                      "GOOGLE DOCS", "GOOGLE SHEETS"],
        "travel":    ["SEARCH"],
        "infra":     ["Fly.io"],
        "netstatus": ["Proxmox"],
    }
    for name, phrases in must_mention.items():
        desc = DEFAULT_AGENTS[name].description
        for phrase in phrases:
            assert phrase in desc, f"{name!r} has the tools but doesn't advertise {phrase!r}"
