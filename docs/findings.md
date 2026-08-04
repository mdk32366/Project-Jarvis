# FINDINGS

Things learned by running the system that reading it would not have shown.

KEEL Principle 8's rule needs a destination, and this is it — a finding written
into a close-out is findable only by someone who already knows which day it
happened on. One entry per finding, newest first, each naming what was believed,
what was measured, and what changes as a result.

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
