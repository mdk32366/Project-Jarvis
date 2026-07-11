"""SMS provider abstraction.

Keeps the channel logic independent of the vendor. Twilio is the default in
production; the stub records messages in-process for local dev and tests (no
account, no network). Swapping to another vendor (e.g. Telnyx) is one class.
"""

from __future__ import annotations

import logging
from typing import List, Protocol, Tuple

from app.config import settings

log = logging.getLogger(__name__)


class SmsProvider(Protocol):
    def send(self, to: str, body: str) -> str:
        """Send an SMS. Returns a provider message id (or a stub id)."""

    def validate_signature(self, url: str, params: dict, signature: str) -> bool:
        """Verify an inbound webhook is authentic."""

    def call(self, to: str, url: str) -> str:
        """Place an OUTBOUND call. Twilio fetches TwiML from `url` when answered."""


class StubProvider:
    """No-op provider used in dev/tests. Captures sends for assertions."""

    def __init__(self) -> None:
        self.sent: List[Tuple[str, str]] = []
        self.calls: List[Tuple[str, str]] = []

    def send(self, to: str, body: str) -> str:
        self.sent.append((to, body))
        log.info("[sms-stub] to=%s body=%s", to, body[:80])
        return f"stub-{len(self.sent)}"

    def validate_signature(self, url: str, params: dict, signature: str) -> bool:
        return True  # trust everything in dev; prod uses Twilio validation

    def call(self, to: str, url: str) -> str:
        self.calls.append((to, url))
        log.info("[voice-stub] calling %s -> %s", to, url)
        return f"stub-call-{len(self.calls)}"


class TwilioProvider:
    """Real Twilio provider. Imports the SDK lazily so tests need not install it."""

    def send(self, to: str, body: str) -> str:
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        msg = client.messages.create(to=to, from_=settings.twilio_from_number, body=body)
        return msg.sid

    def validate_signature(self, url: str, params: dict, signature: str) -> bool:
        if not settings.twilio_validate_signature:
            return True
        from twilio.request_validator import RequestValidator

        validator = RequestValidator(settings.twilio_auth_token)
        return validator.validate(url, params, signature or "")

    def call(self, to: str, url: str) -> str:
        """Place an outbound call. Twilio GETs `url` for TwiML once answered.

        This is what turns JARVIS from an IVR into an assistant: work that can't
        fit inside a phone call's poll budget no longer has to die in a log or an
        email. She hangs up, does the work, and rings back.
        """
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        c = client.calls.create(
            to=to,
            from_=settings.twilio_from_number,
            url=url,
            # If it goes to voicemail, leave the opening line and hang up rather
            # than talking to an answering machine for 40 seconds.
            machine_detection="Enable",
            status_callback=f"{settings.voice_public_url_base}/api/voice/status",
            status_callback_event=["completed"],
        )
        return c.sid


_provider: SmsProvider | None = None


def get_sms_provider() -> SmsProvider:
    global _provider
    if _provider is None:
        _provider = TwilioProvider() if settings.sms_provider == "twilio" else StubProvider()
    return _provider


def set_sms_provider(p: SmsProvider) -> None:
    """Test/override hook."""
    global _provider
    _provider = p
