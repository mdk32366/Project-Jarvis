"""Tests — TDD #13: Google Docs & Sheets creation.

Test table from docs/TDD-google-docs-sheets.md §6.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _ctx(db, thread_key="test-thread"):
    from app.handlers.base import Context
    return Context(db=db, channel="test", actor="test", thread_key=thread_key)


def _fake_docs_svc(doc_id="fake-doc-id-123"):
    """MagicMock that looks like a Google Docs service client."""
    svc = MagicMock()
    svc.documents.return_value.create.return_value.execute.return_value = {
        "documentId": doc_id
    }
    svc.documents.return_value.batchUpdate.return_value.execute.return_value = {}
    svc.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 5}]}
    }
    return svc


def _fake_sheets_svc(sheet_id="fake-sheet-id-456"):
    """MagicMock that looks like a Google Sheets service client."""
    svc = MagicMock()
    svc.spreadsheets.return_value.create.return_value.execute.return_value = {
        "spreadsheetId": sheet_id
    }
    svc.spreadsheets.return_value.values.return_value.update.return_value.execute.return_value = {}
    return svc


# ── §5.2: create_google_doc ───────────────────────────────────────────────────


def test_create_google_doc_returns_shareable_link(db, monkeypatch):
    """Basic happy path: create a doc and get back a shareable Drive link."""
    import app.google_oauth as oauth_module
    from app.handlers.googledocs import _create_google_doc

    svc = _fake_docs_svc("doc-abc-123")
    monkeypatch.setattr(oauth_module, "docs_service", lambda: svc)

    ctx = _ctx(db)
    result = _create_google_doc({"title": "Trip Itinerary", "content": "Day 1: Arrive."}, ctx)

    assert "docs.google.com/document/d/doc-abc-123" in result
    assert "Trip Itinerary" in result


def test_create_google_doc_records_ownership_in_db(db, monkeypatch):
    """create_google_doc inserts a GoogleDocument row — required for append scoping."""
    import app.google_oauth as oauth_module
    from app.handlers.googledocs import _create_google_doc
    from app.models import GoogleDocument
    from sqlalchemy import select

    svc = _fake_docs_svc("doc-ownership-test")
    monkeypatch.setattr(oauth_module, "docs_service", lambda: svc)

    ctx = _ctx(db, thread_key="owner-thread")
    _create_google_doc({"title": "Ownership Test", "content": "Hello."}, ctx)

    row = db.execute(
        select(GoogleDocument).where(GoogleDocument.doc_id == "doc-ownership-test")
    ).scalars().first()

    assert row is not None
    assert row.kind == "doc"
    assert row.title == "Ownership Test"
    assert row.thread_key == "owner-thread"


# ── §5.2: create_google_sheet ─────────────────────────────────────────────────


def test_create_google_sheet_from_structured_rows(db, monkeypatch):
    """Headers + rows produce a correctly-shaped Sheet and return a shareable link."""
    import app.google_oauth as oauth_module
    from app.handlers.googledocs import _create_google_sheet

    svc = _fake_sheets_svc("sheet-xyz-789")
    monkeypatch.setattr(oauth_module, "sheets_service", lambda: svc)

    ctx = _ctx(db)
    result = _create_google_sheet(
        {
            "title": "Trip Costs",
            "headers": ["Date", "Item", "Amount"],
            "rows": [["2026-08-01", "Hotel", "$150"], ["2026-08-02", "Meals", "$45"]],
        },
        ctx,
    )

    assert "docs.google.com/spreadsheets/d/sheet-xyz-789" in result
    assert "Trip Costs" in result

    # Confirm values.update was called with the right shape
    update_call = svc.spreadsheets.return_value.values.return_value.update
    call_kwargs = update_call.call_args
    body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body") or call_kwargs[0][0]
    assert body["values"][0] == ["Date", "Item", "Amount"]
    assert body["values"][1] == ["2026-08-01", "Hotel", "$150"]


def test_create_google_sheet_records_ownership_in_db(db, monkeypatch):
    """create_google_sheet inserts a GoogleDocument row with kind='sheet'."""
    import app.google_oauth as oauth_module
    from app.handlers.googledocs import _create_google_sheet
    from app.models import GoogleDocument
    from sqlalchemy import select

    svc = _fake_sheets_svc("sheet-ownership-test")
    monkeypatch.setattr(oauth_module, "sheets_service", lambda: svc)

    ctx = _ctx(db)
    _create_google_sheet(
        {"title": "Sheet Ownership", "headers": ["Col1"], "rows": []}, ctx
    )

    row = db.execute(
        select(GoogleDocument).where(GoogleDocument.doc_id == "sheet-ownership-test")
    ).scalars().first()

    assert row is not None
    assert row.kind == "sheet"


# ── §5.3: ownership scoping ───────────────────────────────────────────────────


def test_append_refuses_a_doc_id_jarvis_did_not_create(db, monkeypatch):
    """Mirrors test_booking_refuses_an_offer_id_it_did_not_retrieve.

    append_to_google_doc rejects any doc_id not in the google_documents table.
    An arbitrary Drive file ID handed in conversation is refused before any
    API call is made — same load-bearing check as offer_id in flight booking.
    """
    from app.handlers.googledocs import _append_to_google_doc

    ctx = _ctx(db)
    result = _append_to_google_doc(
        {"doc_id": "some-random-drive-file-id", "content": "Add this."}, ctx
    )

    assert result.startswith("[refused]"), f"Expected refusal, got: {result!r}"
    assert "not on record" in result
    assert "some-random-drive-file-id" in result


def test_append_succeeds_for_a_doc_jarvis_created(db, monkeypatch):
    """append_to_google_doc works when the doc_id is in the ownership table."""
    import app.google_oauth as oauth_module
    from app.handlers.googledocs import _create_google_doc, _append_to_google_doc

    svc = _fake_docs_svc("doc-for-append")
    monkeypatch.setattr(oauth_module, "docs_service", lambda: svc)

    ctx = _ctx(db)
    _create_google_doc({"title": "Appendable Doc", "content": "Initial content."}, ctx)

    result = _append_to_google_doc(
        {"doc_id": "doc-for-append", "content": "Appended content."}, ctx
    )

    assert "[refused]" not in result
    assert "Appendable Doc" in result


# ── §5.4: provenance tagging ──────────────────────────────────────────────────


def test_web_sourced_content_is_provenance_tagged(db, monkeypatch):
    """Content containing the UNTRUSTED fence marker gets a source-disclosure footer.

    This is structural: _provenance_footer fires regardless of whether the model
    remembered to add citations. The fence marker is the signal that web-sourced
    material was passed through rather than synthesized out.
    """
    import app.google_oauth as oauth_module
    from app.handlers.googledocs import _create_google_doc

    svc = _fake_docs_svc("doc-web-sourced")
    monkeypatch.setattr(oauth_module, "docs_service", lambda: svc)

    web_content = (
        "--- BEGIN UNTRUSTED WEB CONTENT ---\n"
        "According to the Scottsdale Tourism Board, temperatures in December average 65°F.\n"
        "--- END UNTRUSTED WEB CONTENT ---"
    )

    ctx = _ctx(db)
    _create_google_doc({"title": "Weather Notes", "content": web_content}, ctx)

    # Extract the content that was actually passed to batchUpdate
    batch_call = svc.documents.return_value.batchUpdate.call_args
    sent_body = batch_call.kwargs.get("body") or batch_call[1].get("body") or batch_call[0][0]
    inserted_text = sent_body["requests"][0]["insertText"]["text"]

    assert "Source disclosure" in inserted_text, (
        "Web-sourced content was written without a provenance footer"
    )
    assert "--- BEGIN UNTRUSTED WEB CONTENT ---" not in inserted_text, (
        "Raw fence marker was written verbatim into the document"
    )


def test_jarvis_own_synthesis_is_not_falsely_tagged_as_sourced(db, monkeypatch):
    """Content without the UNTRUSTED fence marker does NOT get a provenance footer.

    Don't over-tag: JARVIS's own analysis or a document typed by the user
    should not carry a spurious 'sourced from the web' disclosure.
    """
    import app.google_oauth as oauth_module
    from app.handlers.googledocs import _create_google_doc

    svc = _fake_docs_svc("doc-own-synthesis")
    monkeypatch.setattr(oauth_module, "docs_service", lambda: svc)

    ctx = _ctx(db)
    _create_google_doc(
        {
            "title": "My Analysis",
            "content": "Based on my knowledge: Scottsdale is warm in December.",
        },
        ctx,
    )

    batch_call = svc.documents.return_value.batchUpdate.call_args
    sent_body = batch_call.kwargs.get("body") or batch_call[1].get("body") or batch_call[0][0]
    inserted_text = sent_body["requests"][0]["insertText"]["text"]

    assert "Source disclosure" not in inserted_text, (
        "Spurious provenance footer added to JARVIS's own synthesis"
    )


# ── §5.1: OAuth scope extension ───────────────────────────────────────────────


def test_oauth_scope_extension_does_not_break_existing_contacts_tasks_access():
    """New Docs/Sheets scopes are added alongside the existing ones, not replacing them.

    Confirms the SCOPES list still contains all five expected entries — the three
    original (Contacts, Tasks, Calendar) plus the two new ones (Docs, Sheets).
    """
    from app.google_oauth import SCOPES

    assert "https://www.googleapis.com/auth/contacts.readonly" in SCOPES
    assert "https://www.googleapis.com/auth/tasks" in SCOPES
    assert "https://www.googleapis.com/auth/calendar" in SCOPES
    assert "https://www.googleapis.com/auth/documents" in SCOPES
    assert "https://www.googleapis.com/auth/spreadsheets" in SCOPES


def test_missing_new_scope_gives_clear_reauth_message_not_raw_403():
    """If the existing token lacks the Docs/Sheets scope, the error is legible.

    Google returns ACCESS_TOKEN_SCOPE_INSUFFICIENT (mapped to a 403). The
    explain() helper converts this to an actionable re-auth prompt rather than
    a raw HTTP exception.
    """
    from app.google_oauth import explain

    class FakeHttpError(Exception):
        pass

    err = FakeHttpError("insufficientPermissions: ACCESS_TOKEN_SCOPE_INSUFFICIENT")
    msg = explain(err)

    assert msg is not None, "explain() returned None for an insufficient-scope error"
    assert "app.google_oauth" in msg or "reconnect" in msg.lower(), (
        f"Error message doesn't mention how to reconnect: {msg!r}"
    )


# ── §5.2: tool availability ───────────────────────────────────────────────────


def test_create_tools_are_available_to_relevant_subagents(db):
    """create_google_doc and create_google_sheet are in the secretary and travel rosters.

    Unlike book_flight, these are ungated (§4) — they live in the sub-agent
    registry and are reachable without going through the top-level orchestrator.
    """
    from app.handlers.base import build_registry
    from app.agents import DEFAULT_AGENTS

    reg = build_registry(include_delegate=False, db=db)
    all_tool_names = {t["name"] for t in reg.anthropic_tools()}

    assert "create_google_doc" in all_tool_names
    assert "create_google_sheet" in all_tool_names
    assert "append_to_google_doc" in all_tool_names

    # Confirm the right agents carry them in their rosters
    assert "create_google_doc" in DEFAULT_AGENTS["secretary"].tools
    assert "create_google_sheet" in DEFAULT_AGENTS["secretary"].tools
    assert "append_to_google_doc" in DEFAULT_AGENTS["secretary"].tools
    assert "create_google_doc" in DEFAULT_AGENTS["travel"].tools
    assert "create_google_sheet" in DEFAULT_AGENTS["travel"].tools
