# Design note: how the architect gets the codebase (decision: no archive)

**Status:** decided, 2026-07-17. Supersedes the earlier "snapshot on deploy" proposal.

## The question

How should Architect Claude (the claude.ai Project) always have the latest codebase to
work from, without the manual "make an `.env`-free zip before each session" step?

## The decision: sync repo source directly — do NOT build an archive

The claude.ai Project syncs source files straight from the GitHub connector. That makes a
generated archive unnecessary, and in fact worse than nothing:

- **Live source beats a snapshot.** The Project reads `backend/`, `ui/`, `docs/` as current
  files on every sync — always matching `main`, no build step, nothing to remember.
- **A zip is opaque to the architect.** The Project ingests files as readable knowledge; a
  binary `.zip` can't be read inside, so committing one would give the architect *less*, not
  more.
- **The `.env` problem only exists locally.** The manual zip stripped `.env` because it was
  zipping the working folder, where the live `.env` sits. Anything sourced from GitHub is
  already `.env`-free — `.env` is gitignored and never committed.

So there is no CI archive step, no committed snapshot, and nothing in `/docs` to generate.

## Safety: what reaches the Project is exactly what's in git

Because the Project syncs the repo, **anything committed is visible to the Project.** Verified
2026-07-17:

- `.env` and `backend/.env` are gitignored (`.gitignore:13`); the only tracked env file is
  `.env.template` (empty placeholders).
- No secret-bearing files are tracked (no service-account JSON, `.pem`, `.key`, tokens).
- No live secret *values* found committed in tracked source.

**Standing rule:** secrets live in `.env` (local) and `fly secrets` (prod) — never committed.
As long as that holds, syncing the repo to the Project exposes nothing sensitive. If a secret
is ever committed by accident, it must be rotated *and* history-scrubbed, not just deleted
from the tip (see the `jarvis.tar.gz` history note — deletion from HEAD leaves it in history).

## Action item (Project-side, not code)

Widen the Project's GitHub sync scope beyond `docs/` to include the source it should read
(`backend/`, `ui/`, and `docs/`). That is a Project knowledge-settings change, not a repo
change — nothing to deploy.
