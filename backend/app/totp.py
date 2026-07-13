"""TOTP second factor for flight booking (flight-booking TDD §2.3).

The gate (an explicit "confirm") proves the caller said the word. It does NOT
prove the caller is who they claim to be — voice auth is caller ID, which is
spoofable, and even on SMS/email a compromised session could type "confirm".
TOTP is the only control that actually beats that: it requires *possession* of
the enrolled device, not merely knowledge of a phone number or a whitelist
entry.

Chosen over SMS (TDD's still-open question, resolved here): no carrier
dependency, no A2P 10DLC blocker, immune to SIM swap. One QR scan sets it up.
"""

from __future__ import annotations

import re

import pyotp

from app.config import settings

# One 30s TOTP step of drift either side. STT + a person reading digits off a
# phone screen takes a few seconds; without slack a code entered right at a
# step boundary would spuriously fail. Wider than this starts trading away the
# thing TOTP buys you.
_VALID_WINDOW = 1


def totp_configured() -> bool:
    return bool(settings.totp_secret)


def provisioning_uri(account_name: str = "JARVIS booking") -> str:
    """QR-code URI for enrolling an authenticator app. Setup-time only."""
    return pyotp.TOTP(settings.totp_secret).provisioning_uri(
        name=account_name, issuer_name="JARVIS"
    )


def normalize_code(raw: str) -> str:
    """STT will mangle digits (TDD §2.3 implementation notes). Strip spaces,
    punctuation, and words; keep only digits. Deliberately does NOT attempt
    spelled-out-number parsing beyond simple digit words, and does NOT do
    fuzzy/near-miss matching — a wrong code is a wrong code."""
    s = raw.strip().lower()
    words = {
        "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    }
    tokens = re.findall(r"[a-z]+|\d", s)
    digits = "".join(words.get(t, t) for t in tokens if t in words or t.isdigit())
    return digits


def verify(code: str) -> bool:
    """Verify a (possibly STT-mangled) code against the current TOTP window.
    Returns False outright if TOTP isn't configured — fail closed, never
    silently accept."""
    if not settings.totp_secret:
        return False
    normalized = normalize_code(code)
    if not normalized:
        return False
    return pyotp.TOTP(settings.totp_secret).verify(normalized, valid_window=_VALID_WINDOW)
