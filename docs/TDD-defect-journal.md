# TDD — The defect journal and the weekly pre-work digest

**Status:** draft, for ratification.
**Motivating request, 2026-08-04:** *"She needs the ability to log potential bugs
against herself. The bugs should be in a bug pre-work document that can be provided in
an email for me to download and stage with the repo. Could be a weekly thing on
Thursday maybe."*

---

## 1. The invention is the evidence requirement; everything else is plumbing

This feature's failure mode is **fabrication**, and it is worse here than anywhere else
in the system.

Asked to "log a bug against yourself," a language model will produce a fluent,
structurally perfect, plausible, entirely invented defect. The completeness gate
(`app/planning.py::session_readiness`) exists because on 2026-07-20 JARVIS produced a
TDD with every section present and every section a placeholder. **A bug report's shape
is even more trivially generatable than a TDD's** — it has four fields and they can all
be confabulated from a vague sense that something felt off.

And the consequence is worse than a bad TDD, because of where this document goes. If
one fabricated item lands in a document staged into the repo, the owner can no longer
trust *any* item without re-deriving it, and re-deriving all of them costs more than
having written them himself. **The channel is worth nothing the moment it is worth
checking.**

> **The rule: a defect row requires a verbatim observation, and JARVIS cannot file
> without one.** What she did, what she expected, what actually happened. No
> observation, no row. The refusal says so.

This is exactly parallel to `risks` and `assumptions` being inception's unfakeable
pair: *you cannot generate a real risk from a project name*, and you cannot generate a
real defect from a feeling. And it is parallel to `planning_note` being **append-only
and verbatim** — the raw capture is the evidence that something actually happened, and
evidence the judged party can edit is not evidence.

**The observation is stored verbatim and is never rewritten.** No LLM summarisation of
it, at capture or at digest time. Summarising the evidence is how the evidence stops
being evidence.

## 2. She records facts, not severity

`docs/ARCHITECTURE.md` records the doctrine on any surface commenting on the owner's
work: **fact, never judgment**, enforced in `project_timeline` by an enumerated
`JUDGMENT_WORDS` list asserted absent, so breaking it requires consciously deleting a
list.

This surface comments on *her own* work, which weakens the argument but does not
reverse it. A P0/P1 label is a triage decision, triage is the Planner's, and a severity
field would be the one part of the row generated rather than observed.

**No severity. No priority. No "this is probably minor."** She records what happened.
The owner triages when he stages.

**Impact is recorded only as observed** — *"the confirmation never resolved and the
action did not run"* is an observation. *"This blocks all gated tools"* is an
inference, and inferences belong in the staging conversation, not the row.

## 3. The area keys to the health topology

`area` is not free text. It must be an existing `component.name` from the health
inventory, or the literal `unknown`.

**Free text lets her file defects against subsystems that do not exist.** Keying to the
component inventory means a bug filed against a real thing is joinable to that thing's
runbook and its health history, and a bug against a made-up subsystem cannot be
written at all. This is *key on the fact you want, not a proxy that correlates with
it* — the F-001 family — applied at the schema.

`unknown` is a legitimate value and must not be discouraged. An observation she cannot
attribute is still an observation. What is forbidden is a **confident wrong**
attribution, which is the note-classification rule (`fails to unclassified, never to a
guess`) restated.

## 4. Schema

New table `defect`. Draft migration `0030` — **confirm against `alembic heads` at Step
0**; this arc has produced five stale migration numbers and the standing rule is to
confirm against the live head, never a draft. Note the collision risk with
`TDD-repo-fork.md`'s `Project.upstream_url`, which also drafts as `0030`; whichever
lands second takes the next number.

| Column | Notes |
|---|---|
| `id` | |
| `title` | short, one line |
| `observation` | **NOT NULL, non-empty at the column** — see §1 |
| `expected` | what she expected to happen |
| `actual` | what happened |
| `area` | FK-ish to `component.name`, or `unknown` (§3) |
| `source_channel` | where it was noticed |
| `status` | `open` / `staged` / `dismissed` |
| `first_seen_at`, `last_seen_at` | |
| `occurrences` | incremented on re-observation (§5) |
| `digest_count` | how many digests it has appeared in |
| `dismissed_reason` | NOT NULL when status is `dismissed` |

**`observation` is NOT NULL at the column, not merely in the tool.** Same reasoning as
`replan.reason` and `baseline_reset.reason`: enforcing it only in the tool leaves the
empty row one direct write away, and the direct write is what happens during a
migration or a backfill script when nobody is watching.

**`dismissed_reason` NOT NULL when dismissed** for the same reason `parked` requires a
reason on projects: a dismissal without one is indistinguishable from a defect that was
quietly lost.

## 5. Duplicates — the thing that kills this feature if unhandled

She will notice the same defect every day. A digest that repeats the same five items
every Thursday is one the owner stops opening, and a digest nobody opens is worse than
none because it creates the belief that defects are being tracked.

Same reasoning as `project_slippage_brief_days` defaulting to 2 rather than 0 (*a
one-day slip is noise, and a brief that reports noise is one the owner stops reading*)
and the Fly balance suppression under autopay.

**Dedup on `(area, normalised title)`** at capture: an existing `open` row increments
`occurrences` and stamps `last_seen_at` rather than inserting. The observation of the
*new* sighting is appended, not replaced — a defect seen five times has five
observations, and the fifth may be the one that identifies it.

**Items do not vanish after one send.** Ratified position (§9.3): an item keeps
appearing until the owner acts, and the digest separates **new since last digest** from
**carried**, with an age and an occurrence count on each carried item.

The reasoning: if appearing once retired an item, a lost email silently drops a defect,
and the owner cannot tell the difference between "nothing new" and "the pipeline
broke." That is the unwatched-instrument shape. Carrying the item is the cost of not
having that ambiguity.

## 6. Compose and deliver are separate

The split already exists in `briefing.py` — `compose_briefing` builds, `send_briefing`
delivers — and it is the right shape here for a structural reason, not a stylistic one.

| | |
|---|---|
| `defect_digest` (tool) | **Ungated.** Composes and **returns** the markdown. Writes nothing outward. Voice-reachable for the summary form. |
| the weekly job | Delivers it by email to the owner's own address |
| `log_defect`, `list_defects`, `dismiss_defect`, `mark_defect_staged` (tools) | **Ungated** — reversible bookkeeping, and diluting the gate with reversible work is how a gate stops being read |

Because `defect_digest` only returns text, "email me that" routes through the existing
**gated** `send_email`, and the gate is preserved without the digest tool needing to be
gated itself.

## 7. Delivery — attachments, and a boundary that matters

`backend/app/notifier.py::send_email` builds a bare `MIMEText`. **There is no
attachment support anywhere in the codebase.** "Download and stage" needs one, so this
becomes `MIMEMultipart` with an optional attachment list.

> **The notifier gains an attachment path. The gated `send_email` TOOL does not
> expose it.**

This boundary is the security control in this TDD. An attachment parameter reachable
from the tool surface is an arbitrary outbound-file primitive available to a model that
reads untrusted web content and untrusted inbound email. The `book_flight` /
`append_to_google_doc` pattern — an action is possible only against an ownership row
JARVIS created herself — exists for exactly this class of hazard.

The digest job calls `notifier.send_email` directly with the attachment, as
`send_briefing` already does. That path is system-initiated, to the owner's own
address, with content the system generated. The tool path stays text-only.

Attach as `.md`, and **also inline the document in the body**. Attachments get stripped,
quarantined, and lost on phones; the body is the copy that survives.

## 8. The weekly enqueuer

Mirror `backend/app/workers/job_worker.py::_briefing_tick` — a per-tick enqueuer, not a
cron, so a schedule change from the runtime overlay takes effect within a tick with no
restart.

**Guard on ISO week, not date.** `hb.last_briefing_date != now.date()` is correct for a
daily job. For a weekly one the guard is `(iso_year, iso_week)`, so a worker down all
Thursday still fires on Friday rather than losing a week of pre-work. **A missed daily
brief costs a day; a missed weekly digest costs a week**, and the catch-up window
should reflect that.

Settings on the runtime allow-list: `defect_digest_enabled`, `defect_digest_weekday`
(0–6, default 3 = Thursday), `defect_digest_hour`. Requires a column on
`scheduler_heartbeat` for the guard — recommend `last_defect_digest_week` (string,
`YYYY-Www`) rather than a second date column, so the guard's semantics are visible in
the schema rather than implied by how it is compared.

## 9. Open questions for ratification

**9.1 — Email attachment, not `commit_document`?**
**Recommend email.** She has `commit_document` and could open a PR directly, and it is
tempting. It is wrong here: the document contains *unreviewed generated claims about
her own defects*, going into her own repo, to be read back as build orders. That loop
needs a human in it at exactly one point, and staging is that point. Committing it
would put generated defect claims into the repo before anyone had read them.

**9.2 — Does a zero-defect week still send?**
**Recommend yes**, with a one-line "nothing logged this week." A digest that never
arrives is indistinguishable from a digest pipeline that broke. This is already the
ratified call for the brief — *"a scheduled brief that composes empty is emailed
(visible), never silently dropped"* — and the same reasoning applies unchanged.

**9.3 — Do items retire after one appearance?**
**Recommend no**, per §5. New-vs-carried with an age and occurrence count.

**9.4 — Does the digest get its own health component?**
**Recommend no, and record the deferral with its trigger.** A component per feature is
how a panel becomes something the eye skips, which is the exact failure the
exception-first page exists to prevent. **Trigger: a second scheduled non-brief job
wanting proof-of-life** — at which point the answer is generalising
`scheduler_heartbeat`, not adding a second bespoke one.

**9.5 — Can JARVIS file a defect unprompted, or only when asked?**
This is the largest open question and I do not have a confident recommendation.

*For unprompted:* the whole value is catching what the owner did not notice, and the
first entry in this journal should be the email confirmation latch — which she
experienced eleven times without being able to record once.

*Against:* an autonomous filer is an autonomous fabricator if §1's evidence rule ever
weakens, and the volume is unbounded.

**Recommended middle:** unprompted filing is allowed **only from structural
detectors** — countable facts requiring no judgement — and never from the model's
general sense that something went wrong. Model-authored defects require the owner to
have said something. Start with exactly one detector (§10) and add on evidence.

## 10. The first detector, and it is not a coincidence

**A confirmation raised and re-raised on the same `thread_key` more than twice without
resolution.**

That is a countable fact. It requires no judgement, it cannot be confabulated, and it
is precisely the signal the 2026-08-04 email latch would have tripped on its first
morning — eleven confirmations, none resolved, and `actions_audit` reading green
throughout because refusals and re-confirmations are deliberately in the ok-family.

It is worth naming why that outage was invisible to everything already built: the audit
substrate is **structurally incapable** of showing a gate that never resolves, because
every row it produced was a healthy one. That is the fourth member of the latch family
after the relay body, calendar liveness, and the UI auth latch, and all four share one
shape — **a failure that emits fluent, well-formed, plausible output.**

**That shape is the argument for this feature.** Health checks catch components that
stop. The defect journal is for the failures that keep running and keep looking right.

## 11. Test plan

| Property | Plant |
|---|---|
| A defect with no observation is refused | Make the observation optional |
| `observation` NOT NULL at the column | Attempt a direct insert with NULL; assert it raises |
| The observation is stored verbatim | Add a summarisation pass |
| `area` outside the component inventory is refused | Accept free text |
| `unknown` area is accepted | Reject it |
| Dedup increments rather than inserting | Remove the dedup key |
| A re-observation appends, not replaces | Overwrite on re-observation |
| Zero-defect week still sends | Return early on empty |
| Weekly guard is ISO-week, not date | Compare dates |
| Thursday-down still fires Friday | Require an exact weekday match |
| Carried items show age and occurrences | Suppress the carried section |
| `dismissed` without a reason is refused | Make the reason optional |
| **The `send_email` tool cannot attach** | Add an attachment parameter to the tool schema; assert the test reddens |
| No judgment words in the digest | Reuse the existing `JUDGMENT_WORDS` list, **asserted un-forked** |

The last two are the ones to negative-validate hardest. The attachment boundary is the
security property in this TDD, and the `JUDGMENT_WORDS` reuse must assert the *same*
list object as the timeline and the brief — two lists drift, and the one that drifts is
the one nobody is looking at.

**§2.7 throughout:** inject a value no branch can legitimately produce. In particular
the weekday plant must not use 3, which is the default and coincides with a legitimate
output.
