"""Handler/tool registry and execution context.

Each capability (finance, general, …) registers tools into a shared Registry.
The orchestrator merges them, exposes their schemas to Claude, and dispatches
tool calls. Tools can be marked `gated` so the orchestrator routes them through
the human-in-the-loop confirmation gate instead of executing immediately.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session


@dataclass
class Context:
    """Everything a tool needs to run, plus who/where the request came from."""

    db: Session
    channel: str          # email | web | sms
    actor: str            # requesting identity (email address, username, …)
    thread_key: str


@dataclass
class _ToolSpec:
    schema: dict
    fn: Callable[[dict, Context], str]
    gated: bool
    # Returns the dollar amount at risk (or None) so the gate can apply a threshold.
    notional: Optional[Callable[[dict], Optional[float]]]
    # Human-readable summary for the confirmation prompt.
    summarize: Callable[[dict], str]
    # Optional pre-gate check for a GATED tool. Runs BEFORE the confirmation
    # gate is raised. Returning a string means "refuse outright, do not gate,
    # do not execute the real fn" — used for checks that should never reach a
    # user as "confirm or cancel" (an offer_id we never retrieved, booking
    # disabled, an absurd fare). Returning None means "proceed to the normal
    # gated flow". Only book_flight uses this today; every other gated tool
    # leaves it unset and behaves exactly as before. Takes Context (unlike
    # notional/summarize) because book_flight's check needs thread-scoped DB
    # access to look up the retained offer.
    pregate: Optional[Callable[[dict, Context], Optional[str]]] = None


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, _ToolSpec] = {}

    def register(
        self,
        schema: dict,
        fn: Callable[[dict, Context], str],
        *,
        gated: bool = False,
        notional: Optional[Callable[[dict], Optional[float]]] = None,
        summarize: Optional[Callable[[dict], str]] = None,
        pregate: Optional[Callable[[dict, Context], Optional[str]]] = None,
    ) -> None:
        name = schema["name"]
        self._tools[name] = _ToolSpec(
            schema=schema,
            fn=fn,
            gated=gated,
            notional=notional,
            summarize=summarize or (lambda i: f"{name}({i})"),
            pregate=pregate,
        )

    def anthropic_tools(self) -> list[dict]:
        return [t.schema for t in self._tools.values()]

    def anthropic_tools_subset(self, names: list[str]) -> list[dict]:
        return [self._tools[n].schema for n in names if n in self._tools]

    def has(self, name: str) -> bool:
        return name in self._tools

    def is_gated(self, name: str) -> bool:
        # Must not KeyError: callers ask about tools that may not be in THIS
        # registry (e.g. run_agent checking a rogue roster entry). Unknown =>
        # not gated here; the caller's `has()` check handles absence.
        spec = self._tools.get(name)
        return bool(spec and spec.gated)

    def notional(self, name: str, args: dict) -> Optional[float]:
        fn = self._tools[name].notional
        return fn(args) if fn else None

    def summarize(self, name: str, args: dict) -> str:
        return self._tools[name].summarize(args)

    def pregate(self, name: str, args: dict, ctx: Context) -> Optional[str]:
        """None -> proceed to the normal gated flow. A string -> refuse
        outright with that message; the gate is never raised and the real fn
        never runs."""
        fn = self._tools[name].pregate
        return fn(args, ctx) if fn else None

    def restrict(self, allow: set[str]) -> None:
        """Keep only allow-listed tools. Fail closed."""
        self._tools = {k: v for k, v in self._tools.items() if k in allow}

    def execute(self, name: str, args: dict, ctx: Context) -> str:
        if name not in self._tools:
            return f"Unknown tool: {name}"
        try:
            return self._tools[name].fn(args, ctx)
        except Exception as e:  # tools must never crash the loop
            return f"Error in {name}: {e}"


def build_registry(include_delegate: bool = False, db=None, allow: set[str] | None = None) -> Registry:
    """Assemble the registry from all handler modules.

    ``include_delegate`` adds the multi-agent ``delegate`` tool. It is enabled
    only for the top-level orchestrator; sub-agents get a registry WITHOUT it so
    they cannot delegate recursively.
    """
    reg = Registry()
    if include_delegate:
        # Top-level orchestrator = pure delegator: it only routes to specialists
        # (delegate) and governs the one irreversible action (trading) behind the
        # confirmation gate. All read-only/domain tools live in specialist agents.
        from app import agents
        from app.handlers import finance, scheduling, secretary, travel

        agents.register_delegate(reg, db)
        finance.register_trading(reg)
        # Gated tools MUST be registered here, at top level. The confirmation
        # gate only runs in orchestrator.run(); sub-agents call reg.execute()
        # directly and bypass it (run_agent now refuses gated tools outright).
        # So anything irreversible lives up here, alongside trading.
        secretary.register_gated(reg)     # send_email
        scheduling.register_gated(reg)    # create_event
        travel.register_gated(reg)        # book_flight (+ TOTP second factor)
        if allow is not None:
            reg.restrict(allow)
        return reg

    # Sub-agent registry: the domain tools specialists draw from (no delegate,
    # no gated trading -> no recursion, no ungoverned money actions).
    from app.handlers import (audit, callback, contacts, finance, general, ideas,
                              infra, location, maps, netstatus, scheduling, secretary,
                              tailscale, tasks, travel, watches, websearch)

    finance.register(reg)
    general.register(reg)
    scheduling.register(reg)
    infra.register(reg)
    netstatus.register(reg)
    tasks.register(reg)
    ideas.register(reg)
    secretary.register(reg)
    travel.register(reg)
    contacts.register(reg)
    callback.register(reg)
    maps.register(reg)
    location.register(reg)
    audit.register(reg)
    websearch.register(reg)
    tailscale.register(reg)
    watches.register(reg)
    return reg
