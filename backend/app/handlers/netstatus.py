"""Network status tools — Proxmox + Uptime Kuma.

PHASE 1: THESE ARE STUBS. JARVIS runs on Fly; Proxmox and the tailnet are on the
local network and are not reachable from Fly. Those are separate projects until
JARVIS migrates onto the LAN.

Principle (TDD §7): **build against the real interface, fake the data.** The
return shapes match the real APIs, so at migration you swap the function bodies
only — the speech rendering, the fuzzy matcher (Phase 2), and the orchestrator
prompt all keep working untouched.

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
        return f"{days} day{'s' if days != 1 else ''}, {hours} hour{'s' if hours != 1 else ''}"
    if days:
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''}"


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
def _get_node_status(args: dict, ctx: Context) -> str:
    """STUB — Proxmox-shaped. Swap the fixture for a real client at migration."""
    target = (args.get("node") or "").strip().lower()

    nodes = _PROXMOX_NODES
    if target:
        # Phase 2 replaces this exact-match with resolve_node() fuzzy matching.
        # STT WILL mangle "pve-01" into "PVE oh one" / "P V 801" — silently and
        # confidently. Until the matcher exists, an unrecognized name must ASK,
        # never guess. See TDD §9.
        nodes = [n for n in _PROXMOX_NODES if n["node"].lower() == target]
        if not nodes:
            known = ", ".join(n["node"] for n in _PROXMOX_NODES)
            return (
                f"No node named '{args.get('node')}'. Known nodes: {known}. "
                f"Ask the user which one they meant — do not guess."
            )

    offline = [n for n in nodes if n["status"] != "online"]
    lines = [_render_node(n) for n in nodes]
    # NB: never "node(s)" — TTS reads that aloud as "node open paren s close paren".
    header = "" if target else f"{_plural(len(nodes), 'node')}, {len(offline)} offline."
    return "\n".join([header, *lines]).strip()


def _get_service_health(args: dict, ctx: Context) -> str:
    """STUB — Uptime Kuma-shaped. Swap the fixture for a real client at migration."""
    target = (args.get("service") or "").strip().lower()

    mons = _KUMA_MONITORS
    if target:
        mons = [m for m in _KUMA_MONITORS if m["name"].lower() == target]
        if not mons:
            known = ", ".join(m["name"] for m in _KUMA_MONITORS)
            return (
                f"No monitor named '{args.get('service')}'. Known: {known}. "
                f"Ask the user which one they meant — do not guess."
            )

    down = [m for m in mons if m["status"] != 1]
    lines = [_render_monitor(m) for m in mons]
    header = "" if target else f"{_plural(len(mons), 'monitor')}, {len(down)} down."
    return "\n".join([header, *lines]).strip()


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
                "Get status of Proxmox nodes on the local network: online/offline, "
                "uptime, CPU, memory. Omit `node` for all nodes. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "Node name, e.g. 'pve-01'. Omit for all nodes.",
                    }
                },
            },
        },
        _get_node_status,
    )
    reg.register(
        {
            "name": "get_service_health",
            "description": (
                "Get reachability from Uptime Kuma for hosts and services (including "
                "laptops, phone, iPad, and the Fly app). Omit `service` for all. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "description": "Monitor name. Omit for all monitors.",
                    }
                },
            },
        },
        _get_service_health,
    )
