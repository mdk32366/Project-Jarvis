"""TTS sanitization — no URL ever reaches Twilio <Say>, whatever the LLM wrote.

The prompt tells the model not to read URLs aloud; _speakable is the guarantee.
"""
from app.channels.voice_pipeline import _say, _speakable


def test_bare_urls_become_a_link():
    text = "I found it at https://example.com/research/2026/07/paper?id=8f3a9c&utm_source=x here."
    assert _speakable(text) == "I found it at a link here."


def test_markdown_link_reads_its_text_only():
    assert _speakable("See [the full report](https://example.com/r/42) for detail.") == \
        "See the full report for detail."


def test_www_urls_without_scheme_are_caught():
    assert _speakable("Go to www.example.com/some/deep/path today") == "Go to a link today"


def test_plain_speech_is_untouched():
    text = "Two options. First, Alaska at three seventeen. Second, Duffel at one oh two."
    assert _speakable(text) == text


def test_email_readback_with_trailing_punctuation():
    text = "The tracking page (https://track.example.com/p/9x7?tok=abc123) says delivered."
    assert _speakable(text) == "The tracking page (a link) says delivered."


def test_say_never_contains_a_url():
    xml = _say("Your order: https://shop.example.com/orders/123456789 — anything else?")
    assert "shop.example.com" not in xml
    assert "a link" in xml
