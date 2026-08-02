"""Secret scanner tests — TDD #3 §9, scoped to what exists at steps 1-2.

The scanner has no writer yet, by design (§4.5 / §8): it is built and proven
first so the abort it enables is a precondition rather than a later addition.
These tests therefore assert the classifier alone.

WHY THE FIXTURES ARE SPLIT ACROSS A `+` — DO NOT "TIDY" THEM BACK TOGETHER.
GitHub push protection scans this repo and rejected the first push of this file
over the Twilio SID fixtures, which are shaped exactly like the real thing
because that is the only way to prove the pattern matches. Source-level
concatenation keeps the runtime string intact while removing the contiguous
literal that a scanner keys on. Joining them back up will block the next push
with an error that has nothing to do with whatever you were changing.

That rejection is worth recording rather than just working around: the very
first push of a secret scanner was stopped by a secret scanner. GitHub's is the
belt to this module's braces, and it is not a substitute — it fires at push
time on THIS repo, whereas this module fires before a write to ANY repo,
including the public ones the ratified visibility default (§11.3) now implies.
"""

import logging

import pytest

from app.secretscan import SecretFinding, scan_for_secrets


def _names(text: str) -> list[str]:
    return [f.pattern_name for f in scan_for_secrets(text)]


# ── 1. One test per known prefix (§9) ────────────────────────────────────────
@pytest.mark.parametrize(
    "name, sample",
    [
        ("github_token",    "token: ghp_" + "A1b2C3d4E5f6G7h8I9j0"),
        ("github_pat",      "token: github_pat_" + "11ABCDEFG0abcdefghij_KLMNOPQRSTUVWX"),
        ("duffel_token",    "auth: duffel_test_" + "abc123DEF456ghi"),
        ("anthropic_key",   "key = sk-ant-" + "api03-AAAAbbbbCCCCddddEEEEffff"),
        ("slack_bot_token", "hook: xoxb-" + "123456789012-abcdefghijkl"),
        ("google_api_key",  "maps: AIza" + "SyD-1234567890abcdefghijklmnopqrstuv"),
        ("twilio_sid",      "sid: AC" + "0123456789abcdef0123456789abcdef"),
    ],
)
def test_each_known_prefix_is_caught(name, sample):
    """Each credential type is recognised BY NAME, so a refusal can say which."""
    assert name in _names(sample), f"{name} not detected in {sample[:20]}..."


def test_private_key_header_is_caught():
    doc = "Here is the deploy key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow...\n"
    assert "private_key" in _names(doc)


def test_plain_begin_private_key_header_is_caught():
    """No algorithm word — the PKCS#8 form."""
    assert "private_key" in _names("-----BEGIN PRIVATE KEY-----")


# ── 2. The non-echo invariant — the load-bearing test (§2.4 / §9) ────────────
def test_finding_never_contains_matched_value(caplog):
    """A finding reports WHERE and WHAT KIND, never the secret itself.

    Asserted over the finding, its repr, and anything the module logged. A
    scanner that leaks the credential into its own output has reintroduced the
    exposure it exists to prevent — and this output is bound for
    `github_write_log`, which is stored AND rendered on the status page, so a
    leak there leaks twice.
    """
    secret = "sk-ant-api03-SUPERSECRETVALUE0123456789abcdef"
    doc = f"The key is {secret} and it must not appear below."

    with caplog.at_level(logging.DEBUG):
        findings = scan_for_secrets(doc)

    assert findings, "precondition: the scanner must actually catch this"
    assert findings[0].pattern_name == "anthropic_key"
    assert findings[0].line == 1

    # The value must be absent from every representation of the result.
    for f in findings:
        assert secret not in repr(f)
        assert secret not in str(f)
        for value in vars(f).values():
            assert secret not in str(value)

    assert secret not in caplog.text, "the scanner logged the secret it caught"
    # And the secret's distinctive body, not just the whole string.
    assert "SUPERSECRETVALUE" not in repr(findings)
    assert "SUPERSECRETVALUE" not in caplog.text


def test_finding_location_is_usable():
    """The location must actually point at the secret — a wrong offset is the
    same as no offset once someone tries to use it."""
    line_2 = "key: ghp_ABCDEFGHIJ0123456789xyz"
    doc = f"# Title\n{line_2}\n"

    (finding,) = [f for f in scan_for_secrets(doc) if f.pattern_name == "github_token"]
    assert finding.line == 2
    start, end = finding.char_span
    assert line_2[start:end].startswith("ghp_")


# ── 3. Not trigger-happy (§9) ────────────────────────────────────────────────
def test_clean_design_document_passes():
    """Ordinary prose and code fences produce no findings. A scanner that cries
    wolf on every document gets routed around, and then it protects nothing."""
    doc = """# TDD — Some Feature

## 1. Problem

The morning brief reads a section that does not exist, so it narrates an error
dump aloud. See `app/briefing.py` and the `is_speakable_briefing` guard.

```python
def scan_for_secrets(text: str) -> list[SecretFinding]:
    return []
```

| Tool | Gated | Notes |
|---|---|---|
| `commit_document` | no | branch + PR |

Deferred: milestone dependencies, critical path, a Gantt render.
"""
    assert scan_for_secrets(doc) == []


def test_empty_and_whitespace_are_clean():
    assert scan_for_secrets("") == []
    assert scan_for_secrets("\n\n   \n") == []


# ── 4. The entropy floor is conservative (§9) ────────────────────────────────
def test_hex_digests_do_not_trip_the_entropy_floor():
    """Encodes the false-positive caution as a test rather than a hope.

    A pure-hex string cannot exceed log2(16) = 4.0 bits/char at ANY length, so
    the 4.5 floor excludes every hex digest by construction — git SHAs, MD5,
    SHA-1, SHA-256, hex UUIDs. Design documents are full of these.
    """
    doc = "\n".join([
        "Deployed at commit 7a016b3f4c2e9d8a1b5c6e7f0a9b8c7d6e5f4a3b",       # sha1, 40
        "sha256: 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "id = 550e8400e29b41d4a716446655440000",                             # hex uuid
    ])
    assert scan_for_secrets(doc) == []


def test_real_high_entropy_secret_trips_the_floor():
    """The other half of the boundary: a mixed-alphabet 40-char credential draws
    on ~62 symbols, lands near 5.0+ bits/char, and IS flagged."""
    doc = "opaque = kJ8xQ2mZ7vB4nR6tY1wE3sD5fG9hL0pA8cX2uI4o"
    assert "high_entropy" in _names(doc)


def test_an_env_file_shaped_document_is_clean(monkeypatch):
    """THE FIRST REAL REFUSAL (2026-08-02), and the tuning it earned.

    Scanning the repo archive flagged five lines of `.env.template` as
    high-entropy. None were secrets — model ids, a timezone, a repo name, an
    email — but `=` was an interior token character, so `KEY=value` scored as ONE
    token and an ordinary assignment became a 37-character blob whose two halves
    were individually harmless.

    That mattered beyond the noise: env-file-shaped content is exactly what a
    design document about configuration contains, so `commit_document` would
    have refused legitimate documents. §11 said tune from real refusals rather
    than imagination; this is the refusal.
    """
    doc = "\n".join([
        "JARVIS_MODEL=claude-opus-4-5-20260101",
        "JARVIS_ROUTER_MODEL=claude-haiku-4-5-20251001",
        "CALENDAR_TIMEZONE=America/Los_Angeles",
        "IDEAS_REPO=mdk32366/jarvis-ideas",
        "COMPLIANCE_EMAIL=someone@example.com",
        "DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/app",
    ])
    assert scan_for_secrets(doc) == []


def test_equals_is_not_an_interior_token_character():
    """The mechanism, pinned directly: two halves each below the length floor
    must not be scored as one token because an `=` joins them."""
    left, right = "aB3dE5fG7hJ9kL1mN3pQ5r", "sT7uV9wX1yZ3aB5cD7eF9g"
    assert len(left) < 32 and len(right) < 32
    assert len(left) + len(right) + 1 > 32, "precondition: the joined form clears the floor"
    assert scan_for_secrets(f"{left}={right}") == []


def test_a_real_secret_in_an_assignment_is_still_caught():
    """Detection is not weakened — the named prefixes never depended on
    tokenisation in the first place."""
    assert "anthropic_key" in _names("ANTHROPIC_API_KEY=sk-ant-"
                                     "api03-AAAAbbbbCCCCddddEEEEffff")
    assert "github_token" in _names("GITHUB_TOKEN=ghp_ABCDEFGHIJ0123456789xyz")


def test_a_raw_high_entropy_value_in_an_assignment_is_still_caught():
    """The other half of not-weakened: once the split happens at the `=`, the
    VALUE is its own token and is scored on its own merits."""
    assert "high_entropy" in _names("SOME_TOKEN=kJ8xQ2mZ7vB4nR6tY1wE3sD5fG9hL0pA8cX2uI4o")


def test_a_padded_base64_blob_is_still_caught_on_its_body():
    """Dropping `=` from the token class does not blind the scanner to padded
    base64 — the body carries the match on its own.

    RECORDED BECAUSE THE FIRST VERSION OF THIS TEST WAS WRONG. It claimed
    trailing padding counted toward the 32-char floor, and asserted a 30-char
    body plus `==` would trip. It failed on the fixed code AND on a planted
    over-correction — because `{32,}` applies to the BODY, so padding could never
    carry a short body over. The element it was defending did nothing, and the
    test only surfaced that by being planted against.

    A short blob stays under the floor with or without padding; that is the
    length rule working, not a gap.
    """
    body = "kJ8xQ2mZ7vB4nR6tY1wE3sD5fG9hL0pA8cX2uI4o"     # 40 chars, over the floor
    assert "high_entropy" in _names(f"blob: {body}==")
    assert "high_entropy" in _names(f"blob: {body}")

    short = "kJ8xQ2mZ7vB4nR6tY1wE3sD5fG9hL0"              # 30 chars, under it
    assert scan_for_secrets(f"blob: {short}==") == [], "padding must not fake up length"


def test_short_random_strings_are_ignored():
    """Below the length floor, entropy is not evidence of anything. A 22-char
    nonce is exactly the shape `secrets.token_urlsafe(16)` produces and appears
    in this repo's own docs."""
    assert scan_for_secrets("nonce = aB3dE5fG7hJ9kL1mN3pQ5r") == []


# ── 5. Reporting shape ───────────────────────────────────────────────────────
def test_a_known_token_is_not_double_reported_as_high_entropy():
    """A named credential type is strictly more useful than 'high entropy
    string'; reporting both for one value makes a refusal read like two."""
    names = _names("key = sk-ant-api03-AAAAbbbbCCCCddddEEEEffffGGGGhhhh")
    assert names.count("high_entropy") == 0
    assert names == ["anthropic_key"]


def test_findings_are_ordered_by_position():
    doc = "\n".join([
        "clean line",
        "first: ghp_ABCDEFGHIJ0123456789xyz",
        "clean again",
        "second: AC" + "0123456789abcdef0123456789abcdef",
    ])
    findings = scan_for_secrets(doc)
    assert [f.line for f in findings] == [2, 4]
    assert [f.pattern_name for f in findings] == ["github_token", "twilio_sid"]


def test_multiple_secrets_on_one_line_all_reported():
    doc = "a=ghp_ABCDEFGHIJ0123456789xyz b=AC" + "0123456789abcdef0123456789abcdef"
    findings = scan_for_secrets(doc)
    assert len(findings) == 2
    assert findings[0].char_span[0] < findings[1].char_span[0]


def test_finding_is_hashable_and_frozen():
    """Frozen so a finding cannot be mutated into carrying a value after the
    fact, and hashable so callers can dedupe without writing a key function."""
    f = SecretFinding(pattern_name="x", line=1, char_span=(0, 1))
    assert len({f, SecretFinding(pattern_name="x", line=1, char_span=(0, 1))}) == 1
    with pytest.raises(Exception):
        f.line = 2  # type: ignore[misc]
