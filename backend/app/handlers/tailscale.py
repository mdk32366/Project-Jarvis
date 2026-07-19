"""Tailscale — the tailnet.

Closes a real gap. You asked her "can you see my Tailscale network?" and she had
to say no: netstatus covers Proxmox (VMs) and Kuma (reachability), and neither
knows anything about the tailnet.

The three now divide cleanly:
  * Proxmox   — VM and host lifecycle
  * Kuma      — is a service reachable
  * Tailscale — is a DEVICE on the network, and when does its key expire

That last one is the sleeper. A Tailscale node key expires (180 days by default)
and the device silently drops off the tailnet. You find out when something you
depend on stops working, usually at the worst moment. JARVIS can just tell you a
week ahead.

Read-only. Deliberately: the API can delete devices and rotate keys, and nothing
about a phone call justifies that.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.handlers.base import Context, Registry, ToolFault

log = logging.getLogger(__name__)

_API = "https://api.tailscale.com/api/v2"
_TIMEOUT = 20.0

NOT_CONFIGURED = (
    "[tailscale not configured] I can't see your tailnet — that needs a Tailscale API "
    "key (TAILSCALE_API_KEY) and your tailnet name (TAILSCALE_TAILNET, e.g. "
    "your-org.github or an email address)."
)


def _fetch_devices() -> list[dict]:
    import httpx

    url = f"{_API}/tailnet/{settings.tailscale_tailnet}/devices"
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.get(url, auth=(settings.tailscale_api_key, ""))
    if r.status_code == 401:
        raise RuntimeError("Tailscale rejected the API key.")
    if r.status_code == 404:
        raise RuntimeError(f"No tailnet called {settings.tailscale_tailnet!r}.")
    r.raise_for_status()
    return (r.json() or {}).get("devices", []) or []


def _is_online(d: dict) -> bool:
    """Tailscale reports lastSeen, not a boolean. A device seen in the last five
    minutes is up; anything older has dropped off."""
    seen = d.get("lastSeen") or ""
    try:
        ts = datetime.fromisoformat(seen.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - ts) < timedelta(minutes=5)


def _name(d: dict) -> str:
    return (d.get("hostname") or d.get("name", "?")).split(".")[0]


def _expiry_warning(d: dict) -> str | None:
    """Key expiry is the silent killer: the node drops off the tailnet and you
    find out when something breaks. Warn a week out."""
    if d.get("keyExpiryDisabled"):
        return None
    raw = d.get("expires") or ""
    try:
        exp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # .days TRUNCATES: 2.99 days reads as 2, so a key expiring in under 3 days
    # gets announced as 2. Round instead — under-warning about an expiry is the
    # one direction that actually costs you.
    import math

    delta = exp - datetime.now(timezone.utc)
    if delta.total_seconds() < 0:
        return f"{_name(d)}: key EXPIRED"
    days = math.ceil(delta.total_seconds() / 86400)
    if days <= 7:
        return f"{_name(d)}: key expires in {days} day{'s' if days != 1 else ''}"
    return None


def _tailscale_status(args: dict, ctx: Context) -> str:
    if not (settings.tailscale_api_key and settings.tailscale_tailnet):
        return NOT_CONFIGURED

    try:
        devices = _fetch_devices()
    except Exception as e:  # noqa: BLE001
        log.error("tailscale failed: %s", e)
        raise ToolFault(f"Couldn't reach Tailscale: {e}")

    if not devices:
        return "No devices on the tailnet."

    target = (args.get("device") or "").strip().lower()
    if target:
        hits = [d for d in devices if target in _name(d).lower()]
        if not hits:
            known = ", ".join(_name(d) for d in devices)
            return (f"No device called '{args.get('device')}'. On the tailnet: {known}. "
                    f"Ask which one — don't guess.")
        d = hits[0]
        state = "up" if _is_online(d) else "OFFLINE"
        ip = (d.get("addresses") or ["?"])[0]
        out = f"{_name(d)}: {state}, {ip}, {d.get('os', '?')}."
        warn = _expiry_warning(d)
        if warn:
            out += f" {warn}."
        return out

    online = [d for d in devices if _is_online(d)]
    offline = [d for d in devices if not _is_online(d)]
    warnings = [w for w in (_expiry_warning(d) for d in devices) if w]

    # Exception reporting — say what's WRONG, not the whole table (see netstatus).
    if not offline:
        out = f"All {len(devices)} devices are on the tailnet."
    elif len(offline) == 1:
        out = (f"{_name(offline[0])} is off the tailnet. "
               f"The other {len(online)} are up.")
    else:
        names = ", ".join(_name(d) for d in offline)
        out = f"{len(offline)} of {len(devices)} devices are off the tailnet: {names}."

    if warnings:
        out += " Heads up — " + "; ".join(warnings) + "."
    if args.get("verbose"):
        out += "\n" + "\n".join(
            f"- {_name(d)}: {'up' if _is_online(d) else 'OFFLINE'}, "
            f"{(d.get('addresses') or ['?'])[0]}, {d.get('os', '?')}"
            for d in devices
        )
    return out


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "tailscale_status",
            "description": (
                "Devices on the user's Tailscale network (tailnet): which are online, their "
                "IPs, and whether any node key is about to expire. This is the TAILNET — "
                "distinct from Proxmox nodes and Uptime Kuma monitors. Read-only."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "device": {"type": "string",
                               "description": "One device by name. Omit for a summary."},
                    "verbose": {"type": "boolean",
                                "description": "Full per-device list. Default false — the "
                                               "summary reports only what's WRONG."},
                },
            },
        },
        _tailscale_status,
    )
