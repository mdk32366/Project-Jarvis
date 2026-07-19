"""self_whoami — JARVIS answering "what am I running, and am I healthy?" in
conversation (health TDD §9). Reads the SAME check state the /status page shows,
so chat and page can't disagree — one source, two renderers. Read-only, ungated,
universal (registered in both registry branches like get_current_datetime).

NOT `whoami` — that's the OWNER'S details (email, phone, home airport). This is
JARVIS's own provenance + health.
"""

from __future__ import annotations

from app.handlers.base import Context, Registry
from app.provenance import provenance


def _self_whoami(args: dict, ctx: Context) -> str:
    from app.health_checks import run_all_checks

    p = provenance(ctx.db)
    results = run_all_checks(ctx.db)   # fresh — same path as GET /api/status/full
    down_deg = [r for r in results if r.status in ("down", "degraded")]
    unknown = [r for r in results if r.status == "unknown"]
    ok = [r for r in results if r.status == "ok"]

    ver = f"commit {p['commit']}" + (f", version {p['version']}" if p["version"] else "")
    built = f" (built {p['build_time']})" if p["build_time"] != "unknown" else ""
    svc = f" In service {p['in_service_days']} days." if p["in_service_days"] is not None else ""

    lines = [f"I'm running {ver}{built} on {p['app']} in {p['region']}.{svc}",
             f"Health: {len(ok)} OK, {len(down_deg)} needing attention, {len(unknown)} unknown."]
    if down_deg:
        for r in sorted(down_deg, key=lambda x: 0 if x.status == "down" else 1):
            lines.append(f"- {r.component}: {r.status.upper()} — {r.detail}")
    else:
        lines.append("Nothing is down or degraded right now.")
    if unknown:
        lines.append("No recent activity to judge: " + ", ".join(sorted(r.component for r in unknown)) + ".")
    return "\n".join(lines)


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "self_whoami",
            "description": (
                "JARVIS's OWN status: what version/commit is running, how long in "
                "service, and the current health of every subsystem (the same state "
                "the /status page shows). Use for 'how are you feeling', 'what are you "
                "running', 'are you healthy', 'what's your status'. This is NOT the "
                "owner's details — that's `whoami`."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        _self_whoami,
    )
