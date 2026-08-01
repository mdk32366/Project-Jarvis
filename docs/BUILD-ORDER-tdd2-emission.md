# BUILD ORDER — TDD #2 (Planning Sessions), Steps 5–7: emission

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Source TDD:** `docs/TDD-planning-sessions.md` (§7.1, §8, §4.2, §4.3)
**Builds on:** PR #58 (gate proven, `emit_tdd` deliberately absent), #54
(`commit_document` — the branch+PR write path emission routes to)
**Type:** Code PR. **Merge-on-green** for the code.
**Precondition satisfied:** the gate is built and proven (#58). Emission may now be
built against it — this is the correct order per §9. `emit_tdd` **refuses on an
incomplete session by calling the gate**; it never re-implements the readiness
logic.

---

## Step 0 — Confirm the two signatures emission depends on

The Planner's tarball predates #54 and #58; read the real signatures, don't trust
the sketch:

- **`commit_document`** (from #54) — confirm its exact parameters
  (`grep -n "def " backend/app/handlers/*.py | grep commit_document`, then read it).
  The order below assumes `commit_document(project, kind, tier, title, body)`
  routing branch+PR — adapt to the real signature.
- **`session_readiness()` / the gate** (from #58) — confirm what it returns
  (bool + missing-slots-with-questions). `emit_tdd` calls this and refuses on
  not-ready; it must not duplicate the check.

Report both signatures before wiring.

---

## Step 1 — `emit_tdd()` (§7.1)

The pipeline, in order. **Refusal is step 1** — nothing composes if the gate says
not-ready.

1. **Gate first.** Call the readiness check (#58). **Not ready → refuse**, return
   the missing slots + their filling questions (the gate already produces these).
   No document composed, no GitHub call. This is the whole reason the gate was
   built before emission — assert (tests) that a not-ready session reaches **no**
   compose and **no** `commit_document` call.
2. **Compose markdown** from the slots — standard TDD structure (problem, goals,
   non-goals, approach, rejected, data model, tests, open questions).
3. **Header banner — non-negotiable, verbatim (§7.1.2):**
   ```
   > Drafted in a JARVIS planning session on <date>. Planner-ready, NOT
   > build-ready — bring to a design session before implementation.
   ```
   This is the KEEL boundary made visible in the artifact. A document that *looks*
   build-ready and isn't is worse than no document. The banner is the first thing
   in the emitted file, every time.
4. **Provenance section (§7.1.3)** — session id, created/emitted dates, channels
   used, note count. This is the evidence a real conversation happened and the
   thing that makes a placeholder-filled doc impossible to pass off as thought-out.
   (It pairs with the gate: the gate refuses thin content, provenance makes thin
   content visible even when it passes.)
5. **Scan before write.** The composed body goes through `scan_for_secrets` before
   `commit_document` — belt to `commit_document`'s own braces. A planning session
   fed from SMS/voice could carry a pasted token in a note. (If `commit_document`
   already scans internally — confirm in Step 0 — this is redundant-but-harmless;
   keep it only if it's not already guaranteed upstream. Do not remove
   `commit_document`'s scan regardless.)
6. **Route by `target`** → `commit_document` → **branch + PR** (never `main`).
   `target=jarvis` → `Project-Jarvis/docs/`; `target=new_project` → the project's
   repo (via TDD #3, created if the flow calls for it — but repo *creation* stays
   its own gated tool; emission commits to an existing repo).
7. **`attach_document(project, kind='tdd', tier='live', ...)`** — reuse the
   existing tool (do not re-insert a ProjectDocument).
8. **Session → `emitted`**, set `emitted_at`, `document_id`.

### 1.1 Never commits to `main` (§7.1)

A PR is reviewable and revertible; that is the entire point of the Planner/Builder
seam existing. Assert no emit path targets `main` — same structural test as #54's
never-merges (no merge call, base is default branch, head is the doc branch).

---

## Step 2 — `next_planning_question()` (§4.3)

The interrogation loop's single highest-value move: return the **one** missing slot
whose filling most advances readiness, phrased as a question. Not the whole missing
list (that's `planning_status`) — the *next* question, so the interview has forward
motion. Reads the gate's missing-slots output and picks the highest-value gap.

Test: a session missing `rejected` and `open_questions` returns one focused
question, not a dump.

---

## Step 3 — Voice cannot emit (§4.2) — the channel constraint

Capture and interrogation are voice-reachable (owner can add notes and be
interrogated from the dock). **`emit_tdd` must NEVER be voice-reachable.** #58 left
a comment at the allowlist marking this; honor it.

- `emit_tdd` is **not** in `VOICE_TOOLS_PHASE1`, and (like `commit_document`)
  registered so it's structurally excluded from a voice-restricted registry —
  fail-closed, not filtered.
- Rationale: emission is an irreversible-ish outward write (a public PR, under the
  ratified public-default). A spoofable-channel path to it is the exact
  blast-radius class the allowlist contains. Same reasoning as `commit_document`
  and `create_project_repo`.

Test: `emit_tdd` absent from the voice allowlist; a voice-restricted registry does
not contain it; the allowlist comment is present.

---

## Step 4 — `planning_sessions` health check (§8)

A `Component` + check for planning-session health. Exception-first, and — like
`github_writes` — **never `down`** (a stuck planning session is not a system
fault). Likely signals: a session `open` far longer than expected, or emission
failures. Fault codes each get a runbook (join guard). Reads planning-session
state, not `actions_audit`.

Keep this modest — the valuable half is emission (Steps 1–3). If the health check
balloons, split it to its own order rather than bloating this PR.

---

## Step 5 — Tests

- **Not-ready session refuses emission — no compose, no `commit_document` call.**
  The gate-before-emission proof at the emit layer: patch the GitHub client, assert
  zero calls when the session is incomplete. The sharpest test.
- **Ready session emits** — complete session → banner present as first content,
  provenance section present, branch+PR opened, `attach_document` called, session
  `emitted`.
- **Banner is verbatim and first** — assert the exact banner text at the top of the
  composed doc.
- **Provenance is present and accurate** — session id, dates, channels, note count
  match the session.
- **Never `main`** — every emit is a branch+PR; base is default, head is the doc
  branch; no merge call.
- **Scan runs before write** — a note containing a token → emission aborts (or
  `commit_document`'s scan aborts it), no PR, value not echoed.
- **`emit_tdd` not voice-reachable** — absent from allowlist, absent from a
  voice-restricted registry.
- **`next_planning_question` returns one question**, not the full missing list.
- **Health check never `down`**, runbook join passes.

---

## Guardrails

- **Gate is called, not re-implemented.** `emit_tdd` refuses by asking the #58
  gate. If you find yourself re-writing readiness logic, stop — call the existing
  check.
- **Never `main`, never voice.** The two structural constraints. Both asserted as
  absences, per house pattern.
- **Reuse `commit_document` and `attach_document`** — do not re-implement the
  write or the ProjectDocument insert.
- **Living-document rule** — adds `emit_tdd`, `next_planning_question`, a health
  component. Update `docs/ARCHITECTURE.md` (tool + component inventory) same PR.
  This completes TDD #2 — mark it complete in the doc.
- **No migration** — #58's `0027_planning_sessions` already has the tables. If
  you're writing one, stop and report why.
- **Run both suites before pushing.**

## Report back

- Both Step-0 signatures, and whether the wiring matched or adapted.
- That "not-ready refuses — client never called" is present and green — the
  gate-before-emission proof at the emit layer. Call it out.
- That the banner is verbatim-first and provenance is accurate, asserted.
- That `emit_tdd` is structurally voice-excluded (absent from allowlist AND
  restricted registry), asserted.
- Never-`main` asserted.
- **TDD #2 status: complete** — steps 1–7 across #58 + this PR. Inception (the
  capstone) is next, and rebases its migration off `0027`.

Merge-on-green once CI is green and ARCHITECTURE.md is updated.
