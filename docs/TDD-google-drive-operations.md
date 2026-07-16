# TDD — Google Drive file & directory operations

**Author:** JARVIS design session (Claude Project)
**For implementation by:** Claude Code (CLI, against live repo)
**Status:** Draft for review — NOT yet scheduled. Captured so it isn't lost.
**Prereq:** OAuth re-consent infrastructure (exists); TDD #13 Docs/Sheets (done).
**Date:** 2026-07-15

---

## 0. The ambition (in one paragraph)

Today JARVIS can create Docs/Sheets she owns, but cannot organize them, and
cannot find or retrieve anything in Drive. The goal: make Google Drive a
**navigable, searchable, retrievable filesystem** — "make a folder and file these
there," and eventually "find the document about the boat survey and email it to
me." This is a meaningfully bigger capability than doc creation, and it crosses a
boundary the existing design deliberately drew ("JARVIS edits what she made, not
arbitrary Drive files"). The security model is therefore the spine of this TDD,
not an afterthought.

---

## 1. The central security decision — scope determines everything

There are two Drive OAuth scopes, and the choice between them defines what this
feature can and cannot be. This is THE decision; make it consciously.

### Option A — `drive.file` (app-created files only)
Grants access ONLY to files/folders JARVIS herself created. Cannot see, search,
or touch anything you made in Drive by other means.
- **Preserves the existing boundary exactly** ("her own files only").
- Folder creation + organizing her own docs into folders: fully works.
- **"Find the boat-survey doc I uploaded last year and email it to me": DOES NOT
  WORK** — that file wasn't app-created, so it's invisible.

### Option B — `drive.readonly` (added alongside `drive.file`)
Grants READ access to your entire Drive — search, list, read any file — but no
write/modify/delete on files she didn't create.
- **Enables the search-and-retrieve ambition** ("find *the* document about X").
- Read-only on the broad grant: she can find and send you anything, but can only
  *organize/write* her own creations.
- **Cost:** JARVIS can now read your entire Drive. That's a real expansion of what
  a compromise or a bad instruction could expose. Worth it only if search-and-
  retrieve is genuinely wanted.

### Decision (owner, 2026-07-15): BOTH phases desired
Phase 2 is wanted, not deferred. The owner's reasoning, which is sound: an
OAuth-verified agent doing file operations is the same category as an
OAuth-verified command-line tool (Claude Code deletes/moves files routinely
through the owner's credentials, unremarkably). The medium (email vs. terminal)
isn't the risk. Search-and-retrieve over the whole Drive is within that framing.

- **Phase 1 (folders/organizing):** `drive.file` only. Own-files boundary,
  delivers folders.
- **Phase 2 (search/retrieve):** add `drive.readonly`. Whole-Drive READ so
  "find the doc about X and send it to me" works. DESIRED.

Each phase = a scope change = a re-consent (`python -m app.google_oauth`). Phase 1
still ships first (smaller, boundary-preserving), but Phase 2 is scheduled, not
a maybe.

### Why delete stays out even though the command-line framing is granted
The owner is right that medium isn't the risk — so the delete boundary is NOT
"an AI shouldn't delete." It's a specific cost/benefit tied to JARVIS's channel
PROPERTIES, which differ from Claude Code's in three ways that compound only on
irreversible operations:

1. **Unsupervised** — JARVIS acts asynchronously, while the owner isn't watching
   (the whole point: she works while he drives). Claude Code runs supervised, in
   a session the owner watches, approving destructive ops in the moment.
2. **Partially-untrusted instruction origin** — JARVIS ingests email, SMS
   (spoofable), and external web/search content (already fenced with `UNTRUSTED
   WEB CONTENT` markers). A prompt injection can attempt to steer her. Claude
   Code's instructions come from the owner directly.
3. **Irreversible live target** — Drive, not a git repo with reflog/trash-history
   recovery.

Delete is the ONE operation where all three compound into unrecoverable loss for
near-zero convenience upside — the owner rarely needs async agent-driven deletion,
but a spoofed SMS or injected instruction acting unwatched could destroy data
permanently. Read/search/retrieve have real, frequent value and reversible or
no side effects. So: read-broad, write-narrow (own files), delete-never — a
lopsided trade that costs almost nothing and closes the one hole where the
channel properties bite hardest.

**This is a defended boundary, not a reflex — and the owner may override it.**
If deletion is wanted, it does NOT get added as a free tool; it gets GATED like
booking (confirmation + TOTP second factor), precisely because of the
async/untrusted-origin properties. Gated-delete is the correct shape if delete
goes in; free-delete is not. See §7 open decision.

---

## 2. HARD security boundaries (all phases)

Non-negotiable, per the owner's explicit direction and the existing design ethos.

- **NO DELETES. Ever.** No `files.delete`, no trashing, no emptying trash. Not a
  tool, not a code path. Deletion is irreversible-enough and valueless-enough
  here that it simply doesn't exist. If a file needs deleting, the owner does it
  by hand.
- **NO writes to files JARVIS didn't create.** Even with `drive.readonly` in
  Phase 2, the broad grant is READ-only. Modify/move/rename is restricted to
  app-created files (the `GoogleDocument` provenance table already tracks these).
- **NO permission/sharing changes.** She does not alter who can access a file,
  make things public, or add collaborators. Sharing is an owner action.
- **NO acting on a Drive file ID handed to her in conversation** for write
  operations — same rule as today's doc editing (googledocs.py:83). Writes go
  only to files whose provenance she can verify.
- **Retrieval = send a link or attach, never expose raw content indiscriminately.**
  "Send it to me" means email the owner (the only allowlisted recipient) a link
  or the file — never forward Drive contents anywhere else.

> Rationale: the whole value here is convenience over the owner's OWN data, sent
> only to the OWNER. Every boundary above ensures a bug or a bad instruction
> can't turn "find my document" into data exfiltration or destruction. Read-broad,
> write-narrow, delete-never.

---

## 3. Phase 1 — folders & organizing (drive.file)

### Scope
Add `https://www.googleapis.com/auth/drive.file` to `SCOPES`, re-consent.

### Tools
- `create_drive_folder(name, parent_folder_id?)` — creates a folder (owned by
  JARVIS). Ungated (reversible; folders trash like files).
- `move_my_file_to_folder(file_id, folder_id)` — moves an **app-created** file
  (verified against the `GoogleDocument`/provenance table) into a folder. Refuses
  if the file isn't one JARVIS created.
- `list_my_drive_items(folder_id?)` — lists app-created files/folders, optionally
  within a folder. Read over her own creations only.

### Routing
Advertise on the secretary's description: "creating and organizing GOOGLE DRIVE
FOLDERS for the docs and sheets she creates." (Description = routing signal;
without this it won't route — the exact bug just fixed for Docs.)

### Gate decisions
- Folder create, move-own-file, list-own: **ungated** (reversible, own-files-only).

---

## 4. Phase 2 — search & retrieve (drive.readonly) — SEPARATE, LATER

Only when the search ambition is actually wanted. This is where "find *the*
document about X and send it to me" lives.

### Scope
Add `https://www.googleapis.com/auth/drive.readonly`, re-consent. Conscious
decision: JARVIS can now READ your whole Drive.

### Tools
- `search_drive(query)` — full-text / name search across Drive (read-only).
  Returns matches with name, id, type, modified date, link. Never dumps content
  wholesale — returns a result list for the owner to pick from.
- `get_drive_file_link(file_id)` — resolves a file to a shareable/viewable link
  for the owner.
- `email_drive_file_to_owner(file_id)` — the "send it to me" path. Routes through
  the EXISTING gated email flow: composes an email to the OWNER (whoami, the only
  allowlisted recipient) with the link/attachment. Sending is the orchestrator's
  gated action, as all email is.

### The "send it to me" flow is gated by construction
Retrieval-and-send reuses `draft_email` → orchestrator confirmation gate. So even
"find and send" can't fire an email without the existing gate. No new send path,
no new exfun surface — it rides the rails already built.

### Gate decisions
- `search_drive`, `get_drive_file_link`: ungated (read-only, no side effects).
- `email_drive_file_to_owner`: gated (it sends email — existing gate applies).

---

## 5. Test table

| # | Phase | Test | Expected |
|---|-------|------|----------|
| 1 | 1 | create_drive_folder returns a folder id | folder created, owned by app |
| 2 | 1 | move an app-created file into a folder | succeeds |
| 3 | 1 | move a NON-app-created file (fake id) | REFUSED — provenance check fails |
| 4 | 1 | any delete tool exists | NO — assert no delete tool registered |
| 5 | 1 | list_my_drive_items shows only app-created | no foreign files listed |
| 6 | 1 | secretary description advertises Drive folders | routing canary passes |
| 7 | 2 | search_drive finds a known file by name | returns match w/ link |
| 8 | 2 | search_drive with drive.readonly absent | clean "search not enabled" (scope check) |
| 9 | 2 | write attempt on a search-found foreign file | REFUSED — read-only on broad grant |
| 10 | 2 | email_drive_file_to_owner recipient | ALWAYS the owner (whoami), never other |
| 11 | 2 | email_drive_file_to_owner send | routes through existing email gate |
| 12 | all | no permission/sharing-change tool exists | NO — assert absent |
| 13 | all | scope check: drive.file present for Phase 1 tools | graceful "not enabled" if missing |
| 14 | all | PERMISSION_DENIED (Drive API off) surfaces runbook | explain() maps it, not opaque |

---

## 6. What this is NOT

- **NOT a delete capability.** Stated twice because it matters: no deletes, no
  trashing, ever, any phase.
- **NOT write access to your existing Drive.** The broad grant (Phase 2) is
  READ-only. Writes stay on app-created files.
- **NOT a sharing/permissions manager.** She never changes who can see a file.
- **NOT a general file forwarder.** "Send it to me" = to the OWNER only, via the
  gated email path. Never to third parties.
- **NOT Phase 2 bundled into Phase 1.** The whole-Drive read grant is its own
  decision; don't take it to ship folders.

---

## 7. Open decisions

1. **Phase 2: DECIDED — desired** (owner, 2026-07-15). Whole-Drive read for
   search/retrieve is wanted. Phase 1 ships first for sequencing, not because
   Phase 2 is uncertain.
2. **`drive.readonly` vs `drive.metadata.readonly` + selective content.** If
   whole-Drive content read feels too broad, a narrower path is metadata-search +
   explicit per-file content fetch (she finds by name/metadata, fetches content
   only on the specific file the owner picks). More complex; the default is
   `drive.readonly` unless the owner prefers the narrower shape. Owner to weigh.
3. **Delete: DECIDED — out, but overridable.** Default is no-delete. If the owner
   wants it, it goes in GATED (confirmation + TOTP), never free. Not a Phase 1/2
   blocker; a separate opt-in if/when wanted.
4. **Provenance for moved files.** Moving an app-created file into a user-made
   folder is fine; confirm the provenance check keys on the file's origin, not
   its current location.
5. **Volume threshold (Phase 1 only).** Is auto-organizing worth building, or does
   the owner drag a handful of files himself? Build Phase 1 folders when manual
   organizing is a chore. (Phase 2 search/retrieve has value independent of
   volume — finding one specific file fast is useful even at low volume.)

---

## 8. Sequencing

Not scheduled. When picked up: Phase 1 is a small, clean, boundary-preserving
addition (folders + organize-own). Phase 2 is a deliberate, separate expansion
gated on the owner actually wanting whole-Drive search. Each phase is one scope
add + one re-consent + tools + description + tests — the now-familiar four-layer
pattern (tool exists, routing advertises, token has scope, API enabled).
