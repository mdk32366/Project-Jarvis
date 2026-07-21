# TDD — Repo Scaffolding & Document Commits

**Status:** Draft, ready to build
**Date:** 2026-07-21
**Series:** 3 of 3 (project tracking → planning sessions → **repo scaffolding**)
**Depends on:** TDD #1 (`project`, `project_document`), TDD #2 (emission),
existing `GITHUB_TOKEN` and the ideas commit path

---

## 1. Problem

JARVIS can produce a design document (TDD #2) and track a project (TDD #1), but
has nowhere to put the document. It needs to land in version control, in the
right repo, in the right tier of the `docs/` convention.

Two destinations, established by Matt on 2026-07-21:

- **A new capability for JARVIS** → `Project-Jarvis/docs/`. KEEL applies exactly
  as it does today.
- **A new project** → a **new repo**, scaffolded with the standard structure,
  document in that repo's `docs/`.

The second requires **repo creation**, which is a materially larger permission
than the file writes JARVIS does today.

## 2. Goals

1. Commit a document to `Project-Jarvis/docs/` on a branch, as a PR.
2. Create and scaffold a new project repo with the standard structure.
3. Enforce the `docs/` tier convention — `live` / `archive` / `operational` — at
   write time, not by hope.
4. Record what was written in `project_document` (TDD #1) so the tracker and the
   repo cannot silently diverge.

## 3. Non-goals

- Committing code. Documents only. JARVIS writes designs, not implementations —
  the Planner/Builder split again.
- Merging PRs. A PR is opened and left for review. **JARVIS never merges.**
- Deleting repos, files, or branches. There is no destructive path here at all.
- Managing collaborators, settings, or branch protection beyond initial creation.

---

## 4. Design

### 4.1 Two operations, two risk profiles

**Document commit** — reversible. A branch and a PR; nothing touches `main`
without human review. Ungated, consistent with the existing ideas commit path
which already writes to GitHub without a gate.

**Repo creation** — **gated.** Creating a repo under Matt's account is not
reversible in the way a PR is: it takes a name permanently, it is visible, and
undoing it is a manual deletion. This is squarely the irreversible-action class,
and it gets the standard treatment.

The gate is on creation only. Once a repo exists and is recorded on the project,
document commits into it are ungated like any other.

### 4.2 Token scope — a real prerequisite

The existing `GITHUB_TOKEN` has `repo` scope, set for the ideas commit sink.
**`repo` does not permit repository creation.** A classic PAT needs `public_repo`
for public repos, or full `repo` plus account-level create permission; a
fine-grained PAT needs explicit "Administration: read and write."

Therefore:

- **`GITHUB_TOKEN`** — unchanged, retains `repo`. Used for all document commits.
- **`GITHUB_ADMIN_TOKEN`** — new, narrower audience, create permission only. Used
  *solely* by the repo-creation path.

Two tokens rather than upgrading one, deliberately. The high-privilege token is
reachable from exactly one gated code path, so the blast radius of the common
path stays where it is today. Upgrading the existing token would silently grant
creation rights to every existing GitHub call site.

Owner action: generate on desktop, set as a Fly secret, record in the password
manager at creation. Fly secrets are write-only once set.

### 4.3 Repo visibility — KEEL doctrine

New project repos are created **public** by default, per current KEEL doctrine:
the Planner AI (browser chat) can only connect directly to public repos, so a
private repo on day one cannot be brought into a session like this one.

The go-private trigger is unchanged and is **owner action, not automated**: first
real credential, first real user data, or first live deploy — whichever comes
first. JARVIS does not flip visibility.

**Consequence that must be enforced, not assumed:** a public repo means every
document committed is public at the moment of the commit. §4.5.

### 4.4 The scaffold

Mirrors the structure proven in `Project-Jarvis`:

```
README.md
ARCHITECTURE.md
docs/
  README.md          ← carries the convention itself
  archive/.gitkeep
  operational/.gitkeep
.gitignore
```

`docs/README.md` is the important file. It carries the convention verbatim,
including the organizing principle:

> Files are sorted by whether they are **live**, **superseded**, or **spent** —
> not by topic. `docs/` holds live design records and active references.
> `docs/archive/` holds superseded documents, each with a banner naming what
> replaced it. `docs/operational/` holds executed handoffs and checklists.
>
> Commit the design before the work is done.

The scaffold is a **stored, versioned template** in `Project-Jarvis`, not
reconstructed from the model's memory per invocation. A structure regenerated
from memory each time will drift, and drift in the thing whose entire job is
preventing drift is a special kind of failure.

### 4.5 Secret scanning — mandatory, pre-commit

Every document is scanned **before** the commit call, not after:

- high-entropy strings above a length threshold
- known token prefixes: `ghp_`, `github_pat_`, `duffel_`, `sk-ant-`, `AC` +
  32 hex (Twilio SID), `xoxb-`, `AIza`
- anything matching the values of known Fly secret names, if resolvable
- private key headers

A hit **blocks the commit** and reports the match location without echoing the
matched value.

This is not optional caution. KEEL's own doctrine is that building public makes
secrets discipline *structurally enforced rather than optional*, and the
automatic scanner belongs in the gate. A machine that writes to public repos
without one is the exact hazard that doctrine names.

---

## 5. Data model

No new tables. Additions only:

- `project.repo_url` (already in TDD #1 §5.1) — set on creation
- `project_document.url` — set on successful commit
- `github_write_log`: `id`, `operation` (`create_repo` / `commit_doc` /
  `open_pr`), `target`, `ref`, `ok`, `error`, `created_at`

The write log exists so a failed or partial write is diagnosable after the fact.
Everything else here reuses TDD #1's schema.

Migration `0024_github_writes.py`.

---

## 6. Tools

| Tool | Gated | Notes |
|---|---|---|
| `commit_document(project, kind, tier, title, body)` | no | branch + PR |
| `create_project_repo(project, name, visibility='public')` | **yes** | §4.1 |
| `list_project_repo(project)` | no | read |

### 6.1 `commit_document`

1. Scan for secrets (§4.5). Abort on hit.
2. Resolve destination repo: `Project-Jarvis` when the project is JARVIS herself,
   otherwise `project.repo_url`. **Abort if unresolvable** — never guess a repo.
3. Resolve path from tier: `docs/`, `docs/archive/`, `docs/operational/`.
4. Branch `docs/<slug>-<yyyymmdd>`.
5. Commit, open PR, **never merge**.
6. `attach_document(...)`, record in `github_write_log`.

Path resolution from tier is the enforcement point for the convention. A caller
cannot write an archive document into `docs/` because it does not supply the
path — it supplies the tier.

### 6.2 `create_project_repo`

Gated: confirmation with readback of repo name, visibility, and owner before
anything is created. Uses `GITHUB_ADMIN_TOKEN` exclusively.

1. Create repo (`public` default, §4.3)
2. Commit the scaffold as an initial commit
3. Set `project.repo_url`
4. Log

**Idempotence:** if the repo already exists, do not fail destructively and do not
attempt to overwrite. Report it, set `repo_url` if unset, stop. A half-created
repo from a network failure must be recoverable by re-running.

---

## 7. Health check — `github_writes`

- `ok` — no failed writes in the trailing 7 days
- `degraded` — any `ok=false` in `github_write_log` in 7 days
- `unknown` — no writes ever
- never `down` — inability to commit a document is not a system fault

Remediation runbook: token validity and scope, rate limit, repo existence,
branch conflict.

---

## 8. Build order

| # | Work | Testable |
|---|---|---|
| 1 | Migration 0024, `github_write_log` | ✅ |
| 2 | **Secret scanner** — standalone, first | ✅ |
| 3 | `commit_document` → `Project-Jarvis/docs/` | ✅ (mock API) |
| 4 | Scaffold template stored in repo | ✅ |
| 5 | `create_project_repo`, gated | ✅ (mock) then live |
| 6 | Health check + wire TDD #2 emission | ✅ |

Scanner before any write path exists. A writer built first is a writer that works
without the scanner, and then the scanner is an addition rather than a
precondition.

---

## 9. Test plan

- **Secret scanner catches each prefix** — `ghp_`, `duffel_`, `sk-ant-`, Twilio
  SID, private key header. One test per pattern.
- **Scanner blocks the commit** — a document containing a token produces no API
  call at all. Assert the client was never invoked, not merely that it returned
  an error.
- **Scanner does not echo the secret** — assert the matched value is absent from
  the error message and from `github_write_log`.
- **Tier → path** — `live`→`docs/`, `archive`→`docs/archive/`,
  `operational`→`docs/operational/`. A caller-supplied path is ignored.
- **Never targets `main`** — assert every commit is on a `docs/` branch.
- **Never merges** — assert no merge call exists on the path. Asserted in test.
- **Unresolvable repo aborts** — project with null `repo_url` and target
  `new_project` → abort, no API call, no guess.
- **Repo creation is gated** — ungated invocation refuses; assert nothing created.
- **Creation idempotence** — existing repo → reports, sets `repo_url`, does not
  overwrite the scaffold.
- **Admin token isolation** — assert `commit_document` never reads
  `GITHUB_ADMIN_TOKEN`. This is the §4.2 argument; assert it rather than trusting
  it.
- **Scaffold completeness** — created repo contains all files from §4.4, and
  `docs/README.md` contains the convention text.

---

## 10. Open questions

- **Repo naming.** Derive from project name, or ask? Deriving is smoother;
  asking is safer given the name is permanent. Lean: propose a derived name in
  the gate readback, let Matt override. The gate makes this nearly free.
- **Does a new project repo need a CI gate from day one?** KEEL says the
  foundation comes before features, which argues for scaffolding the gate
  workflow too. Deferred from v1 — a docs-only repo has nothing to test, and a
  gate that tests nothing is theatre. Revisit when a scaffolded repo first gets
  code.
- **Scanner false-positive rate.** Aggressive entropy detection will flag base64
  in a design document. Start with prefix matching plus a conservative entropy
  threshold; tune from real refusals rather than from imagination.
- **Should `Project-Jarvis` document commits go through the same gate as code?**
  Currently no — they are docs, on a branch, reviewed as a PR. If a document
  commit ever lands in a path that CI reads, revisit.
