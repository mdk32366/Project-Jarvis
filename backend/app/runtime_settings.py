"""Runtime settings overlay (health TDD §7, roadmap R2).

Behavior that the owner tunes — briefing time, quiet-hours window, outbound-call
toggles — must be *visible and changeable without a redeploy*. This module is
that overlay: a bounded allow-list of behavioral keys, each with a DB override
(the `runtime_settings` table) that wins over the env/`Settings` default.

Two hard rules, both load-bearing:

1. **NEVER a secret.** `ALLOWED_KEYS` is the enforcement boundary. `get_effective`
   / `set_effective` reject any key not on it — an API key or token can never be
   read or written through here, no matter what the caller asks for.

2. **The reader must read the overlay.** Every runtime reader of an allow-list
   key uses `get_effective(db, key)` — never `settings.key` directly. A UI that
   writes a row nobody reads is the exact silent-no-op fragility this removes, so
   the reader switch ships in the same change as the writer.

`get_effective` does NOT mutate the `@lru_cache` `settings` singleton; it reads
the override and falls back to the singleton's default, leaving it untouched.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ActionAudit, RuntimeSetting

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Key:
    type: str                     # "bool" | "int"
    safety_critical: bool = False  # requires explicit confirm to change; always audited
    min: int | None = None
    max: int | None = None


# The allow-list. Keys must exist on `Settings` (that's the default source).
# `outbound_calls_enabled` and `max_outbound_calls_per_hour` gate real phone
# calls, so they are safety-critical: a change needs an explicit confirm and is
# always written to the audit trail.
ALLOWED_KEYS: dict[str, _Key] = {
    "briefing_enabled":            _Key("bool"),
    "briefing_by_phone":           _Key("bool"),
    "briefing_hour":               _Key("int", min=0, max=23),
    "briefing_minute":             _Key("int", min=0, max=59),
    "quiet_hours_start":           _Key("int", min=0, max=23),
    "quiet_hours_start_minute":    _Key("int", min=0, max=59),
    "quiet_hours_end":             _Key("int", min=0, max=23),
    "quiet_hours_end_minute":      _Key("int", min=0, max=59),
    "outbound_calls_enabled":      _Key("bool", safety_critical=True),
    "max_outbound_calls_per_hour": _Key("int", safety_critical=True, min=1, max=20),
}

_TRUE = {"1", "true", "yes", "on", "t"}


def _coerce(spec: _Key, raw: str):
    """Text (as stored) -> the key's declared Python type."""
    if spec.type == "bool":
        return str(raw).strip().lower() in _TRUE
    return int(raw)


def _validate(key: str, spec: _Key, value) -> object:
    """Parse+bound an incoming value for a write. Raises ValueError on anything
    unparseable or out of range — a bad override must be refused, not clamped
    silently into a surprising state."""
    if spec.type == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in _TRUE
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer, got {value!r}")
    if spec.min is not None and v < spec.min:
        raise ValueError(f"{key} must be >= {spec.min}, got {v}")
    if spec.max is not None and v > spec.max:
        raise ValueError(f"{key} must be <= {spec.max}, got {v}")
    return v


def get_effective(db: Session, key: str):
    """The value in force right now: the DB override if one exists, else the
    env/`Settings` default. Fails closed on anything not on the allow-list, so a
    secret can never be read through this path."""
    spec = ALLOWED_KEYS.get(key)
    if spec is None:
        raise KeyError(f"{key!r} is not a runtime-overridable setting")
    row = db.get(RuntimeSetting, key)
    if row is None or row.value == "":
        return getattr(settings, key)
    try:
        return _coerce(spec, row.value)
    except (TypeError, ValueError):
        # A corrupt row must never break a runtime reader — fall back to default.
        log.warning("runtime_settings: corrupt value for %s (%r); using default", key, row.value)
        return getattr(settings, key)


def set_effective(db: Session, key: str, value, *, confirm: bool = False,
                  actor: str = "admin", channel: str = "web"):
    """Write an override. Returns the coerced effective value.

    - Unknown key -> ValueError (fail closed; never a secret).
    - Safety-critical key without `confirm` -> PermissionError.
    - Out-of-range / unparseable value -> ValueError.
    Every successful change is written to `actions_audit` (health TDD §8.2:
    safety-critical settings are gated + audited)."""
    spec = ALLOWED_KEYS.get(key)
    if spec is None:
        raise ValueError(f"{key!r} is not a runtime-overridable setting")
    if spec.safety_critical and not confirm:
        raise PermissionError(f"{key} is safety-critical; pass confirm=true to change it")

    coerced = _validate(key, spec, value)
    stored = "true" if coerced is True else "false" if coerced is False else str(coerced)

    row = db.get(RuntimeSetting, key)
    if row is None:
        db.add(RuntimeSetting(key=key, value=stored))
    else:
        row.value = stored
    db.add(ActionAudit(
        channel=channel, actor=actor, tool="set_runtime_setting",
        arguments=json.dumps({"key": key, "value": coerced}),
        result=f"{key} = {coerced}",
        status="confirmed" if spec.safety_critical else "ok",
    ))
    db.commit()
    log.info("runtime setting %s set to %r by %s", key, coerced, actor)
    return coerced


def get_all_effective(db: Session) -> dict[str, dict]:
    """Every allow-list key with its effective value and where it came from
    (`override` vs `default`) — the read model for the Admin settings surface."""
    out: dict[str, dict] = {}
    rows = {r.key: r for r in db.query(RuntimeSetting).all()}
    for key, spec in ALLOWED_KEYS.items():
        row = rows.get(key)
        overridden = row is not None and row.value != ""
        out[key] = {
            "value": get_effective(db, key),
            "source": "override" if overridden else "default",
            "type": spec.type,
            "safety_critical": spec.safety_critical,
        }
    return out
