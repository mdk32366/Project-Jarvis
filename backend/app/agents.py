"""Multi-agent delegation (Phase 1).

The top-level orchestrator can hand a scoped task to a named sub-agent via the
`delegate` tool. Each sub-agent has its own role/system prompt and a restricted
set of tools, and runs its own small tool loop. Sub-agents cannot delegate
further (they get a registry without the delegate tool), preventing recursion.

This is the seed of the "manage several agents / orchestrate tasks" goal: add a
new specialist by adding one entry to build_agents().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.handlers.base import Context, Registry, build_registry
from app.llm import create_message

log = logging.getLogger(__name__)

_MAX_ITERS = 5


@dataclass
class Agent:
    name: str
    description: str
    system: str
    tools: list[str] = field(default_factory=list)  # tool names from the handler registry


def build_agents() -> dict[str, Agent]:
    """The roster of specialists JARVIS can delegate to."""
    return {
        "researcher": Agent(
            name="researcher",
            description="General research, explanation, analysis, and drafting. No external tools.",
            system=(
                "You are JARVIS's research and writing specialist. Given a task, produce a "
                "clear, correct, concise result. You have no external tools; reason from "
                "knowledge and return the finished work only."
            ),
            tools=[],
        ),
        "finance": Agent(
            name="finance",
            description="Stock prices and portfolio status (read-only market data).",
            system=(
                "You are JARVIS's finance analyst. Use the available finance tools to fetch "
                "prices and portfolio data, then answer succinctly. You cannot place trades."
            ),
            tools=["get_stock_price", "get_portfolio"],
        ),
        "archivist": Agent(
            name="archivist",
            description="Saves durable facts about the user to long-term memory.",
            system=(
                "You are JARVIS's archivist. When given information worth keeping, use the "
                "remember_fact tool to persist it, then confirm what you saved."
            ),
            tools=["remember_fact"],
        ),
    }


def run_agent(db, agent: Agent, task: str, ctx: Context, max_iters: int = _MAX_ITERS) -> str:
    """Run a single sub-agent's tool loop for one task and return its final text."""
    reg = build_registry()  # no delegate tool -> no recursion
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
            # Sub-agents may only use their declared tools.
            if tu.name not in agent.tools:
                content = f"Tool '{tu.name}' is not available to the {agent.name} agent."
            else:
                content = reg.execute(tu.name, tu.input, ctx)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(content)})
        messages.append({"role": "user", "content": results})

    return final_text or "(no result)"


def _delegate(args: dict, ctx: Context) -> str:
    agent_name = str(args.get("agent", "")).strip()
    task = str(args.get("task", "")).strip()
    agents = build_agents()
    if agent_name not in agents:
        return f"Unknown agent '{agent_name}'. Available: {', '.join(agents)}."
    if not task:
        return "No task provided to delegate."
    log.info("delegating to %s: %s", agent_name, task[:80])
    result = run_agent(ctx.db, agents[agent_name], task, ctx)
    return f"[{agent_name}] {result}"


def register_delegate(reg: Registry) -> None:
    agents = build_agents()
    roster = "; ".join(f"{a.name}: {a.description}" for a in agents.values())
    reg.register(
        {
            "name": "delegate",
            "description": (
                "Delegate a self-contained subtask to a specialist sub-agent and get its "
                "result. Use for focused work you can hand off, then synthesize the reply. "
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
