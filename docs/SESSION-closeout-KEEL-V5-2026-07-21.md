# SESSION CLOSE-OUT — KEEL V5 (2026-07-21)

**Project:** KEEL Curriculum
**Session type:** Planning (browser Project). No code in this repo changed.
**Why this file exists:** the KEEL arc had **no committed record anywhere**. When
the project backfill ran, four of five arcs were reconstructable from close-outs
in `docs/`; KEEL was not, because its entire state lived in a chat session and in
`.docx` files outside this repo. That gap is the reason this document exists, and
closing it is itself an instance of what KEEL now teaches.

---

## What KEEL is

A teaching curriculum for peers who want to build software with AI tools and real
engineering discipline. Seven artifacts. Not code — the deliverables are
documents, currently generated as `.docx` and living outside `Project-Jarvis`.

## What happened this session

V5 of five artifacts. Started as a merge conflict: the owner's uploaded V5 drafts
had **reverted the public-repo doctrine** established in V3 (private repo +
`git archive` tarball ritual). Diagnosis was that the V5 edits had been made
against a pre-V3 base and the public-repo change was lost in the merge — not a
deliberate reversal. Confirmed by the owner, then merged forward: V5's new
content grafted onto V3's public-repo doctrine.

### Content added in V5

- **Principle 8 — evidence.** Every claim names the artefact it came from; ask
  what is *available* not what is *on*; ask for the breakdown, not the total.
  Sourced from PharmFold: three confidently-written claims reversed in one
  afternoon, each true as stated and wrong in what it implied.
- **Principle 9 — binding checks.** The gate stops the deploy; only branch
  protection stops the human. A red X on a mergeable PR is decoration.
- **Principle 10 — build public, go private when production-stable.** Planner can
  only connect to a public repo; Builder reads private fine. Tripwires, whichever
  first: first real credential, first real user data, first live deploy.
- **Principle 7 rewritten offensively.** Was defensive ("so you don't chase a bug
  already fixed"). Now: *you are building an asset, not a receipt.* Adds the
  D-NNN convention, the rejected-options and evidence fields, and the recovery
  mechanic — "we were sound up to D-023" as a navigable coordinate. A git history
  for the design.
- **The Planner-will-try-to-build warning**, added to the two-AI section of the
  Checklist, the AI section of the Pattern doc, and the Quick Card. Prompted by a
  live failure in this same session (see below).
- **Exploratory vs constructive work** as a judgment call, not a rule. Surviving
  assumptions from an exploratory phase become the premises of the TDDs built
  after it.

### Artifacts at V5

Pattern doc, Day-One Checklist, Quick Reference Card, Demo Repo Setup Guide,
Live Demo Script. Checklist went 14 → 15 steps (public repo forces a connect
step); Pattern went 9 → 10 principles.

**Still at V3:** the deck and the How-It-Works SVG. Both now materially
understate the framework — the deck shows 9 principles, 14 steps, no override
moment, no decision log.

## Decisions taken

**Decision: hold decision-ordered work as universal doctrine.** PharmFold ran
decision-to-decision (D-001…D-nnn, one file each) rather than TDD-to-TDD, and it
worked well. The owner's instinct was to promote that to doctrine.

- *Rejected:* promoting it now on PharmFold's evidence alone.
- *Why:* one project, one shape. PharmFold was exploratory — questions genuinely
  open, answers found by experiment. JARVIS is largely constructive. The
  counter-evidence is the project in this repo, built the other way, running
  daily. Plausible confound: PharmFold went well because decisions were backed by
  *research rather than supposition*, which helps under any document scheme.
- *Kept instead:* the D-NNN convention and the evidence requirement, both
  transferable regardless of project shape. Exploratory-vs-constructive taught
  as a judgment call.
- *Revisit when:* FFIS retrofit (a third shape — retrofit onto existing work) and
  Sentinel produce evidence from other regimes. Promote to doctrine in V6 if it
  holds.

**Decision: put the Planner-overstep warning in the two-AI section, not
Principle 7.** Owner initially asked for Principle 7.

- *Rejected:* amending Principle 7.
- *Why:* Principle 7 is about the decision log. The Planner overstepping is a
  *role* violation. Different failure, and stapling it onto 7 dilutes the
  principle just sharpened.

## The live failure that produced the new warning

Mid-session, the Planner was given a repo snapshot and asked to address defects.
It **edited working source files directly** — `briefing.py`, `test_briefing.py` —
and produced finished, plausible, merge-ready output. The owner caught it:
*"Should I feed this to Code? That's how we always work."*

The artifacts warned that the Planner *cannot* build. They did not warn that it
*will try*, given file access, and produce something good enough to merge without
a gate. The output was written against a stale tarball, had never been run, and
its tests were imagined rather than executed.

Rule now in the artifacts: **a Planner's output is a build order, not a merge.**
Finished code from the Planner is a signal it overstepped — keep the reasoning,
bin the artefact.

Corroborated three more times the same session: the Planner reported three Admin
defects as open when two were already fixed and one was mis-specified; it
asserted a `[not configured]` sentinel shape across three netstatus handlers when
two were Phase-1 stubs with no such path; and it read a whitespace difference in
a terminal paste as a code defect and recommended editing a correct file. All
three caught by the Builder reading live bytes. The pattern is consistent — the
Planner is confident and readable and wrong about current repo state, and only
the Builder can settle it.

## Milestones (as seeded into project tracking)

| # | Milestone | State |
|---|---|---|
| 1 | V3 of all seven artifacts | done |
| 2 | V5 core artifacts | done |
| 3 | Update deck to V5 | open |
| 4 | Update How-It-Works SVG to V5 | open |
| 5 | FFIS retrofit as a real work session → Migration Guide | open |
| 6 | Sentinel — prove the Migration Guide twice | open |
| 7 | `gate-demo` repo as unsinkable fallback demo | open |

**Provenance note (Principle 8 applied to this file):** milestones 3–7 come from
this planning session and the owner's stated intent, not from a prior committed
document. Milestones 1–2 are verifiable against the generated `.docx` artifacts.
This is the first committed record of the KEEL arc.

## Next

1. Deck and SVG to V5 — they are the presented surface and the most out of date.
2. FFIS retrofit → Migration Guide. Doubles as the evidence test for
   decision-ordered work on a third project shape.
3. Sentinel, twice, to prove the guide.
4. `gate-demo` per the Setup Guide — build it once, prove it once, keep it.

## Open question carried forward

Whether project *type* (exploratory / constructive / retrofit) is a property of
the project or of the phase. Current read: phase, and the skill is recognizing
which one you are in. Deliberately not taught as a taxonomy in V5 — evidence
first. FFIS and Sentinel are the scheduled evidence.

## Postscript — an unrelated incident worth recording here

While recovering a lost `DATABASE_URL` during the same session, the Planner
suggested `fly ssh console -a jarvis-mdk -C "printenv DATABASE_URL"`, which
printed the live production Postgres password in plaintext into a chat log. The
credential was rotated immediately.

The lesson generalizes beyond this repo and belongs with KEEL's Principle 4:
**"write-only after being set" is a weaker guarantee than it sounds.** A secret
store that will not show you a value still hands it back through any process that
holds it in its environment. Anyone hunting a lost connection string will reach
for exactly that command. Prefer `fly proxy` plus a value set into a shell
variable without echoing, or run the job where the value already lives.
