"""Thin Anthropic client wrapper used by the orchestrator and (later) the reflector."""

from typing import Optional

import anthropic

from app.config import settings

_client: Optional[anthropic.Anthropic] = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def create_message(system: str, messages: list, tools: Optional[list] = None, model: Optional[str] = None):
    """Single round-trip to Claude. Returns the raw response object."""
    return client().messages.create(
        model=model or settings.jarvis_model,
        max_tokens=settings.max_tokens,
        system=system,
        tools=tools or [],
        messages=messages,
    )
