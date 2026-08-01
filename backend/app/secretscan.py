"""Secret scanner — a pure classifier for text about to be written to GitHub.

TDD #3 (`docs/TDD-repo-scaffolding.md` §4.5, §9). Built and tested BEFORE any
writer exists, which is the point rather than an accident of scheduling: a
writer built first is a writer that works without the scanner, and then the
scanner is an addition rather than a precondition. That ordering stopped being
prudence and became a safety property when public-by-default repo visibility was
ratified (§11.3) — under a private default, a scaffold committed before the
scanner leaked nothing; under a public default it can.

WHAT THIS MODULE IS
    A pure function. Text in, findings out. No network, no file reads, no
    logging, no GitHub client, no opinion about what to do next.

WHAT IT DELIBERATELY IS NOT
    It does not abort anything. Detection and enforcement are kept apart so the
    classifier is trivially testable offline and the refusal is asserted at the
    call site, where it can be seen. The writer (TDD #3 step 4) is what treats a
    non-empty result as a hard stop.

THE NON-ECHO INVARIANT
    A finding carries a pattern name and a location and NEVER the matched value.
    A scanner that leaks the secret into its own finding, log line, or error
    message has reintroduced the exposure it exists to prevent — and this one's
    output is destined for `github_write_log`, which is stored in the database
    and rendered on the status page, so a leak there leaks twice. That is not
    hypothetical: `_scrub()` on the AutoRemote path leaked a key into a
    transcript on 2026-07-21 by matching only the raw form of a value that was
    form-encoded on the wire. Asserted in test, not trusted.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretFinding:
    """One suspected secret. Carries WHERE and WHAT KIND, never the value.

    `char_span` is a (start, end) offset pair within the reported LINE, not
    within the whole document — a line number plus an offset into that line is
    what a human needs to go look, and it stays correct regardless of how the
    document is later reflowed or chunked.
    """

    pattern_name: str
    line: int                    # 1-based
    char_span: tuple[int, int]   # (start, end) within that line


# ── Known token shapes ───────────────────────────────────────────────────────
# Each is NAMED so a hit reports which credential type was seen. The name is
# what makes a refusal actionable ("there is a Twilio SID in section 4") without
# the message having to quote the thing it is refusing to publish.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_pat",      re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    # Must be tried after github_pat_: `ghp_` would otherwise not match it, but
    # keeping the more specific pattern first documents the intent.
    ("github_token",    re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("duffel_token",    re.compile(r"duffel_[A-Za-z0-9_\-]{10,}")),
    ("anthropic_key",   re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("slack_bot_token", re.compile(r"xoxb-[A-Za-z0-9\-]{10,}")),
    ("google_api_key",  re.compile(r"AIza[A-Za-z0-9_\-]{35}")),
    ("twilio_sid",      re.compile(r"AC[0-9a-fA-F]{32}")),
    ("private_key",     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

# ── Entropy heuristic ────────────────────────────────────────────────────────
# Deliberately conservative. TDD §10 says tune from real refusals rather than
# from imagination, so these start strict-about-what-they-flag and are module
# constants (promote to runtime settings only if real use proves them noisy).
#
# WHY 4.5 BITS/CHAR, specifically — it is not a round number picked by feel.
# Shannon entropy per character is capped by alphabet size: a pure-hex string
# cannot exceed log2(16) = 4.0 bits/char NO MATTER HOW LONG IT IS. So a floor
# above 4.0 excludes every hex digest by construction — git SHAs, MD5/SHA1/SHA256
# hashes, hex UUIDs — which are exactly the innocuous high-looking strings a
# design document is full of. A mixed-case alphanumeric secret draws on ~62
# symbols and lands near 5.0-5.3 at these lengths, comfortably above the floor.
# The gap between 4.0 and 5.0 is what the threshold sits in.
_ENTROPY_MIN_LEN = 32
_ENTROPY_THRESHOLD = 4.5

# Characters that can appear inside a credential-looking blob. Splitting on
# anything else keeps ordinary prose from being scored as one long token.
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_\-]{%d,}" % _ENTROPY_MIN_LEN)

# TODO(§4.5): value-match against known Fly secret values ("anything matching the
# values of known Fly secret names, if resolvable"). NOT built here, and the
# omission is deliberate rather than an oversight: it requires reading the live
# secret environment, which means this module would hold real secret VALUES in
# memory in order to compare against them — turning a pure offline classifier
# into something with a secret-shaped attack surface of its own. That is a
# judgment call with a real argument on both sides and it is filed as a TDD open
# question, not decided in passing. Prefix + entropy + key-header covers the
# exposure that motivated the scanner.


def _shannon_entropy(s: str) -> float:
    """Bits of entropy per character. 0.0 for an empty string."""
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def scan_for_secrets(text: str) -> list[SecretFinding]:
    """Return findings; an empty list means clean.

    No I/O, no logging of matches. Findings are ordered by line, then by offset
    within the line, so a report reads top-to-bottom like the document does.
    """
    if not text:
        return []

    findings: list[SecretFinding] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        # Spans claimed by a named pattern, so the entropy pass does not report
        # the same credential a second time under a vaguer name. A known token
        # type is strictly more useful than "high entropy string".
        claimed: list[tuple[int, int]] = []

        for name, pattern in _PATTERNS:
            for m in pattern.finditer(line):
                findings.append(
                    SecretFinding(pattern_name=name, line=lineno, char_span=(m.start(), m.end()))
                )
                claimed.append((m.start(), m.end()))

        for m in _TOKEN_RE.finditer(line):
            if any(m.start() < end and start < m.end() for start, end in claimed):
                continue
            if _shannon_entropy(m.group()) >= _ENTROPY_THRESHOLD:
                findings.append(
                    SecretFinding(
                        pattern_name="high_entropy", line=lineno, char_span=(m.start(), m.end())
                    )
                )

    findings.sort(key=lambda f: (f.line, f.char_span[0]))
    return findings
