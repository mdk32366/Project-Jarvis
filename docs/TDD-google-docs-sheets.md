# TDD — Backlog Item #13: Google Docs & Sheets Creation

**Status:** Needs a gate decision before build (see §4)
**Repo:** `mdk32366/Project-Jarvis`
**Prereq:** existing Google OAuth (Contacts, Tasks) already integrated —
this extends the same OAuth relationship to two new scopes, not a new
auth flow from scratch.

---

## 1. What we're building

JARVIS currently reads/writes Contacts and Tasks via Google OAuth. This adds
the ability to **create and edit Google Docs and Sheets** — genuinely broad
utility (drafting, trip itineraries, structured notes, expense tracking,
literally anything that benefits from a durable, shareable, human-editable
document Matt didn't have to create by hand).

**This is a write capability with a different risk shape than Contacts/Tasks
writes** — a created Doc or Sheet is content someone might read, share,
or act on, and Sheets in particular can contain something that looks like
structured data Matt trusts. It's not irreversible the way `book_flight` or
`send_email` are (nothing leaves the building, no money moves, and Drive has
version history/trash), but it's not nothing either. Scope accordingly —
see §4.

---

## 2. Root cause / motivation

No current tool produces a durable, shareable artifact. Everything JARVIS
does today is conversational (SMS/voice reply) or narrowly structured
(a Task, a Trip record, a calendar event). A lot of genuinely useful output
— a trip itinerary, a comparison table, meeting notes, a running list —
wants to be a *document*, not a text reply that scrolls away.

---

## 3. Scope

**In scope:**
- Create a new Google Doc with title + initial content (text, basic
  structure — headings, lists; not asking for full rich-text/formatting
  fidelity in v1).
- Create a new Google Sheet with title + initial data (rows/columns from
  structured input JARVIS already has — e.g., a trip cost breakdown, a
  comparison table).
- Append to / edit an existing Doc or Sheet that JARVIS herself created
  (same ownership-scoping principle as flight offers: she edits what she
  made, not an arbitrary file ID handed to her).
- Return a shareable link in the response.

**Out of scope for v1:**
- Editing arbitrary pre-existing Docs/Sheets Matt already has (a much wider
  blast radius — that's "JARVIS can modify any file in your Drive," not
  "JARVIS can make new ones"). If wanted later, scope it separately with
  its own discussion of confirmation/gating.
- Sharing/permission changes (inviting other people, changing visibility) —
  a Doc JARVIS creates is private to the Drive it's created in by default;
  granting access to third parties is a different, sharper action.
- Formatting fidelity beyond basic structure — no charts, no complex
  Sheets formulas, no embedded images in v1.
- Deleting Docs/Sheets — not needed for the stated use cases, and deletion
  of a document someone might be relying on is its own can of worms.

---

## 4. The gate question — needs your call

Unlike Contacts/Tasks writes (low-stakes, easily correctable) and unlike
`book_flight`/`send_email` (irreversible, clearly gated), **document
creation sits in between**, and the TDD shouldn't guess at where you want
it without saying so plainly:

**Arguments for ungated (like Contacts/Tasks today):**
- Reversible — Drive has trash/version history, nothing is lost outright.
- No money, no external irreversible action (no email leaves, no ticket
  is bought).
- High-frequency, low-stakes use case ("jot this down as a doc") is
  annoying to gate — a confirmation prompt on every doc creation defeats
  the "infinite uses" value Matt's after.

**Arguments for gated, or at least a lighter-weight confirmation:**
- Once a Doc/Sheet exists and Matt starts trusting its contents (e.g., a
  cost breakdown JARVIS wrote into a Sheet), a manipulated or wrong number
  in a **Sheet specifically** could get treated as authoritative later —
  this is a milder version of the exact concern the flight-booking TDD
  raised in §2.2(a) about the web feeding bad data into an action Matt
  trusts. A malformed webpage that influences what JARVIS puts in a Sheet
  is a real, if lower-stakes, version of the injection concern.
- Docs created under Matt's Google identity are attributable to him if
  shared onward — a wrong or embarrassing auto-generated doc has a mild
  reputational tail he didn't sign off on.

**Recommendation, not a decision:** ungated for creation, but with the
same category of protection §2.2(a) established for booking — **content
JARVIS writes into a Doc/Sheet that's sourced from external web content
gets provenance-tagged**, so Matt can tell "this line came from a page
JARVIS read" versus "this is JARVIS's own summary/analysis." That's a much
lighter control than a confirmation gate, and it addresses the actual risk
(trusting fabricated or manipulated content) without making every doc
creation a multi-turn interaction. Land this recommendation or override it
before build — don't let it default silently either way.

---

## 5. Implementation

### 5.1 OAuth scope extension

Add `https://www.googleapis.com/auth/documents` and
`https://www.googleapis.com/auth/spreadsheets` to the existing OAuth
consent scope alongside Contacts/Tasks. This likely requires Matt to
re-consent once (existing token won't cover the new scopes retroactively)
— flag this clearly at rollout, don't let it surface as a mysterious
permission-denied error on first use.

### 5.2 Tools

```python
def register_docs_sheets(reg: Registry) -> None:
    reg.register(
        {
            "name": "create_google_doc",
            "description": (
                "Create a new Google Doc with a title and content. Returns a "
                "shareable link. Content sourced from external web pages must "
                "be provenance-tagged, not presented as JARVIS's own words."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},  # markdown-ish; converted to Docs structure
                },
                "required": ["title", "content"],
            },
        },
        _create_google_doc,
        gated=False,   # pending §4 decision
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
                    "title": {"type": "string"},
                    "headers": {"type": "array", "items": {"type": "string"}},
                    "rows": {"type": "array", "items": {"type": "array"}},
                },
                "required": ["title", "headers", "rows"],
            },
        },
        _create_google_sheet,
        gated=False,   # pending §4 decision
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
                    "doc_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["doc_id", "content"],
            },
        },
        _append_to_google_doc,
        gated=False,
    )
```

### 5.3 Ownership scoping (mirrors §2.2(a) from flight booking)

- New table, `GoogleDocument` (or similar): `doc_id`, `sheet_or_doc`,
  `created_at`, `created_by_thread`, `title`, `url`.
- `append_to_google_doc` (and any future edit tool) checks the target
  `doc_id` against this table before calling the Docs API. Not found →
  refuse, same pattern as the offer_id check. **JARVIS should not accept
  an arbitrary Google Doc ID handed to her in conversation and start
  editing it** — that's a much wider blast radius than "edit what you
  made."

### 5.4 Provenance tagging (per §4 recommendation)

When content passed to `create_google_doc`/`create_google_sheet` was
substantially sourced from a web search or fetched page in the same turn,
the writer (orchestrator or sub-agent) should tag that content —
inline annotation or a footer note ("Sourced from: [url], retrieved
[timestamp]") — rather than presenting it as indistinguishable from
JARVIS's own synthesis. This is a prompt-level discipline backed by a
structural nudge (similar to TDD #11 §4.2's forced-first-call pattern):
if a sub-agent's turn included a web-fetch tool call, content derived from
it going into a Doc/Sheet should carry the tag by default, not as
something the LLM has to remember to add.

---

## 6. Tests

| Test | Property |
|---|---|
| `test_create_google_doc_returns_shareable_link` | Basic happy path. |
| `test_create_google_sheet_from_structured_rows` | Headers + rows produce a correctly-shaped Sheet. |
| `test_append_refuses_a_doc_id_jarvis_did_not_create` | Mirrors `test_booking_refuses_an_offer_id_it_did_not_retrieve` — the same-shaped load-bearing check for this domain. |
| `test_web_sourced_content_is_provenance_tagged` | Content traceable to a web-fetch call in the same turn carries a source annotation in the created doc. |
| `test_jarvis_own_synthesis_is_not_falsely_tagged_as_sourced` | Don't over-tag — content that isn't from an external fetch shouldn't get a spurious citation. |
| `test_oauth_scope_extension_does_not_break_existing_contacts_tasks_access` | New scopes added alongside, not replacing, existing ones. |
| `test_missing_new_scope_gives_clear_reauth_message_not_raw_403` | If Matt hasn't re-consented yet, the failure is legible, not a bare permission error. |
| `test_create_tools_are_available_to_relevant_subagents` | Unlike booking, this isn't top-level-gated (pending §4) — assert it's reachable where useful (e.g., secretary/travel agents). |

---

## 7. Things I would push back on, if asked

- **Don't skip the ownership-scoping check on `append_to_google_doc`.**
  It's tempting to treat this as low-stakes because it's "just a doc," but
  accepting an arbitrary `doc_id` from conversation means JARVIS could be
  talked into editing a document she didn't create and Matt didn't intend
  her to touch. Same principle as offer_id, smaller stakes, still worth
  enforcing in code.
- **Don't build editing of pre-existing arbitrary Docs/Sheets in this
  pass.** It's a materially different, wider-blast-radius feature ("modify
  anything in your Drive" vs. "make new things") and deserves its own
  scoping conversation, not a scope-creep bullet here.
- **Get the §4 gate question answered explicitly**, even if the answer is
  "ungated, ship it." Don't let it default silently — that's exactly the
  kind of quiet scope decision that's fine until it isn't.

---

## 8. Decisions needed before build

1. **Gated or ungated?** (§4) — recommendation given, not decided.
2. **Provenance tagging: build now or fast-follow?** Recommend building it
   in the same pass as creation, since retrofitting "which content came
   from where" onto docs that already exist without tags is much harder
   than tagging at write time.
3. **Re-consent UX** — does Matt want a heads-up before the new OAuth
   scopes go live, given it'll interrupt whatever flow first tries to use
   these tools with a re-auth prompt?

---

*High value, moderate new surface area. The interesting design question
isn't the Docs/Sheets API integration itself — that's mechanical — it's
whether content JARVIS writes into a durable, shareable artifact needs the
same "don't let the open web quietly become something you trust" discipline
that flight booking established. Recommend: yes, but lighter — tag
provenance, don't gate creation.*
