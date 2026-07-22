"""Morning briefing (Phase 2).

Assembles the sections we have live data for today — schedule and the week ahead
(Google Calendar), weather and marine (NWS), commute (Google Maps), open tasks,
travel (recorded trips), news (Tavily), hosted-app health and spend (Fly), and
local-network status (Tailscale) — then has the LLM compose a concise, warm
briefing in the principal's voice. Unconfigured or failing sources are simply
omitted, never announced. Delivered on demand (/api/briefing) or on a daily
schedule (the worker enqueues a `morning_briefing` job → emails the owner).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.handlers.base import Context
from app.llm import create_message
from app.memory import build_system_preamble
from app.models import Memory

log = logging.getLogger(__name__)

# Sources not yet integrated — shown so the briefing is honest about coverage.
# This is hand-maintained prose that NOTHING fails on when it goes stale, and it
# has now been wrong twice: "Weekend & travel" (audit L12 — a live ## Travel
# section is now assembled from recorded trips) and "Project status" (shipped in
# PR #40 / migration 0024, tools live on the secretary). Both listed a feature the
# brief itself already carries. A candidate for deriving from configuration;
# deliberately not done here to keep this PR scoped.
_PENDING_SECTIONS = ["Upcoming bills"]

# ── NWS / external API constants ──────────────────────────────────────────────
# Nominatim usage policy requires a meaningful User-Agent identifying the app.
# A bare Python/httpx default will be rate-limited or silently blocked.
_NWS_HEADERS = {
    "User-Agent": "JARVIS-briefing/1.0 (personal assistant; github.com/mdk32366/Project-Jarvis)"
}
_NWS_TIMEOUT = 10.0

# News via Tavily: capped at 15s, not the full 25s used by the search tool.
# This is the one section mediated by an external search service rather than a
# direct API call; it should not stall the brief if Tavily is slow.
_TAVILY_NEWS_TIMEOUT = 15.0

# Marine zone constants — confirmed via NWS API, 2026-07-14.
# Methodology: hit api.weather.gov/points/48.50,-122.60 (open water near Anacortes /
# Skyline Marina) → type=Marine, forecastZone=PZZ133.
# PZZ133 "Northern Inland Waters Including The San Juan Islands" — Anacortes and
# Rosario Strait lie inside this zone (boundary 122.43–123.09°W, 48.59–49.00°N).
# PZZ132 "East Entrance U.S. Waters Strait Of Juan De Fuca" — the Strait immediately
# west of the islands; relevant for passages past Deception Pass or into the Strait.
# PZZ134 (Admiralty Inlet) omitted: south of Anacortes, not on routine routes from
# Skyline Marina to the San Juans or the Strait — would add noise to the brief.
_MARINE_ZONES = ["PZZ133", "PZZ132"]
# Reference point for the marine gridpoint forecast: open water near Anacortes.
# Verified 2026-07-14: api.weather.gov/points/48.50,-122.60 → type=Marine, zone=PZZ133.
_MARINE_REF_LAT, _MARINE_REF_LON = 48.50, -122.60

# Suppress traffic when _get_traffic reports this exact phrase (delay < 120 s).
# The phrase lives in maps._get_traffic — the goal is no noise on clear commutes.
_TRAFFIC_QUIET = "Traffic is light."


def _safe(label: str, fn):
    """Run a data-source call; never let one failing source sink the briefing."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("briefing source '%s' failed: %s", label, e)
        return f"({label} unavailable right now: {e})"


def _nws_weather(address: str) -> str:
    """NWS point forecast for a US address. No API key required.

    Three-step flow: geocode via Nominatim → NWS /points → gridpoint forecast.
    Returns "" when the address is blank or any step fails — the caller's _safe()
    wrapper provides the outer catch for unexpected exceptions.
    """
    if not address:
        return ""

    import httpx

    # Step 1: geocode address → lat/lon via Nominatim (OpenStreetMap, no key).
    try:
        r = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1},
            headers=_NWS_HEADERS,
            timeout=_NWS_TIMEOUT,
        )
        locs = r.json()
        if not locs:
            log.warning("Nominatim: no result for %r", address)
            return ""
        lat = float(locs[0]["lat"])
        lon = float(locs[0]["lon"])
    except Exception as e:
        log.warning("weather geocode failed for %r: %s", address, e)
        return ""

    # Step 2: NWS /points → gridpoint + forecast URL.
    try:
        r = httpx.get(
            f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}",
            headers=_NWS_HEADERS,
            timeout=_NWS_TIMEOUT,
        )
        forecast_url = r.json()["properties"]["forecast"]
    except Exception as e:
        log.warning("NWS points lookup failed for %.4f,%.4f: %s", lat, lon, e)
        return ""

    # Step 3: gridpoint forecast → first two periods (today + tonight).
    try:
        r = httpx.get(forecast_url, headers=_NWS_HEADERS, timeout=_NWS_TIMEOUT)
        periods = r.json()["properties"]["periods"][:2]
        return "\n".join(f"{p['name']}: {p['detailedForecast']}" for p in periods)
    except Exception as e:
        log.warning("NWS forecast fetch failed: %s", e)
        return ""


def _nws_marine() -> str:
    """Active marine advisories + forecast for the Anacortes/San Juan Islands corridor.

    Checks PZZ133 and PZZ132 for Small Craft Advisories, Gale Warnings, and Storm
    Warnings, surfacing them prominently before the condition text. That's the
    actionable content — a quiet-winds forecast is background; an SCA changes the day.

    Internal concurrency: the two zone alert checks and the gridpoint /points lookup
    are all independent — they run in a small pool so latency is max(10s, 10s, 10s)
    = 10s rather than 30s sequential. The final forecast fetch depends on the /points
    URL and stays sequential after the pool.
    """
    import httpx

    def _fetch_alerts(zone: str) -> list[str]:
        lines: list[str] = []
        try:
            r = httpx.get(
                f"https://api.weather.gov/alerts/active?zone={zone}",
                headers=_NWS_HEADERS,
                timeout=_NWS_TIMEOUT,
            )
            for feat in r.json().get("features", []):
                props = feat["properties"]
                event = props.get("event", "")
                if any(kw in event for kw in ("Small Craft", "Gale", "Storm", "Hurricane")):
                    headline = props.get("headline") or event
                    lines.append(f"ADVISORY: {headline}")
        except Exception as e:
            log.warning("NWS marine alert check failed for %s: %s", zone, e)
        return lines

    def _fetch_points_url() -> str | None:
        try:
            r = httpx.get(
                f"https://api.weather.gov/points/{_MARINE_REF_LAT},{_MARINE_REF_LON}",
                headers=_NWS_HEADERS,
                timeout=_NWS_TIMEOUT,
            )
            return r.json()["properties"]["forecast"]
        except Exception as e:
            log.warning("NWS marine points lookup failed: %s", e)
            return None

    # Alert checks and the gridpoint resolution are mutually independent.
    with ThreadPoolExecutor(max_workers=3) as pool:
        alert_futures = {z: pool.submit(_fetch_alerts, z) for z in _MARINE_ZONES}
        f_points = pool.submit(_fetch_points_url)

    sca_lines: list[str] = []
    for zone in _MARINE_ZONES:
        sca_lines.extend(alert_futures[zone].result())

    forecast_lines: list[str] = []
    forecast_url = f_points.result()
    if forecast_url:
        try:
            r = httpx.get(forecast_url, headers=_NWS_HEADERS, timeout=_NWS_TIMEOUT)
            periods = r.json()["properties"]["periods"][:2]
            for p in periods:
                temp = f"{p['temperature']}°{p.get('temperatureUnit', 'F')}"
                forecast_lines.append(
                    f"{p['name']}: {p['shortForecast']}, {p['windSpeed']} {p['windDirection']}, {temp}."
                )
        except Exception as e:
            log.warning("NWS marine forecast failed: %s", e)

    all_lines = sca_lines + forecast_lines
    return "\n".join(all_lines) if all_lines else ""


def _traffic_brief(db) -> str:
    """Home → work commute. Returns "" when unconfigured or delay is not meaningful.

    Suppresses the section entirely when _get_traffic reports "Traffic is light."
    (delay < 120 s). No delay reported every morning is noise; a 25-minute delay
    IS the reason you read the briefing — that's what gets included.
    """
    if not (settings.google_maps_api_key
            and settings.owner_home_address
            and settings.owner_work_address):
        return ""

    from app.handlers.maps import _get_traffic

    ctx = Context(db=db, channel="briefing", actor="system", thread_key="briefing")
    result = _get_traffic(
        {"origin": settings.owner_home_address, "destination": settings.owner_work_address},
        ctx,
    )
    # Surface ONLY a genuine traffic report. Besides a real commute line and the
    # "Traffic is light." quiet case, _get_traffic also returns guidance strings
    # ("No route found…", "Where to?…", "[maps not configured]…") — none of which
    # belong in the brief as a "## Traffic" section (that is what produced a
    # confusing traffic line). A real OK result always carries this ETA phrase;
    # the guidance strings never do. Anything without it, or a quiet commute, is
    # "no traffic to report" → omitted.
    if _TRAFFIC_QUIET in result or "puts you there about" not in result:
        return ""
    return result


def _news_brief() -> str:
    """2–3 top headlines via Tavily, with a hard 15-second timeout.

    Uses Tavily directly (not via the web_search tool handler) to control the
    timeout independently and to format output for human reading rather than
    for LLM consumption — no UNTRUSTED fence, just the answer + headlines.
    """
    if not settings.tavily_api_key:
        return ""

    import httpx

    try:
        with httpx.Client(timeout=_TAVILY_NEWS_TIMEOUT) as client:
            r = client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": "top news today",
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 3,
                    "topic": "news",
                    "days": 1,
                },
            )
        if r.status_code != 200:
            log.warning("Tavily news returned %d", r.status_code)
            return ""
        data = r.json()
    except Exception as e:
        log.warning("news brief failed: %s", e)
        return ""

    answer = (data.get("answer") or "").strip()
    results = data.get("results") or []
    lines: list[str] = []
    if answer:
        lines.append(answer[:300] + ("..." if len(answer) > 300 else ""))
    for res in results[:3]:
        title = (res.get("title") or "").strip()
        if title:
            lines.append(f"- {title}")
    return "\n".join(lines) if lines else ""


def gather_context(db: Session) -> str:
    """Collect raw material from every live source into one text block.

    All external-API sources are independent — they run concurrently in a thread
    pool so wall-clock time is bounded by the SLOWEST single source rather than
    their sum.

    Threading model
    ---------------
    _safe() catches all exceptions inside each worker thread and returns the
    graceful-degradation string, so .result() on any future is always safe (never
    raises).  DB-bound sources (tasks, trips, memory) stay on the main thread because
    the SQLAlchemy session is not thread-safe.  They take <10 ms and finish while
    the HTTP futures are still in-flight, so they add nothing to the critical path.
    The HTTP-bound handlers (calendar, infra, weather, marine, traffic, news,
    tailscale) do not exercise ctx.db in their normal read paths.
    """
    from app.handlers.scheduling import _calendar_lookup
    from app.handlers.infra import _fleet_health, _fleet_spend
    from app.handlers.tasks import open_task_summary
    from app.handlers.travel import _list_trips
    from app.handlers.tailscale import _tailscale_status

    ctx = Context(db=db, channel="briefing", actor="system", thread_key="briefing")

    with ThreadPoolExecutor(max_workers=10) as executor:
        f_today     = executor.submit(_safe, "calendar",  lambda: _calendar_lookup({"range": "today"}, ctx))
        f_week      = executor.submit(_safe, "calendar",  lambda: _calendar_lookup({"range": "this week"}, ctx))
        # Portfolio is intentionally NOT gathered. Alpaca is unconfigured and
        # _get_portfolio only ever returns a "[demo mode]" sentinel, so it has no
        # path to the reader — gathering it spent a thread on content that was
        # always filtered out. The tool stays live on the finance handler/agent;
        # restoring it in the brief is re-adding one executor.submit(_safe,
        # "portfolio", ...) here plus a "## Portfolio" section block below.
        f_health    = executor.submit(_safe, "infra",     lambda: _fleet_health({}, ctx))
        f_spend     = executor.submit(_safe, "infra",     lambda: _fleet_spend({}, ctx))
        f_weather   = executor.submit(_safe, "weather",   lambda: _nws_weather(settings.owner_home_address))
        f_marine    = executor.submit(_safe, "marine",    lambda: _nws_marine())
        f_traffic   = executor.submit(_safe, "traffic",   lambda: _traffic_brief(db))
        f_news      = executor.submit(_safe, "news",      lambda: _news_brief())
        # Local network / the house. ONLY Tailscale is wired here. Proxmox
        # (get_node_status) and Uptime Kuma (get_service_health) are deliberately
        # absent: they are Phase-1 STUBS that return fabricated fixtures with no
        # config gate — see the module docstring in app/handlers/netstatus.py
        # ("PHASE 1: THESE ARE STUBS ... the LAN isn't reachable from Fly").
        # Submitting them would report a fake-offline node to the owner every
        # morning. Re-add each as one executor.submit + one predicate entry once it
        # speaks to a real backend at LAN migration.
        f_tailscale = executor.submit(_safe, "tailscale", lambda: _tailscale_status({}, ctx))

        # DB-bound sources: safe on the main thread, done before futures finish.
        tasks    = _safe("tasks",  lambda: open_task_summary(db))
        trips    = _safe("trips",  lambda: _list_trips({}, ctx))
        facts_raw = _safe("memory", lambda: db.execute(
            select(Memory).order_by(Memory.created_at.desc()).limit(5)
        ).scalars().all())

    today     = f_today.result()
    week      = f_week.result()
    health    = f_health.result()
    spend     = f_spend.result()
    weather   = f_weather.result()
    marine    = f_marine.result()
    traffic   = f_traffic.result()
    news      = f_news.result()
    tailscale = f_tailscale.result()

    if isinstance(facts_raw, str):
        fact_lines = facts_raw
    else:
        fact_lines = "\n".join(f"- {m.content}" for m in facts_raw) or "(none)"

    sections = [f"## Today's calendar\n{today}", f"## This week\n{week}"]
    # Weather — silently omitted if unconfigured (no home address) or NWS fails.
    # A _safe error string starts with "(" and is excluded by that guard.
    if weather and not weather.startswith("("):
        sections.append(f"## Weather\n{weather}")
    # Marine — Anacortes/San Juan Islands corridor (PZZ133 + PZZ132).
    # SCA lines are prefixed "ADVISORY:" and sorted first by _nws_marine().
    if marine and not marine.startswith("("):
        sections.append(f"## Marine\n{marine}")
    # Traffic — home → work commute. Present only when delay is meaningful (>=120 s).
    # _traffic_brief returns "" for light traffic, so _safe returns "" too.
    if traffic and not traffic.startswith("("):
        sections.append(f"## Traffic\n{traffic}")
    # Open tasks: always worth surfacing — this is the list JARVIS owns.
    if isinstance(tasks, str) and tasks and not tasks.startswith("No open tasks"):
        sections.append(f"## Open tasks\n{tasks}")
    # Upcoming trips (captured from confirmation emails).
    if isinstance(trips, str) and trips and not trips.startswith("No trips on file"):
        sections.append(f"## Travel\n{trips}")
    # (Portfolio section removed — see the gather site above for why and what
    # restores it. The finance tool itself is untouched.)
    # News — 2-3 headlines via Tavily. Omitted when unconfigured or slow/failing.
    if news and not news.startswith("("):
        sections.append(f"## News\n{news}")
    sections.append(f"## Recent notes/memory\n{fact_lines}")
    # Hosted apps — the Fly fleet. Suppress the _safe error shape "(" and any infra
    # bracket-sentinel "[" ("[infra not configured]", "[infra] No apps to watch")
    # so a failing or unconfigured source never leaks into the brief — matching how
    # every section above guards its own "(".
    if isinstance(health, str) and health and not health.startswith(("(", "[")):
        block = health
        if isinstance(spend, str) and spend and not spend.startswith(("(", "[")):
            block += "\n\n" + spend
        sections.append(f"## Hosted apps\n{block}")
    # Local network — the house, kept DISTINCT from ## Hosted apps (Fly) so "is
    # anything down" is never ambiguous about where. Include a part only when it is
    # a non-empty string starting with neither "(" (the _safe error shape) nor "["
    # (an unconfigured-integration sentinel, e.g. "[tailscale not configured]").
    # Built as a filtered list so partial configuration still renders and re-adding
    # Proxmox/Kuma later (see the gather site) is one more entry here.
    net_parts = [
        p for p in (tailscale,)
        if isinstance(p, str) and p and not p.startswith(("(", "["))
    ]
    if net_parts:
        sections.append("## Local network\n" + "\n\n".join(net_parts))
    sections.append("## Not yet connected\n" + ", ".join(_PENDING_SECTIONS))
    return "\n\n".join(sections)


_BRIEF_INSTRUCTIONS = """
Write a concise morning briefing for your principal, in their voice and preferences.
Keep it tight and scannable — short lines or compact bullets, no filler.

Use this fixed section order every day, so the brief can be skimmed and the eye
learns where to look:
  1. Today's schedule
  2. This week
  3. Weather, then Marine
  4. Traffic
  5. Open tasks, then Travel
  6. News
  7. Systems — hosted apps and local network together

Rules:
- The data below is the ONLY source. Write about a section only if it appears
  there. If a section (traffic, weather, marine, news, …) is NOT in the data, say
  nothing about it at all — do not note that it's missing, unavailable, or not
  checked, and never speculate about why. A silent omission is correct; a line
  explaining an absence ("traffic wasn't available") is a bug.
- Systems is exception-first: report what is WRONG. When everything is healthy,
  say so in one short line rather than enumerating the healthy components — a list
  of green trains the eye to skip the section where red will eventually appear.
- Do not invent anything. End with a brief, useful nudge only if warranted.
"""


def compose_briefing(db: Session) -> str:
    data = gather_context(db)
    try:
        # §4.2 forced-first-call pattern: ground the LLM in real current time
        # before it reasons about "today", "this week", or anything date-relative.
        # Without this, the model infers "now" from its training data — which is
        # what produced the wrong-time briefing content (the scheduler's own clock
        # was correct; the LLM composing the spoken text was not).
        from app.handlers.datetime_tools import _get_current_datetime
        from app.handlers.base import Context
        _ctx = Context(db=db, channel="briefing", actor="system", thread_key="briefing")
        dt_ctx = _get_current_datetime({}, _ctx)
        grounded_data = f"[Current date/time: {dt_ctx}]\n\n{data}"

        system = build_system_preamble(db) + "\n" + _BRIEF_INSTRUCTIONS
        resp = create_message(system=system, messages=[{"role": "user", "content": grounded_data}])
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        text = "\n".join(parts).strip()
        if text:
            return text
    except Exception as e:  # noqa: BLE001
        log.error("briefing compose failed: %s", e)
        # Prefix with a degraded-sentinel so the CALL path won't read this error
        # and raw dump aloud (audit M3). The email path still sends it verbatim so
        # the owner can see what happened.
        return f"(briefing failed) Could not generate the written briefing ({e}).\n\nHere is the raw data:\n\n{data}"
    return "(no briefing generated)\n\n" + data


# Degraded compose_briefing outputs — real prose never starts with these. The
# spoken briefing_call path checks this so it never reads an error/empty-marker
# and raw data dump aloud; the email path sends them so the owner still sees them.
_DEGRADED_PREFIXES = ("(no briefing", "(briefing failed")


def is_speakable_briefing(text: str) -> bool:
    """True only for a real, composed briefing — not an empty/failed sentinel."""
    return bool(text) and not text.lstrip().startswith(_DEGRADED_PREFIXES)


def send_briefing(db: Session) -> str:
    """Compose and email the briefing to the owner. Returns a status string."""
    from app.notifier import send_email

    to = settings.owner_email_resolved
    if not to:
        return "no owner email configured"
    text = compose_briefing(db)
    send_email(to, "Your JARVIS morning briefing", text)
    return f"briefing emailed to {to}"
