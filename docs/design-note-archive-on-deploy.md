# Design note: codebase snapshot on deploy (for the architect)

**Status:** proposal / documentation. Not yet implemented. For review with Architect Claude
before wiring into CI/CD.

## The goal

Every time we push to `main` and the hyperscaler (Fly) redeploys, automatically produce a
**clean, `.env`-free snapshot of the codebase** and make it available as the canonical
"latest code" that the claude.ai Project (Architect Claude) starts each day from. Today this
snapshot is produced by hand before each architecting session; the aim is to make it a
byproduct of the deploy pipeline so the architect is never working from stale code.

## Why not "commit the archive into a repo folder"

The first instinct — drop the archive into a folder in the repo and commit it — works but has
two real costs, and we already felt one of them:

1. **History bloat, permanently.** A binary zip committed on every push is stored forever in
   git history and can never be fully removed without a history rewrite. This is exactly what
   `jarvis.tar.gz` did on day one (2026-07-16) — we removed it from the tip, but it still sits
   in commit `7f57add` forever.
2. **Nesting.** An archive of the repo, committed into the repo, contains the *previous*
   archive, which contains the one before it. It compounds.

So the archive should live **outside git history** — as a build artifact, not a committed
file.

## Recommended approach: a GitHub Release asset built in CI

Fits the existing CI/CD pattern (`.github/workflows/fly-deploy.yml` already runs on push to
`main`). Add a step, after the deploy succeeds, that:

1. Builds a clean archive with **`git archive`**, which snapshots a commit and honors
   `export-ignore` rules (see below) so secrets and noise are excluded by construction:
   ```
   git archive --format=zip -o jarvis-latest.zip HEAD
   ```
2. Publishes it to a **single, stable "latest" GitHub Release** (same tag reused each deploy,
   asset overwritten). The Project — or you — always pulls the same URL, and nothing enters
   git history.

Sketch of the added workflow step (for review, not final):
```yaml
  archive:
    needs: deploy            # only snapshot code we actually shipped
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build clean archive
        run: git archive --format=zip -o jarvis-latest.zip HEAD
      - name: Publish as the 'latest-code' release
        uses: softprops/action-gh-release@v2
        with:
          tag_name: latest-code
          files: jarvis-latest.zip
```

### What `export-ignore` does (the `.env` exclusion)

`git archive` reads `.gitattributes`. Marking a path `export-ignore` omits it from every
archive automatically — set once, correct forever:
```
# .gitattributes
.env            export-ignore
.env.*          export-ignore
docs/Keel/      export-ignore     # optional: keep the archive lean
```
Note: `.env` should already be git-ignored and never committed, so it wouldn't be in
`HEAD` anyway — `export-ignore` is belt-and-suspenders, and the real value is trimming
committed-but-unwanted paths (large docs, fixtures) from the snapshot.

## Alternative considered

- **Post-push git hook writing to a folder outside the repo** (e.g.
  `C:\Projects\_jarvis-snapshots\jarvis-latest.zip`). Simple and local, but it runs on your
  machine, not in the pipeline, so it isn't really "part of CI/CD" and won't fire if a deploy
  happens from elsewhere. The Release-asset approach is machine-independent and tied to the
  actual deployed commit.

## Open questions for the architect

1. **Delivery to the Project.** How should the Project consume the snapshot — a synced repo
   directory, or downloading the Release asset? (You mentioned adjusting the Project's sync
   and possibly adding a directory for the archive.) The Release-asset approach keeps the
   archive out of the synced `docs/` tree, so the sync wouldn't need to carry a binary.
2. **Format.** `.zip` (Windows-friendly, what you produce today) vs `.tar.gz` (smaller).
3. **Scope of the snapshot.** Whole repo, or `backend/` + `ui/` + `docs/` only? Decide what
   `export-ignore` trims.
4. **Trigger precision.** Every push to `main`, or only on a successful *deploy* (docs-only
   pushes skip deploy today — should they also skip the snapshot)? Gating on `needs: deploy`
   ties the snapshot to real code ships.

## Related

- The two-clone / stale-code lesson that motivated this: see the working-copy notes; the
  architect should always start from the deployed code, not a local copy that may have
  diverged.
- CI/CD lives in `.github/workflows/fly-deploy.yml`; deploy is gated on the test job.
