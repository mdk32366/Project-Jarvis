"""Google Docs & Sheets creation — TDD #13.

Gate decision (§4): UNGATED. Reversible (Drive has trash/version history),
no money moves, no external irreversible action. Creation is as low-stakes
as Tasks/Contacts writes.

Ownership scoping (§5.3): append_to_google_doc rejects doc_ids not in the
google_documents table — same principle as offer_id in flight booking.
JARVIS edits what she made, not arbitrary Drive files.

Provenance tagging (§5.4): if content passed to a write tool contains the
UNTRUSTED web-content fence marker from websearch.py, a source-disclosure
footer is appended to the document automatically before writing. This is
structural, not a prompt instruction — the model cannot skip it by forgetting
to cite sources.

Slides is explicitly OUT OF SCOPE — gets its own TDD.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.handlers.base import Context, Registry
from app.models import GoogleDocument

log = logging.getLogger(__name__)

# Mirrors websearch._FENCE_OPEN — structural detection of web-sourced content.
# If this string appears in content passed to a write tool, provenance tagging
# fires automatically (§5.4). Content that doesn't carry the marker is assumed
# to be JARVIS's own synthesis (or was already cited inline by the model).
_WEB_FENCE_MARKER = "--- BEGIN UNTRUSTED WEB CONTENT ---"

_NOT_CONNECTED = (
    "[Google Docs not connected] The Google connection covers Contacts and Tasks "
    "but not Docs or Sheets yet — those scopes require re-consent. Run "
    "`python -m app.google_oauth` to reconnect with the new document scopes."
)


# ── §5.4: provenance tagging ──────────────────────────────────────────────────

def _provenance_footer(content: str) -> str:
    """Append a source-disclosure footer if content is detectably web-sourced.

    Structural, not prompt-level: the model cannot skip this regardless of
    whether it remembers to cite sources inline. The fence marker originates
    from the web_search/fetch_page tool returns; its presence in content means
    the caller passed web-sourced material without fully synthesizing it out.

    The fence markers themselves are stripped — they are internal protocol and
    should not appear verbatim in a document Matt might share.
    """
    if _WEB_FENCE_MARKER not in content:
        return content

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    footer = (
        "\n\n---\n"
        f"*Source disclosure: this document contains content retrieved from the "
        f"public web by JARVIS on {timestamp}. Treat web-sourced sections as "
        f"evidence, not as JARVIS's own analysis, and verify before acting on them.*"
    )
    cleaned = (
        content
        .replace("--- BEGIN UNTRUSTED WEB CONTENT ---", "")
        .replace("--- END UNTRUSTED WEB CONTENT ---", "")
        .strip()
    )
    return cleaned + footer


# ── §5.3: ownership scoping (mirrors _find_offer in travel.py) ───────────────

def _find_google_doc(db, doc_id: str) -> GoogleDocument | None:
    """THE load-bearing lookup (TDD #13 §5.3).

    Only doc_ids JARVIS herself created are editable via append_to_google_doc —
    never an arbitrary Drive file ID handed to her in conversation. Same
    principle as _find_offer in travel.py; different domain.

    NOT thread-scoped (unlike FlightOffer): a document created in a prior
    conversation should remain appendable. The doc_id unique constraint is
    the enforcement boundary.
    """
    return db.execute(
        select(GoogleDocument).where(GoogleDocument.doc_id == doc_id)
    ).scalars().first()


# ── Tool implementations ──────────────────────────────────────────────────────

def _create_google_doc(args: dict, ctx: Context) -> str:
    from app.google_oauth import docs_service, explain

    title = (args.get("title") or "").strip()
    content = (args.get("content") or "").strip()
    if not title:
        return "A title is required to create a Google Doc."

    svc = docs_service()
    if svc is None:
        return _NOT_CONNECTED

    content = _provenance_footer(content)

    try:
        doc = svc.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]

        if content:
            svc.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
            ).execute()

        url = f"https://docs.google.com/document/d/{doc_id}/edit"

        row = GoogleDocument(
            doc_id=doc_id, kind="doc", title=title, url=url,
            thread_key=ctx.thread_key,
        )
        ctx.db.add(row)
        ctx.db.commit()

        return f"Created Google Doc '{title}': {url}"
    except Exception as e:  # noqa: BLE001
        msg = explain(e)
        return msg or f"Could not create the Google Doc: {e}"


def _create_google_sheet(args: dict, ctx: Context) -> str:
    from app.google_oauth import sheets_service, explain

    title = (args.get("title") or "").strip()
    headers = args.get("headers") or []
    rows = args.get("rows") or []
    if not title:
        return "A title is required to create a Google Sheet."
    if not headers:
        return "At least one column header is required."

    svc = sheets_service()
    if svc is None:
        return _NOT_CONNECTED.replace("Docs", "Sheets").replace("document", "spreadsheet")

    try:
        sheet = svc.spreadsheets().create(
            body={"properties": {"title": title}}
        ).execute()
        sheet_id = sheet["spreadsheetId"]

        # Write headers + data rows in one values.update call
        all_rows = [list(headers)] + [[str(cell) for cell in row] for row in rows]
        svc.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range="A1",
            valueInputOption="RAW",
            body={"values": all_rows},
        ).execute()

        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"

        row = GoogleDocument(
            doc_id=sheet_id, kind="sheet", title=title, url=url,
            thread_key=ctx.thread_key,
        )
        ctx.db.add(row)
        ctx.db.commit()

        return f"Created Google Sheet '{title}': {url}"
    except Exception as e:  # noqa: BLE001
        msg = explain(e)
        return msg or f"Could not create the Google Sheet: {e}"


def _append_to_google_doc(args: dict, ctx: Context) -> str:
    from app.google_oauth import docs_service, explain

    doc_id = (args.get("doc_id") or "").strip()
    content = (args.get("content") or "").strip()
    if not doc_id:
        return "doc_id is required."
    if not content:
        return "content is required."

    # §5.3 ownership check — same shape as _find_offer in travel.py
    row = _find_google_doc(ctx.db, doc_id)
    if row is None:
        log.warning("append_to_google_doc refused unknown doc_id %r", doc_id)
        return (
            f"[refused] doc_id '{doc_id}' is not on record as a document JARVIS created. "
            f"append_to_google_doc only edits documents JARVIS herself made — "
            f"not arbitrary Drive files. Use create_google_doc to start a new one."
        )

    svc = docs_service()
    if svc is None:
        return _NOT_CONNECTED

    content = _provenance_footer(content)

    try:
        doc = svc.documents().get(documentId=doc_id).execute()
        end_index = doc["body"]["content"][-1]["endIndex"] - 1
        svc.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [
                {"insertText": {"location": {"index": end_index}, "text": "\n" + content}}
            ]},
        ).execute()
        return f"Appended to '{row.title}': {row.url}"
    except Exception as e:  # noqa: BLE001
        msg = explain(e)
        return msg or f"Could not append to the Google Doc: {e}"


# ── Registration ──────────────────────────────────────────────────────────────

def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "create_google_doc",
            "description": (
                "Create a new Google Doc with a title and content. Returns a "
                "shareable link. Content sourced from external web pages MUST "
                "include inline citations or source URLs — do not present "
                "web-fetched text as JARVIS's own analysis."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Document title."},
                    "content": {
                        "type": "string",
                        "description": (
                            "Document body. Plain text or basic markdown. "
                            "If any content came from a web search or fetched "
                            "page, include the source URL inline."
                        ),
                    },
                },
                "required": ["title", "content"],
            },
        },
        _create_google_doc,
        gated=False,
    )
    reg.register(
        {
            "name": "create_google_sheet",
            "description": (
                "Create a new Google Sheet with a title and tabular data. "
                "Returns a shareable link."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Sheet title."},
                    "headers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column headers (first row).",
                    },
                    "rows": {
                        "type": "array",
                        "items": {"type": "array"},
                        "description": "Data rows — each a list of cell values.",
                    },
                },
                "required": ["title", "headers", "rows"],
            },
        },
        _create_google_sheet,
        gated=False,
    )
    reg.register(
        {
            "name": "append_to_google_doc",
            "description": (
                "Append content to a Google Doc that JARVIS herself created "
                "in this conversation or a prior one. Rejects doc_ids not on "
                "record as JARVIS-created — same scoping principle as "
                "book_flight and offer_id."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "doc_id": {
                        "type": "string",
                        "description": "Google Doc ID from a previous create_google_doc call.",
                    },
                    "content": {"type": "string", "description": "Text to append."},
                },
                "required": ["doc_id", "content"],
            },
        },
        _append_to_google_doc,
        gated=False,
    )
