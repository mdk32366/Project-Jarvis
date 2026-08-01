"""Handler/tool registry and execution context.

Each capability (finance, general, …) registers tools into a shared Registry.
The orchestrator merges them, exposes their schemas to Claude, and dispatches
tool calls. Tools can be marked `gated` so the orchestrator routes them through
the human-in-the-loop confirmation gate instead of executing immediately.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


class ToolFault(Exception):
    """Raised by a handler to signal that a tool call genuinely failed —
    upstream auth rejected, a 401, an unreachable service, a bad key.

    This is the structured fault signal the audit/health substrate keys off
    (health TDD §5.1). A handler raises it INSTEAD of returning a hand-worded
    error string; the registry catches it at the single execution seam, records
    `status="error"` in the audit trail, and still surfaces the message to the
    caller verbatim — so the user keeps the handler's carefully-worded guidance
    while liveness gains a real failure it can see.

    `fault_code` is forward-looking metadata for the remediation join
    (`(component, fault_code) -> runbook`, health TDD §4.2); the audit write in
    this build does not consume it yet.
    """

    def __init__(self, message: str, fault_code: str = "error") -> None:
        super().__init__(message)
        self.fault_code = fault_code


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

    def run_tool(self, name: str, args: dict, ctx: Context) -> tuple[str, str]:
        """Execute a tool and report its outcome: returns `(result, status)`
        where `status` is `"ok"` or `"error"`.

        This is the single execution seam that knows whether a call succeeded,
        so it is where audit status is DERIVED rather than assumed. A handler
        that raises `ToolFault` (or any exception) is recorded as `error`; the
        `ToolFault` message is surfaced verbatim so the user keeps the handler's
        wording. A tool must still never crash the loop — every failure comes
        back as a string, never a raise into the caller.
        """
        if name not in self._tools:
            return f"Unknown tool: {name}", "error"
        try:
            return str(self._tools[name].fn(args, ctx)), "ok"
        except ToolFault as e:
            return str(e), "error"
        except Exception as e:  # unexpected — still must not crash the loop
            return f"Error in {name}: {e}", "error"

    def execute(self, name: str, args: dict, ctx: Context) -> str:
        """Back-compat: run a tool and return just its result string. Callers
        that also record audit should prefer `run_tool` to get the status."""
        return self.run_tool(name, args, ctx)[0]


def record_tool_audit(db, *, channel: str, actor: str, tool: str,
                      args: dict, result: str, status: str) -> None:
    """Write the `actions_audit` row for a tool run OUTSIDE the orchestrator.

    WHY THIS EXISTS. `check_liveness` derives a component's health from its audit
    rows, so a tool exercised off the audited path is invisible to it — and a
    component whose only regular exercise is off-path can LATCH. That is not
    hypothetical: the morning brief read the calendar every day through a direct
    handler call, wrote nothing, and `google_calendar_svcacct` sat `down` for a
    day on a fault that had already resolved, because the only thing that could
    have cleared it never ran.

    `status` must be DERIVED from the call's outcome (use `Registry.run_tool`,
    which is the seam that knows), never asserted by the caller. An audit row that
    says `ok` because the caller assumed so is the fabricated-`ok` bug that made
    539 rows worthless before the PR-0 epoch.

    Never raises: failing to record a call must not fail the call. It is written on
    its own commit for the same reason — a caller mid-transaction must not have its
    work committed early by an audit write.
    """
    from app.models import ActionAudit

    try:
        db.add(ActionAudit(
            channel=channel, actor=actor, tool=tool,
            arguments=json.dumps(args, default=str)[:4000],
            result=str(result)[:4000], status=status,
        ))
        db.commit()
    except Exception:  # noqa: BLE001 — recording must never break the caller
        db.rollback()
        log.warning("could not record audit row for %r", tool)


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
        from app.handlers import (datetime_tools, finance, ideas, planning, repos,
                                  scheduling, secretary, selfstatus, travel)

        agents.register_delegate(reg, db)
        finance.register_trading(reg)
        # Gated tools MUST be registered here, at top level. The confirmation
        # gate only runs in orchestrator.run(); sub-agents call reg.execute()
        # directly and bypass it (run_agent now refuses gated tools outright).
        # So anything irreversible lives up here, alongside trading.
        secretary.register_gated(reg)     # send_email
        scheduling.register_gated(reg)    # create_event
        travel.register_gated(reg)        # book_flight (+ TOTP second factor)
        ideas.register_gated(reg)         # create_project_from_idea (creates a repo)
        repos.register_gated(reg)         # create_project_repo (creates a repo)
        # get_current_datetime is ungated and universal — registered at top level
        # AND in the sub-agent branch (TDD §4.1: both branches).
        datetime_tools.register(reg)
        # self_whoami: JARVIS's own provenance + health. Universal + ungated, so
        # the orchestrator (a pure delegator) can answer "how are you feeling"
        # directly instead of delegating it (health TDD §9).
        selfstatus.register(reg)
        # commit_document: UNGATED (a branch + PR is reversible) but registered
        # top-level ON PURPOSE — that placement is what keeps it off voice. The
        # `allow` restriction below drops anything absent from
        # VOICE_TOOLS_PHASE1, and a sub-agent roster could not achieve the same
        # thing because every agent is voice-reachable (repos.py docstring).
        repos.register(reg)
        # emit_tdd: same reasoning as commit_document — top-level so the voice
        # allowlist excludes it. Reviewing a design read aloud is not review.
        planning.register_top_level(reg)
        if allow is not None:
            reg.restrict(allow)
        return reg

    # Sub-agent registry: the domain tools specialists draw from (no delegate,
    # no gated trading -> no recursion, no ungoverned money actions).
    from app.handlers import (audit, callback, contacts, datetime_tools, episodes,
                              finance, general, googledocs, ideas, infra, location,
                              maps, netstatus, planning, projects, scheduling, secretary,
                              selfstatus, tailscale, tasks, travel, watches, websearch)

    finance.register(reg)
    general.register(reg)
    episodes.register(reg)
    scheduling.register(reg)
    infra.register(reg)
    netstatus.register(reg)
    tasks.register(reg)
    ideas.register(reg)
    planning.register(reg)
    projects.register(reg)
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
    googledocs.register(reg)
    selfstatus.register(reg)   # self_whoami — universal, ungated (both branches)
    # get_current_datetime: ungated, universal, no side effects.
    # Registered in BOTH branches (TDD §4.1). Sub-agents can always call it;
    # run_agent() also auto-injects it into every agent's effective tool list
    # so it's available even if an agent's DB roster omits it (TDD §4.2).
    datetime_tools.register(reg)
    return reg
