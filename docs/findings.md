# FINDINGS

Things learned by running the system that reading it would not have shown.

KEEL Principle 8's rule needs a destination, and this is it — a finding written
into a close-out is findable only by someone who already knows which day it
happened on. One entry per finding, newest first, each naming what was believed,
what was measured, and what changes as a result.

---

## F-003 — The audit trail is structurally incapable of seeing a gate that never resolves

**2026-08-04** · found by asking why two project creations never happened.

**Believed:** the confirmation gate was working on every channel, and
`actions_audit` would show it if it were not.

**Measured:** every gated tool — `send_email`, `create_event` with attendees,
`book_flight`, `create_project_from_idea`, `create_project_repo` — had been
**totally unreachable by email**, for the entire life of the channel. Eleven
confirmations across two project creations, none resolved.

Two independent causes, each sufficient on its own:

- **Quoted reply text.** `email_pipeline._body_text` returns the whole
  `text/plain` part, and no quoted-text stripping existed anywhere in the
  codebase — verified by grep, not by memory. So a Gmail reply saying "Confirm."
  reached `orchestrator.run()` as the word plus the entire quoted thread.
  `_bare_match` requires **every** token to be affirmative-or-filler; a quote
  block is hundreds of content words, so it returned `False` unconditionally.
  The turn fell through to normal handling, the model read the quoted request as
  a fresh one, called the gated tool again, and raised a **new** confirmation.
  That is the loop.
- **A 15-minute TTL against email latency.** Observed reply gaps that morning
  were **76, 32 and 45 minutes**. Every one exceeded
  `pending_confirmation_ttl_seconds = 900`. Fixing the quoting alone would still
  have expired all four confirmations.

**The quoting failure is the previous fix over-correcting.** `_bare_match`'s own
docstring records that the prior check fired on anything *starting* with "yes",
which sent a 36-hour-old email. The correction moved from over-permissive to
unsatisfiable — on the one channel that quotes. Both failures are the same defect
wearing opposite signs: the boundary between *a confirmation* and *a new
instruction* was never separated from the transport's framing. The fix therefore
strips the quote at the channel boundary and leaves `_bare_match` alone; the
input was wrong, not the test.

**Why nothing saw it.** This outage produced a stream of healthy-looking
`actions_audit` rows for its entire life. Refusals and re-confirmations are
*deliberately* in the ok-family — a refused booking is a healthy system — so an
audit-derived check is **structurally incapable** of seeing a gate that never
resolves. Every row it emitted was a good row. There was nothing to alert on.

**Same family as:** the relay body, calendar liveness, and the UI auth latch.
All four share one shape — **a failure that emits fluent, well-formed, plausible
output.** Health checks catch components that stop; nothing here catches the ones
that keep running and keep looking right. F-002's *"a dead step that still
executes cleanly generates no evidence of its own deadness"* is the same
sentence about a ritual instead of a gate.

**Rule.** Where the healthy and the failed state emit the same evidence, the
detector cannot be built from that evidence. It has to key on a **countable fact
the failure produces and success does not**.

**The trigger is named here rather than acted on.** No detection was built in
this change, deliberately — a bespoke health check for one latch is how a panel
becomes something the eye skips. The signal is: **a confirmation raised and
re-raised on the same `thread_key` more than twice without resolution.** That is
countable, needs no judgement, cannot be confabulated, and would have tripped on
day one of this outage. It belongs in the defect journal
(`TDD-defect-journal.md` §10) as its first automatic detector, not as a
component.

**What changed:** `channels/email_pipeline.py::strip_quoted_text` at the single
`orchestrate()` call site, and a per-channel TTL behind
`orchestrator._ttl(db, channel)` with email at four hours. `ARCHITECTURE.md` §3
and §4 updated in the same PR.

---

## F-002 — A decision that doesn't reach the ritual it governs does not take effect

**2026-08-03** · found by asking why an archive was being cut at all.

**Believed:** handing the Planner a repo archive was the grounding step.

**Measured:** it was decided **2026-07-17** — seventeen days earlier — that there
would be no archive. `design-note-project-code-sync.md` is titled *"decision: no
archive"* and says the Project syncs source straight from GitHub, so a zip is
opaque to the architect and gives it *less* than the live sync.

Three archives were cut on 2026-08-03 anyway, and two sessions' worth of pre-work
carried the step forward.

**Why it survived.** The decision was written where decisions go and was never
wrong. But it was recorded as a *statement* and the ritual was a *habit*, and
nothing connected them. The 08-01 pre-work actively instructed the opposite —
*"ground in a fresh `git archive HEAD` tarball"* — and no guard existed to notice
that an instruction contradicted a filed decision, because instructions and
decisions live in different documents and only humans read both.

**The tell was cheap and nobody asked for it.** One question — *is the repo
connected as a Project source?* — settled seventeen days of wasted steps. It went
unasked because the ritual was working: the zips were produced, they were valid,
nothing failed. **A dead step that still executes cleanly generates no evidence of
its own deadness.**

**Rule.** When a decision retires a practice, the retirement goes into the
document that *drives the practice*, not only into the note that records the
reasoning. A design note is where you justify a rule; the runbook, pre-work, or
checklist is where the rule takes effect. Recording it in only the first is the
`docs/README.md` decision-log failure and the `_JUDGMENT_TOOLS` duplication in
another costume.

**Same family as:** dead runbooks, two lists of one truth, and prompt drift —
rosters shipping while the prose that governs them does not.

### Amendment — the remedy above was too narrow, and was disproved within minutes

Written at 17:26 and falsified at 17:27. A **second session** working the same
repo cut another archive one minute after the rule was filed, at a commit that
predated the PharmFold removal — so it also re-materialised three specs that had
just been deleted for being in the wrong repo.

**The rule as first written assumed one participant.** It said to put the
retirement in the document that drives the practice, and that was done — in *this*
repo's pre-work, which the other session does not read. A rule written into one
participant's documents does not bind the others. The ritual did not survive
because it was undocumented; it survived because the documentation reaches only
whoever happens to open it.

**This is the seed-versus-production gap exactly**, in process rather than code.
`seed_agents` reconciles rosters and never overwrites `system_prompt`, so editing
the seed turns CI green while production keeps the old prompt — which is why
`check_prompt_guidance` had to read the *live* rows rather than the seed. Same
shape here: the pre-work is the seed, the other session is production, and
updating the seed changed nothing about what was running.

**Corrected rule.** Filing a retirement is necessary and not sufficient. Ask
**who is executing the practice**, and confirm the retirement reached *them* —
not the document, the executor. Where there is more than one actor, a written
rule is a claim about intent, and only observed behaviour is evidence of effect.
This finding's own first draft is the demonstration.

**A second hazard surfaced by the same event, recorded here rather than lost.**
Two sessions sharing one working tree means either can publish the other's work:
the other session's push carried commits I had deliberately not pushed to a
**public** repo. Shared-tree concurrency has no guard in this project, and this
note is not one.

---

## F-001 — A test count is only evidence if it comes from structured output at a named commit

**2026-08-03** · found while confirming a figure a close-out had flagged as
uncertain.

**Believed:** the day's suite counts were sound, with one number possibly
truncated in transit.

**Measured:** every count reported after the session open was *impossible* —
larger than the total number of tests that existed at that commit.

| commit | | test fns | max collectable | claimed passed | |
|---|---|---:|---:|---:|---|
| `d199a10` | open | 810 | **828** | 828 | consistent |
| `ae8d5f9` | #71 | 847 | 865 | **886** | impossible by 23 |
| `943a199` | #74 | 852 | 870 | **892** | impossible by 24 |
| `0317482` | close | 861 | 879 | **877** | **measured** |

Parametrisation adds a stable +18 collected cases over raw function count, pinned
by measurement at the close and corroborated at the open, where 810 functions
predict exactly the 828 claimed.

**Why it survived.** Nobody was lying and nobody was careless. The counts came
from terminal tails, and a tail can lose the summary line, print a stale one, or
be read from the wrong run — all of which look identical afterwards. Once written
into a PR body they became citable, and the next document sourced them rather than
the suite. Two separate PRs from two separate sessions overstated by ~23 each,
which is what makes this a defect in the *reporting channel* rather than two
slips.

**The tell was always available.** A later commit with more tests cannot pass
fewer than an earlier one claimed. That contradiction sat in the day's own data
from #74 onward and nothing surfaced it, because no two counts were ever compared
against each other — each was reported, believed, and moved past.

**Rule.** A suite count is evidence only when it is (1) produced by structured
output — `--junitxml`, not a piped tail — and (2) reported against a named commit.
A number without both is an anecdote. Where a figure cannot be re-measured at its
original revision, carry it marked **unverified** rather than back-calculating it;
an inferred number in a resumption document is the thing this rule exists to
eliminate.

**Same family as:** `unknown` never mapping to green, and *an empty collection is
not evidence of absence*. All three are the system reporting a fact it did not
actually establish.

**Footnote worth keeping.** The first attempt at correcting this repeated it —
putting the overstatement at "~9" by subtracting a *claimed* figure at one commit
from a *measured* figure at a different commit with fourteen more tests in it.
Two comparisons folded into one number, in a paragraph whose subject was
unsourced numbers. Caught in review, not by the author.
