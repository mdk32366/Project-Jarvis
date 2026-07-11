import re


def _prose(html: str) -> str:
    """Collapse whitespace so assertions test CONTENT, not source line wrapping.
    The pages wrap long sentences across lines; a reviewer reads the rendered
    text."""
    return re.sub(r"\s+", " ", html).lower()


"""Compliance page tests — mapped to the Twilio errors that rejected us.

These assert the EXACT phrases carrier reviewers look for. They exist so a future
edit to main.py can't silently re-break the registration.

Rejection history (2026-07-11):
  30908 — privacy policy not verifiable / missing the no-sharing statement
  30909 — message flow / CTA doesn't explain how consent is obtained
  30882 — terms don't meet requirements (third-party marketing conflict)
  30886 — campaign description doesn't explain who sends / receives / why

Root cause of all four: "/" served the Vite starter template's SPA login screen.
A reviewer visiting jarvis-mdk.fly.dev saw "Vite + FastAPI Starter" — no business,
no program, no disclosures. Errors 30919 (site lacks business/use-case info) and
30921 (site requires authentication) were the real underlying failures.
"""


# ── Landing page must exist and be public (30919, 30921) ─────────────────────
def test_root_is_a_public_landing_page_not_the_spa(client):
    """A reviewer hitting the root domain must see the messaging program, not a
    login screen. This is what sank the last submission."""
    r = client.get("/")
    assert r.status_code == 200
    body = _prose(r.text)
    assert "vite" not in body, "root is still serving the starter template!"
    assert "jarvis" in body
    assert "password" not in body          # no auth wall


def test_landing_names_sender_recipient_and_purpose(client):
    """30886: must say who sends, who receives, and why."""
    body = _prose(client.get("/").text)
    assert "who sends the messages" in body
    assert "who receives the messages" in body
    assert "why the messages are sent" in body
    assert "account owner" in body


def test_landing_describes_the_opt_in_workflow(client):
    """30909 / 30917: every opt-in path, described completely."""
    body = _prose(client.get("/").text)
    assert "opt-in" in body
    assert "no public sign-up" in body
    assert "user-initiated" in body or "initiating contact" in body
    assert "consent is not a condition" in body


def test_landing_carries_the_required_disclosures(client):
    """30909: CTA disclosures, verbatim."""
    body = _prose(client.get("/").text)
    assert "message and data rates may apply" in body
    assert "message frequency varies" in body
    assert "stop" in body and "help" in body


def test_landing_has_the_no_sharing_statement(client):
    """30908: the exact CTIA sentence."""
    body = _prose(client.get("/").text)
    assert "no mobile information will be shared with third parties or affiliates for" in body
    assert "marketing or promotional purposes" in body


def test_landing_links_to_privacy_and_terms(client):
    """30933 / 30934: both must be reachable from the reviewed page."""
    body = client.get("/").text
    assert 'href="/privacy"' in body
    assert 'href="/terms"' in body


# ── Privacy (30908, 30933) ───────────────────────────────────────────────────
def test_privacy_page(client):
    r = client.get("/privacy")
    assert r.status_code == 200
    body = _prose(r.text)
    assert "message and data rates may apply" in body
    assert "message frequency" in body


def test_privacy_has_the_exact_no_sharing_language(client):
    """30908 — the reviewer looks for this sentence specifically."""
    body = _prose(client.get("/privacy").text)
    assert "no mobile information will be shared with third parties or affiliates for" in body
    assert ("all the above categories exclude text messaging originator opt-in data and "
            "consent" in body)


def test_privacy_explains_how_consent_is_obtained(client):
    """30909 / 30917 — the opt-in workflow belongs here too, not only on the CTA."""
    body = _prose(client.get("/privacy").text)
    assert "how consent is obtained" in body
    assert "no public sign-up form" in body


# ── Terms (30882, 30934) ─────────────────────────────────────────────────────
def test_terms_page(client):
    r = client.get("/terms")
    assert r.status_code == 200
    assert "STOP" in r.text and "HELP" in r.text


def test_terms_disclaim_third_party_marketing(client):
    """30882 — rejected when the use case conflicts with third-party marketing
    rules. Say plainly that there is none."""
    body = _prose(client.get("/terms").text)
    assert "no third-party marketing" in body or "no marketing or promotional content" in body


def test_terms_describe_sender_recipient_purpose(client):
    """30886."""
    body = _prose(client.get("/terms").text)
    assert "sender:" in body
    assert "recipient:" in body
    assert "purpose:" in body


# ── No personal address published ────────────────────────────────────────────
def test_no_personal_email_is_published(client):
    """The compliance contact is a config value, deliberately NOT a personal
    address — these pages are public and read by strangers."""
    for path in ("/", "/privacy", "/terms"):
        assert "mdk32366@gmail.com" not in client.get(path).text
