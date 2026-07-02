"""Infra handler — read-only status & cost of the hosted Fly.io fleet.

Powers the "## Hosted apps" section of the morning briefing and gives the
`infra` specialist agent two tools:

  * ``fleet_health``  — per-app machine status via the Fly **Machines API**
                        (https://api.machines.dev/v1). Rock-solid & documented.
  * ``fleet_spend``   — the org **credit balance** via the Fly **GraphQL API**
                        (https://api.fly.io/graphql) plus a *rough* monthly
                        run-rate estimate computed from the machine sizes we
                        already see in health.

    Brutally honest caveat, baked into the output: Fly has **no stable public
    API for month-to-date spend** — they removed ``organization.billables`` in
    2023. So the dollar run-rate here is an *estimate* from machine specs and
    published pricing; the authoritative number lives on the Fly dashboard.

Auth: a Fly API token (a read-only or deploy token works) in
``FLY_API_TOKEN_READ``. Everything degrades to a clear "not configured" / error
string rather than raising, so one bad source can never sink the briefing.

``httpx`` is already a dependency; imported lazily to keep import cost off the
hot path and tests hermetic.
"""

from __future__ import annotations

import logging

from app.config import settings
from app.handlers.base import Context, Registry

log = logging.getLogger(__name__)

_MACHINES_BASE = "https://api.machines.dev/v1"
_GRAPHQL_URL = "https://api.fly.io/graphql"
_TIMEOUT = 15.0

# ── Rough pricing for the monthly run-rate estimate ──────────────────────────
# Fly bills per running second; these are the always-on /30-day preset prices
# (USD) plus a per-GB add-on for RAM above the preset's included amount. Prices
# drift — treat as ballpark and update from https://fly.io/docs/about/pricing/.
# Keyed by shared-cpu preset (cpu_kind="shared", N cpus). Included RAM in MB.
_PRESET_PRICE = {
    ("shared", 1): (1.94, 256),    # shared-cpu-1x
    ("shared", 2): (3.89, 512),    # shared-cpu-2x
    ("shared", 4): (7.78, 1024),   # shared-cpu-4x
    ("shared", 8): (15.55, 2048),  # shared-cpu-8x
}
_EXTRA_RAM_PER_GB = 5.0  # ~$5 / 30 days / GB above the preset's included RAM


def _token() -> str:
    return (settings.fly_api_token_read or "").strip()


def _watched_apps() -> list[str]:
    return settings.watched_fly_app_list


def _expected_running(app: str) -> int:
    """Min RUNNING machines before an app is 'DEGRADED'. Default 1 (>=1 must be up).

    Async/scale-to-zero apps set this to their listener count; always-on multi-
    process apps set it to their process-group count (e.g. jarvis-mdk = 3).
    """
    return max(1, settings.fleet_expected_map.get(app, 1))


def _auth_variants() -> list[str]:
    """Ordered Authorization header values to try — Fly has two token schemes.

    ``flyctl auth token`` issues a Bearer-compatible token; ``fly tokens create``
    issues a macaroon that needs the ``FlyV1`` scheme. Rather than require the
    user to know which they pasted, we try one then fall back to the other on
    401/403. Tolerates a stray ``Bearer `` prefix and surrounding whitespace.
    """
    tok = _token()
    if tok.lower().startswith("bearer "):
        tok = tok[len("bearer "):].strip()
    if tok.startswith("FlyV1 "):
        core = tok[len("FlyV1 "):].strip()
        return [tok, f"Bearer {core}"]          # already FlyV1-scheme; try that first
    return [f"Bearer {tok}", f"FlyV1 {tok}"]     # bare token; Bearer then FlyV1


def _headers(auth: str) -> dict:
    return {"Authorization": auth, "Content-Type": "application/json"}


def _request(client, method: str, url: str, **kw):
    """Issue a request, retrying across auth schemes on 401/403.

    Returns the first response that isn't an auth failure, or the last auth
    failure if every scheme is rejected (so the caller can surface the status).
    """
    last = None
    for auth in _auth_variants():
        r = client.request(method, url, headers=_headers(auth), **kw)
        if r.status_code not in (401, 403):
            return r
        last = r
    return last


def _list_machines(client, app: str) -> list[dict]:
    r = _request(client, "GET", f"{_MACHINES_BASE}/apps/{app}/machines")
    if r.status_code == 404:
        raise RuntimeError(f"app '{app}' not found (404)")
    if r.status_code in (401, 403):
        raise RuntimeError(f"unauthorized ({r.status_code}) — check FLY_API_TOKEN_READ scope")
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("machines", [])


def _machine_size(m: dict) -> tuple[str, int, int]:
    """Return (cpu_kind, cpus, memory_mb) from a machine's guest config."""
    guest = (m.get("config") or {}).get("guest") or {}
    return (
        str(guest.get("cpu_kind") or "shared"),
        int(guest.get("cpus") or 1),
        int(guest.get("memory_mb") or 256),
    )


def _estimate_machine_cost(cpu_kind: str, cpus: int, memory_mb: int) -> float | None:
    """Rough always-on monthly USD for one machine, or None if unpriceable."""
    preset = _PRESET_PRICE.get((cpu_kind, cpus))
    if preset is None:
        return None  # performance CPUs / unknown preset — don't guess
    base, included_mb = preset
    extra_gb = max(0, memory_mb - included_mb) / 1024.0
    return base + extra_gb * _EXTRA_RAM_PER_GB


def _fleet_health(args: dict, ctx: Context) -> str:
    if not _token():
        return ("[infra not configured] Set FLY_API_TOKEN_READ (a Fly read/deploy "
                "token) and WATCHED_FLY_APPS to report fleet health.")
    apps = _watched_apps()
    if not apps:
        return "[infra] No apps to watch. Set WATCHED_FLY_APPS (comma-separated app names)."

    import httpx

    lines: list[str] = []
    with httpx.Client(timeout=_TIMEOUT) as client:
        for app in apps:
            try:
                machines = _list_machines(client, app)
            except Exception as e:  # noqa: BLE001 — report per-app, keep going
                lines.append(f"- {app}: error — {e}")
                continue
            if not machines:
                lines.append(f"- {app}: no machines")
                continue
            states: dict[str, int] = {}
            for m in machines:
                st = str(m.get("state") or "unknown")
                # A "stopped" machine is intentionally parked (you stopped it, or Fly
                # auto-stopped an idle one) — that's idle, not unhealthy.
                st = "idle" if st == "stopped" else st
                states[st] = states.get(st, 0) + 1
            started = states.get("started", 0)
            total = len(machines)
            expected = _expected_running(app)
            summary = ", ".join(f"{n} {s}" for s, n in sorted(states.items()))
            flag = "OK" if started >= expected else "DEGRADED"
            detail = summary if flag == "OK" else f"{started}/{expected} up — {summary}"
            lines.append(f"- {app}: {flag} — {total} machine(s): {detail}")
    return "Fly fleet health:\n" + "\n".join(lines)


def _fleet_spend(args: dict, ctx: Context) -> str:
    if not _token():
        return ("[infra not configured] Set FLY_API_TOKEN_READ to report spend.")

    import httpx

    out: list[str] = []

    # 1) Credit balance — a real dollar figure, stable GraphQL field.
    query = ("{ personalOrganization { name creditBalance creditBalanceFormatted } }")
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = _request(client, "POST", _GRAPHQL_URL, json={"query": query})
            r.raise_for_status()
            payload = r.json()
        if payload.get("errors"):
            out.append(f"(credit balance unavailable: {payload['errors']})")
        else:
            org = ((payload.get("data") or {}).get("personalOrganization")) or {}
            bal = org.get("creditBalanceFormatted") or (
                f"${org.get('creditBalance', 0)/100:.2f}" if org.get("creditBalance") is not None else "?"
            )
            out.append(f"Fly credit balance: {bal} (org: {org.get('name', '?')})")
    except Exception as e:  # noqa: BLE001
        out.append(f"(credit balance unavailable: {e})")

    # 2) Estimated monthly run-rate from machine sizes (health data).
    est_total = 0.0
    priced = 0
    unpriced = 0
    apps = _watched_apps()
    if apps:
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                for app in apps:
                    try:
                        machines = _list_machines(client, app)
                    except Exception:  # noqa: BLE001
                        continue
                    for m in machines:
                        if str(m.get("state")) != "started":
                            continue  # only running machines accrue cost
                        cost = _estimate_machine_cost(*_machine_size(m))
                        if cost is None:
                            unpriced += 1
                        else:
                            est_total += cost
                            priced += 1
            note = f"~${est_total:.2f}/mo estimated run-rate ({priced} running machine(s) priced"
            note += f", {unpriced} unpriced)" if unpriced else ")"
            out.append(note)
        except Exception as e:  # noqa: BLE001
            out.append(f"(run-rate estimate unavailable: {e})")

    out.append("Note: run-rate is a rough estimate from machine specs; Fly has no "
               "stable spend API — see the Fly dashboard for the authoritative bill.")
    return "\n".join(out)


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "fleet_health",
            "description": "Report the up/down status of the user's hosted Fly.io apps "
                           "(per-app machine states). Read-only.",
            "input_schema": {"type": "object", "properties": {}},
        },
        _fleet_health,
    )
    reg.register(
        {
            "name": "fleet_spend",
            "description": "Report the user's Fly.io credit balance and a rough estimated "
                           "monthly run-rate for the hosted fleet. Read-only. Note: Fly has "
                           "no exact spend API; the run-rate is an estimate.",
            "input_schema": {"type": "object", "properties": {}},
        },
        _fleet_spend,
    )
