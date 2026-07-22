"""Network status tools — Proxmox + Uptime Kuma.

PHASE 1: THERE IS NO LIVE BACKEND. JARVIS runs on Fly; Proxmox and Uptime Kuma
are on the local network and are not reachable from Fly. Until JARVIS migrates
onto the LAN, the data source (`_fetch_nodes` / `_fetch_monitors`) returns None
and the tools answer with an honest "[… not configured]" notice — they DO NOT
invent status. Presenting fixed sample data as live was a real defect: a node the
fixture called "online" was reported up while it was actually down, and rpi-02
was reported down on every call regardless of reality — on the live agent roster.

Principle (TDD §7): **build against the real interface.** The fixtures below match
the real API shapes and remain as the shape reference (and as the data the tests
inject). At migration you swap ONLY the two `_fetch_*` bodies for a real client —
the speech rendering, the fuzzy matcher (Phase 2), and the orchestrator prompt all
keep working untouched.

Division of labour, once real:
  * Proxmox `/api2/json/nodes`  -> VM/host lifecycle + resources
  * Uptime Kuma                 -> reachability (and it knows about the laptops,
                                   iPad, and phone, which Proxmox does not)

Everything here is READ-ONLY. Node start/stop is Phase 3, and lands behind a
second factor — not merely behind readback. See TDD §3.2.
"""

from __future__ import annotations

from app.handlers.base import Context

# ── Fixtures: shaped exactly like the real payloads ──────────────────────────
# Proxmox GET /api2/json/nodes -> {"data": [ ... ]}
_PROXMOX_NODES: list[dict] = [
    {
        "node": "pve-01",
        "status": "online",
        "uptime": 481203,
        "cpu": 0.07,
        "maxcpu": 4,
        "mem": 3_221_225_472,
        "maxmem": 8_589_934_592,
        "disk": 41_875_931_136,
        "maxdisk": 209_715_200_000,
    },
    {
        "node": "rpi-01",
        "status": "online",
        "uptime": 902144,
        "cpu": 0.02,
        "maxcpu": 4,
        "mem": 512_000_000,
        "maxmem": 4_294_967_296,
        "disk": 8_589_934_592,
        "maxdisk": 62_914_560_000,
    },
    {
        "node": "rpi-02",
        "status": "offline",
        "uptime": 0,
        "cpu": 0.0,
        "maxcpu": 4,
        "mem": 0,
        "maxmem": 4_294_967_296,
        "disk": 0,
        "maxdisk": 62_914_560_000,
    },
]

# Uptime Kuma monitor shape (heartbeat-ish summary).
_KUMA_MONITORS: list[dict] = [
    {"name": "pve-01", "active": True, "status": 1, "ping": 3, "uptime_24h": 1.0},
    {"name": "rpi-01", "active": True, "status": 1, "ping": 5, "uptime_24h": 0.998},
    {"name": "rpi-02", "active": True, "status": 0, "ping": None, "uptime_24h": 0.41},
    {"name": "rpi-03", "active": True, "status": 1, "ping": 4, "uptime_24h": 1.0},
    {"name": "jarvis-mdk.fly.dev", "active": True, "status": 1, "ping": 82, "uptime_24h": 1.0},
]


# ── Honest "no backend" notices ──────────────────────────────────────────────
# The "[" prefix matches the unconfigured-sentinel shape other sources use
# (briefing / infra / tailscale), so any consumer suppresses these the same way.
NODES_NOT_CONFIGURED = (
    "[proxmox not configured] I can't see the Proxmox nodes — they're on the local "
    "network, which isn't reachable from where I run yet. That integration lands "
    "when JARVIS moves onto the LAN; until then I can't tell you what's up or down."
)
SERVICES_NOT_CONFIGURED = (
    "[uptime kuma not configured] I can't see the Uptime Kuma monitors — they're on "
    "the local network, not reachable from where I run yet, so I can't tell you "
    "which hosts or services are reachable."
)


# ── Data source (the migration seam) ─────────────────────────────────────────
# Until JARVIS is on the LAN there is NO reachable backend, so these return None
# and the tools answer honestly. At migration, replace each body with a real
# client returning rows shaped exactly like the fixtures above. Do NOT return the
# fixtures from here: presenting fixed sample data as live status is the defect
# this replaced. The fixtures stay as the shape reference and the tests inject
# them by patching these functions.
def _fetch_nodes() -> list[dict] | None:
    """Live Proxmox node list, or None when no backend is reachable."""
    return None


def _fetch_monitors() -> list[dict] | None:
    """Live Uptime Kuma monitor list, or None when no backend is reachable."""
    return None


# ── Speech-friendly rendering (TDD §7.1) ─────────────────────────────────────
# Tool output is spoken aloud. Raw bytes and epoch seconds must never reach the
# TTS. Render here — deterministic, and it doesn't burn model attention.
def _gb(n: int) -> str:
    return f"{n / 1_073_741_824:.1f}".rstrip("0").rstrip(".")


def _plural(n: int, word: str) -> str:
    """'3 nodes' / '1 node'. Never 'node(s)' — TTS speaks the parens aloud."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _uptime(seconds: int) -> str:
    if seconds <= 0:
        return "down"
    days, rem = divmod(seconds, 86400)
    hours = rem // 3600
    if days and hours:
        return f"{_plural(days, 'day')}, {_plural(hours, 'hour')}"
    if days:
        return _plural(days, "day")
    return _plural(hours, "hour")


def _render_node(n: dict) -> str:
    if n["status"] != "online":
        return f"{n['node']}: OFFLINE."
    cpu_pct = round(n["cpu"] * 100)
    return (
        f"{n['node']}: online, up {_uptime(n['uptime'])}, "
        f"CPU {cpu_pct} percent, "
        f"memory {_gb(n['mem'])} of {_gb(n['maxmem'])} gigabytes."
    )


def _render_monitor(m: dict) -> str:
    if m["status"] != 1:
        return f"{m['name']}: DOWN. 24-hour uptime {round(m['uptime_24h'] * 100)} percent."
    return (
        f"{m['name']}: up, {m['ping']} millisecond response, "
        f"24-hour uptime {round(m['uptime_24h'] * 100)} percent."
    )


# ── Tools (signature mirrors app/handlers/infra.py) ──────────────────────────
def _summarize(items: list[dict], label: str, is_ok, render, name_of) -> str:
    """Report the EXCEPTION, not the table.

    A human colleague asked "how are the servers?" says "all good" or "rpi-02 is
    down." They do not read you every row with CPU and memory for each. Reading
    the table aloud is what makes an assistant sound like a machine.

    So: if everything is healthy, say so in one line. If something is wrong, lead
    with THAT and give detail only for the broken thing. Detail on the healthy
    ones is available on request — see the `verbose` flag.
    """
    bad = [i for i in items if not is_ok(i)]
    n = len(items)

    if not bad:
        if n == 1:
            return f"{name_of(items[0])} is up."
        return f"All {n} {label}s are up."

    if len(bad) == 1:
        return f"{render(bad[0])}\nEverything else is up ({n - 1} of {n})."

    names = ", ".join(name_of(b) for b in bad)
    return (f"{len(bad)} of {n} {label}s are down: {names}.\n"
            + "\n".join(render(b) for b in bad))


def _get_node_status(args: dict, ctx: Context) -> str:
    """Proxmox node status via `_fetch_nodes()`. No backend reachable -> an honest
    not-configured notice, never invented status."""
    all_nodes = _fetch_nodes()
    if all_nodes is None:
        return NODES_NOT_CONFIGURED

    target = (args.get("node") or "").strip().lower()
    verbose = bool(args.get("verbose"))

    nodes = all_nodes
    if target:
        # Phase 2 replaces this exact-match with resolve_node() fuzzy matching.
        # STT WILL mangle "pve-01" into "PVE oh one" / "P V 801" — silently and
        # confidently. Until the matcher exists, an unrecognized name must ASK,
        # never guess. See TDD §9.
        nodes = [n for n in all_nodes if n["node"].lower() == target]
        if not nodes:
            known = ", ".join(n["node"] for n in all_nodes)
            return (
                f"No node named '{args.get('node')}'. Known nodes: {known}. "
                f"Ask the user which one they meant — do not guess."
            )

    # A specific node was asked about, or full detail requested: give the row.
    if target or verbose:
        return "\n".join(_render_node(n) for n in nodes)

    # Otherwise summarize. Don't read the table aloud.
    return _summarize(
        nodes, "node",
        is_ok=lambda n: n["status"] == "online",
        render=_render_node,
        name_of=lambda n: n["node"],
    )


def _get_service_health(args: dict, ctx: Context) -> str:
    """Uptime Kuma monitor status via `_fetch_monitors()`. No backend reachable ->
    an honest not-configured notice, never invented status."""
    all_mons = _fetch_monitors()
    if all_mons is None:
        return SERVICES_NOT_CONFIGURED

    target = (args.get("service") or "").strip().lower()
    verbose = bool(args.get("verbose"))

    mons = all_mons
    if target:
        mons = [m for m in all_mons if m["name"].lower() == target]
        if not mons:
            known = ", ".join(m["name"] for m in all_mons)
            return (
                f"No monitor named '{args.get('service')}'. Known: {known}. "
                f"Ask the user which one they meant — do not guess."
            )

    if target or verbose:
        return "\n".join(_render_monitor(m) for m in mons)

    return _summarize(
        mons, "service",
        is_ok=lambda m: m["status"] == 1,
        render=_render_monitor,
        name_of=lambda m: m["name"],
    )


# ── Registry wiring ──────────────────────────────────────────────────────────
# Registered into the SUB-AGENT registry (build_registry's include_delegate=False
# branch), alongside finance/general/scheduling/infra. Reached from voice via the
# `netstatus` specialist — the top-level registry is a pure delegator and holds
# no read tools at all.
#
# These are READ-ONLY. When start_node / stop_node arrive in Phase 3 they must be
# gated INDEPENDENTLY of notional value. The current gate (_needs_confirmation)
# is financial in shape: is_gated(name) AND notional >= confirm_threshold_usd. A
# stop_node has no notional, so registry.notional() returns None and confirmation
# happens *by accident*. Correct outcome, wrong reason — make "destructive" its
# own flag before relying on it.
def register(reg) -> None:
    reg.register(
        {
            "name": "get_node_status",
            "description": (
                "Status of Proxmox nodes on the local network. Read-only. Returns a "
                "SUMMARY by default (e.g. 'all 3 are up' / 'rpi-02 is down'), not a "
                "table; omit `node` for all. NOTE: the LAN isn't reachable from where "
                "JARVIS runs yet, so today this reports that it can't see the nodes "
                "rather than inventing status — relay that honestly, don't guess."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "Node name, e.g. 'pve-01'. Omit for all nodes.",
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "Full per-node detail. Default false, which gives a "
                                       "SUMMARY ('all 3 nodes are up' / 'rpi-02 is down'). "
                                       "Only set true if the user explicitly asks for details.",
                    },
                },
            },
        },
        _get_node_status,
    )
    reg.register(
        {
            "name": "get_service_health",
            "description": (
                "Reachability from Uptime Kuma for hosts and services (laptops, phone, "
                "iPad, the Fly app). Read-only. Omit `service` for all. NOTE: not "
                "reachable from where JARVIS runs yet, so today it reports that it "
                "can't see them rather than inventing status."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Monitor name. Omit for all monitors.",
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "Full per-service detail. Default false, which gives a "
                                       "SUMMARY. Only set true if the user asks for details.",
                    },
                },
            },
        },
        _get_service_health,
    )
