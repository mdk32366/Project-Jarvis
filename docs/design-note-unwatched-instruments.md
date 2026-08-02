# Design note — Unwatched instruments: guards that cannot fail the way their name implies

**Status:** Named pattern + a working discipline. Written 2026-08-02 after four
instances in one day, three of which were caught only because a deliberate
defect was planted and the guard *stayed green*.

**Prompted by:** the project-management arc (PRs #53–#64), where "break the
instrument once to confirm it fires" stopped being a ritual and started
returning findings.

**Sibling note:** `design-note-latch-failures.md`. That one is about a *system*
reporting a confident wrong answer. This one is about a *test* doing it.

---

## Why this note exists

The standing discipline in this codebase is **break every guard once to confirm
it fires**. It arrived as a reaction to `test_missing_runbook_degrades_gracefully`,
which asserted a bug as a guarantee and would have defended it against every
future fix.

On 2026-08-02 that discipline paid out four times in one day — and three of those
payouts came in a form nobody had written down: **the plant produced no failure.**

The reflex when that happens is to assume the plant didn't apply. Twice it had
applied perfectly, and the green was the finding.

---

## 1. The class

> **An unwatched instrument is a guard that appears to protect something but
> cannot fail in the way its name implies.**

It differs from a merely absent guard in the way that matters: an absent guard is
visibly absent. An unwatched one is *read as coverage*. It appears in the file,
its name describes the property you care about, it is green, and it is inert.

### The tell

**A planted defect that produces no failure.** That is not a null result. It has
exactly two readings, and both are findings:

| The plant didn't fire because… | What you've found |
|---|---|
| the test doesn't reach that path | a **coverage gap** — the suite covers fewer cases than its names claim |
| the code element can't change an answer | a **dead element** — it reads as protection and isn't |

There is a third possibility — the plant never applied — and it is the one to
eliminate *first*, mechanically, before interpreting anything (§4).

---

## 2. Confirmed instances (all 2026-08-02 unless noted)

### 2.1 Coverage gap — emit atomicity (#63)

`emit_project_plan` seeds draft rows, commits a document, and promotes the rows
only on success. I planted *"promote the rows without checking the document
landed"* and ran `test_a_failed_commit_leaves_no_half_landed_state`.

**It passed.**

`commit_document` can fail two ways: it can **raise** (a GitHub fault) or
**refuse by returning a string** (no token, scanner hit). My test forced a 500,
which raises — handled by the `try/except`. The plant lived in the *other* path,
guarded only by judging success on state. The suite covered **one of two failure
modes while reading as complete**, on the single most important invariant in the
change.

Wrote the second test, re-planted, watched it fail.

### 2.2 Dead element — the scanner's base64 padding (#64)

Fixing a false positive, I removed `=` from the entropy tokenizer's character
class but kept it as trailing padding: `{32,}={0,2}`. I wrote a test claiming
padding could carry a short body over the 32-character floor, then planted the
element's removal.

**The test stayed green.**

Strengthening the fixture to a 30-character body made it fail *on the fixed code
too* — because `{32,}` applies to the **body**, so padding can only ever extend a
match that already qualified. The element could not change a verdict, only a
span. I had written a regex element that did nothing **and a test defending it**.

Dropped both. A pattern element that cannot change an answer is worse than
absent: it invites exactly the false confidence that produced the test.

### 2.3 Load-bearing-by-accident — the completeness gate's placeholder detection (#58)

The gate strips placeholder tokens (`TBD`, `[details]`) and measures what
remains. I disabled the stripping, expecting every placeholder test to fail.

**They all passed.**

Every fixture was shorter than the 120-character floor, so the **length check was
doing all the work** and the placeholder logic was never exercised. The tests
were named for placeholder detection and tested length.

Fixed with a 200-character wall of pure filler — over the floor, so only
stripping catches it — asserting the *measured* substance is a quarter of the raw
length, which is the only observable proof stripping ran.

### 2.4 The instrument's own instrument — a silent no-op patch (#59)

Earlier the same day, a patch script's `str.replace` didn't match (an em-dash and
an escaped newline), so **no defect was planted at all** — and the tests passed.
The script printed `"defect planted"` unconditionally afterwards.

Read at face value, that is a validated guard. It was nothing at all.

### 2.5 Prior art — a bug with a passing test (2026-07-31)

`test_missing_runbook_degrades_gracefully` asserted `remediation is None` for a
failing component and called it *graceful* — a docstring dressing a bug as
design. Every future fix would have tripped it and been reverted "because it
broke a test."

The origin of the discipline, and the reason this note is about tests rather
than about systems.

---

## 3. What this class is NOT

Worth separating, because merging them blunts both checklists.

| | Unwatched instrument | Latch (`design-note-latch-failures.md`) |
|---|---|---|
| What's wrong | the **guard** can't detect the thing | the **system** can't clear a resolved fault |
| Surface | a green test | a confident wrong status |
| Found by | planting a defect | a live probe |
| Fix | make the guard able to fail | restore the clearing path |

They share a consequence — *confident, stable, wrong* — which is why they feel
alike. The questions differ, so the notes differ.

---

## 4. The discipline, in order

1. **Make the plant verify itself before you interpret anything.** Assert the
   file actually changed:

   ```python
   assert t != original, "PATCH DID NOT APPLY"
   ```

   §2.4 is why. A `print("planted")` after an unverified `str.replace` is an
   uninstrumented instrument checking an instrument.

2. **Plant the defect the guard is named for** — not a nearby one. "Scan too
   late" for a scan-precedes-write guard; "widen the base slot set" for a
   don't-regress-what-you-extend guard. A plant the guard was never meant to
   catch teaches nothing either way.

3. **Watch it fail, then restore, then watch it pass.** Both directions. A guard
   only seen passing has been seen doing nothing.

4. **If it stays green, do not move on.** Work the table in §1. It is a finding
   every time.

5. **Prefer plants that are the tempting shortcut.** The best ones are things a
   future contributor would plausibly *do*: set the baseline when the date is
   first proposed (simpler); use one slot set for every session type (tidier);
   promote the rows and check afterwards (fewer branches). Those are the changes
   that will actually arrive, wearing a refactor's clothes.

---

## 5. What makes a guard hard to leave unwatched

Observed across the arc; offered as construction advice, not law.

- **Assert the mechanism, not the outcome**, when the outcome has more than one
  cause. §2.3's fix asserts *measured substance is a quarter of raw length* —
  a fact only stripping can produce — rather than "not ready", which the length
  floor also produces.
- **Assert absences structurally.** "Never merges" is checked both as *no
  `/merge` URL called* and *no `/merge` string in the module*. "Only ratify
  writes a baseline" parses the module and asserts exactly two writers. A
  behavioural test alone would not stop a third being added.
- **Enumerate the thing you're forbidding.** `JUDGMENT_WORDS` is a list, so
  breaking the fact-not-judgment rule requires consciously deleting entries
  rather than drifting into a phrase — and the list is asserted **un-forked**,
  because two lists drift and the one that drifts is the one nobody watches.
- **Prefer a data shape that cannot express the failure.** `SecretFinding` has
  no value-bearing field at all, so the non-echo invariant holds by the shape of
  the data rather than by every call site remembering to be careful. That is the
  strongest form available and it needs no guard at all.

---

## 6. The one-line version

**A plant that produces no failure is telling you something.** Eliminate "the
patch didn't apply" first; after that, green means either your test doesn't reach
the path or your code doesn't do anything — and you have just learned which.
