"""Multi-agent delegation (Phase 1) — now data-driven.

The specialist roster lives in the DB (AgentConfig), editable from the admin tab
and read live by the `delegate` tool. DEFAULT_AGENTS is the code-defined seed +
fallback used when the DB is empty/unavailable (e.g. tests without seeding).

Sub-agents cannot delegate (their registry has no delegate tool) — no recursion.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from app.handlers.base import Context, Registry, build_registry
from app.llm import create_message

log = logging.getLogger(__name__)

_MAX_ITERS = 5


@dataclass
class Agent:
    name: str
    description: str
    system: str
    tools: list[str] = field(default_factory=list)


# Code-defined seed + fallback roster.
DEFAULT_AGENTS: dict[str, Agent] = {
    "researcher": Agent(
        "researcher",
        "General research, explanation, analysis, and drafting. No external tools.",
        "You are JARVIS's research and writing specialist. Given a task, produce a clear, "
        "correct, concise result. You have no external tools; return the finished work only.",
        [],
    ),
    "finance": Agent(
        "finance",
        "Stock prices and portfolio status (read-only market data).",
        "You are JARVIS's finance analyst. Use the available finance tools to fetch prices and "
        "portfolio data, then answer succinctly. You cannot place trades.",
        ["get_stock_price", "get_portfolio"],
    ),
    "archivist": Agent(
        "archivist",
        "Saves durable facts about the user to long-term memory.",
        "You are JARVIS's archivist. When given information worth keeping, use the remember_fact "
        "tool to persist it, then confirm what you saved.",
        ["remember_fact"],
    ),
    "infra": Agent(
        "infra",
        "Reports hosted-app (Fly.io) health and estimated spend (read-only).",
        "You are JARVIS's infrastructure monitor. Use fleet_health to report which hosted "
        "apps/machines are up, and fleet_spend for credit balance and estimated run-rate. "
        "Be precise about status; flag anything not fully 'started'. Note that spend is an "
        "estimate, not an exact bill.",
        ["fleet_health", "fleet_spend"],
    ),
    "secretary": Agent(
        "secretary",
        "Drafts emails, and manages the user's tasks and captured ideas.",
        "You are JARVIS's secretary. Draft emails with draft_email and return the FULL "
        "draft (to, subject, body) as your result — the orchestrator sends it, behind a "
        "confirmation gate. Never say email cannot be sent; say the draft is ready to send. "
        "Manage tasks with "
        "add_task/list_tasks/complete_task, and capture ideas with capture_idea. Capture the "
        "user's own framing when recording an idea, not a summary of it.",
        ["draft_email", "add_task", "list_tasks", "complete_task", "cancel_task",
         "capture_idea", "list_ideas"],
    ),
    "travel": Agent(
        "travel",
        "Reports the user's trips (learned from airline confirmation emails) and searches flights.",
        "You are JARVIS's travel assistant. Use list_trips for booked travel — JARVIS learns "
        "trips from confirmation emails sent to its inbox, so it holds no airline credentials "
        "and cannot access airline accounts. Use search_flights to research options. You "
        "cannot book; if the user wants to book, say so plainly and offer to open a task.",
        ["list_trips", "search_flights"],
    ),
    "netstatus": Agent(
        "netstatus",
        "Reports local network status: Proxmox nodes and Uptime Kuma monitors (read-only).",
        "You are JARVIS's network monitor. Use get_node_status for Proxmox hosts and "
        "get_service_health for Kuma reachability. Be precise about what is down. "
        "If a node name is unrecognized, ask which was meant — never guess.",
        ["get_node_status", "get_service_health"],
    ),
    "scheduling": Agent(
        "scheduling",
        "Checks the user's calendar and helps with scheduling (calendar integration pending).",
        "You are JARVIS's scheduling assistant. Use the calendar tool to look up events and help "
        "the user plan. If the calendar is not yet connected, say so plainly.",
        ["calendar_lookup"],
    ),
}


def build_agents(db=None) -> dict[str, Agent]:
    """Live roster from the DB; falls back to DEFAULT_AGENTS if none/unavailable."""
    if db is not None:
        try:
            from app.models import AgentConfig

            rows = db.execute(select(AgentConfig).where(AgentConfig.enabled.is_(True))).scalars().all()
            if rows:
                return {
                    r.name: Agent(r.name, r.description, r.system_prompt, json.loads(r.tools or "[]"))
                    for r in rows
                }
        except Exception as e:  # pragma: no cover - defensive
            log.warning("build_agents DB read failed, using defaults: %s", e)
    return dict(DEFAULT_AGENTS)


def seed_agents(db) -> int:
    """Additively seed any DEFAULT_AGENTS missing (by name) from agent_configs.

    Safe to run every startup: inserts only default agents not already present,
    so newly code-defined specialists (e.g. `infra`) appear after a deploy while
    user-edited rows are never touched. Returns the count newly inserted.
    """
    from app.models import AgentConfig

    existing = {r.name for r in db.execute(select(AgentConfig)).scalars().all()}
    n = 0
    for a in DEFAULT_AGENTS.values():
        if a.name in existing:
            continue
        db.add(AgentConfig(name=a.name, description=a.description,
                           system_prompt=a.system, tools=json.dumps(a.tools), enabled=True))
        n += 1
    if n:
        db.commit()
        log.info("seeded %d default agent(s)", n)
    return n


def run_agent(db, agent: Agent, task: str, ctx: Context, max_iters: int = _MAX_ITERS) -> str:
    """Run a single sub-agent's tool loop for one task and return its final text."""
    reg = build_registry()  # no delegate -> no recursion
    tools = reg.anthropic_tools_subset(agent.tools)
    messages = [{"role": "user", "content": task}]
    final_text = ""

    for _ in range(max_iters):
        resp = create_message(system=agent.system, messages=messages, tools=tools)
        text_parts = [b.text for b in resp.content if b.type == "text"]
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if text_parts:
            final_text = "\n".join(text_parts)
        if resp.stop_reason != "tool_use" or not tool_uses:
            break
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            if tu.name not in agent.tools:
                content = f"Tool '{tu.name}' is not available to the {agent.name} agent."
            elif not reg.has(tu.name) or reg.is_gated(tu.name):
                # STRUCTURAL SAFETY: the confirmation gate lives in
                # orchestrator.run(); run_agent calls reg.execute() directly and
                # has no gate. A gated tool reaching a sub-agent would therefore
                # execute with NO confirmation at all — the gated=True flag would
                # be silently inert.
                #
                # Rather than rely on the convention "don't put gated tools in
                # agent rosters" (a convention that fails silently), refuse here.
                # A mis-configured AgentConfig now fails CLOSED instead of, say,
                # sending email as the user unconfirmed.
                log.error("agent %r tried gated tool %r — refusing (gate is top-level only)",
                          agent.name, tu.name)
                content = (
                    f"'{tu.name}' requires the user's confirmation and cannot be run by a "
                    f"sub-agent. Tell the orchestrator to call it directly."
                )
            else:
                content = reg.execute(tu.name, tu.input, ctx)
            _audit_subagent(ctx, agent.name, tu.name, tu.input, content)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(content)})
        messages.append({"role": "user", "content": results})

    return final_text or "(no result)"


def _audit_subagent(ctx: Context, agent_name: str, tool: str, args: dict, result) -> None:
    """Record a sub-agent's raw tool call in the audit trail (visible in Admin)."""
    try:
        from app.models import ActionAudit

        ctx.db.add(ActionAudit(
            channel=ctx.channel, actor=ctx.actor, tool=f"{agent_name}:{tool}",
            arguments=json.dumps(args)[:4000], result=str(result)[:4000], status="ok",
        ))
        ctx.db.commit()
    except Exception:
        ctx.db.rollback()


def _delegate(args: dict, ctx: Context) -> str:
    agent_name = str(args.get("agent", "")).strip()
    task = str(args.get("task", "")).strip()
    agents = build_agents(ctx.db)

    # Channel-scoped restriction (TDD 3.3). Voice auth is caller ID, which is
    # spoofable. build_agents() reads the roster LIVE from the DB, so an agent
    # edited via /api/agents to include a write tool would otherwise become
    # reachable from a phone call. Re-validate at call time; fail closed.
    if ctx.channel == "voice":
        from app.channels.voice_pipeline import VOICE_AGENTS_PHASE1, VOICE_TOOLS_PHASE1

        if agent_name not in VOICE_AGENTS_PHASE1:
            return f"The {agent_name} specialist isn't available over voice."
        _a = agents.get(agent_name)
        if _a and not set(_a.tools).issubset(VOICE_TOOLS_PHASE1):
            log.warning("voice: agent %r has non-allowlisted tools %s — refusing",
                        agent_name, sorted(set(_a.tools) - VOICE_TOOLS_PHASE1))
            return f"The {agent_name} specialist isn't available over voice."

    if agent_name not in agents:
        return f"Unknown agent '{agent_name}'. Available: {', '.join(agents)}."
    if not task:
        return "No task provided to delegate."
    log.info("delegating to %s: %s", agent_name, task[:80])
    result = run_agent(ctx.db, agents[agent_name], task, ctx)
    return f"[{agent_name}] {result}"


def register_delegate(reg: Registry, db=None) -> None:
    agents = build_agents(db)
    roster = "; ".join(f"{a.name}: {a.description}" for a in agents.values())
    reg.register(
        {
            "name": "delegate",
            "description": (
                "Delegate a self-contained subtask to a specialist sub-agent and get its result. "
                "Use for focused work you can hand off, then synthesize the reply. "
                f"Available agents — {roster}."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Which specialist to use.",
                              "enum": list(agents.keys())},
                    "task": {"type": "string", "description": "The self-contained task for the sub-agent."},
                },
                "required": ["agent", "task"],
            },
        },
        _delegate,
    )
