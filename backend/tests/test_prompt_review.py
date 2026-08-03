"""The prompt-review guards — three, and the first one is the one that works.

`seed_agents` reconciles rosters and never overwrites `system_prompt`, so every
new tool ships and none of the prose does. Nothing goes red. These make it go
red. See `app/prompt_review.py` for why the ledger is a code file, and for what
these guards deliberately cannot see (production).
"""

import pytest

from app.agents import DEFAULT_AGENTS
from app.prompt_review import GUIDED, LEDGER, SELF_DESCRIBING, guided_tools


def test_every_rostered_tool_has_been_reviewed():
    """THE GUARD THAT DOES THE WORK. Adding a tool to a roster now fails CI until
    someone has consciously decided whether it needs prose — and the act of
    adding the ledger entry IS that acknowledgement.

    This is deliberately structural rather than a list of known gaps: the defect
    is silent GROWTH, and a guard that only knows today's gaps cannot catch
    tomorrow's, which is the whole problem.
    """
    problems = []
    for agent, spec in sorted(DEFAULT_AGENTS.items()):
        unreviewed = sorted(set(spec.tools) - set(LEDGER.get(agent, {})))
        if unreviewed:
            problems.append(
                f"{agent}: {len(unreviewed)} tool(s) added since last prompt review: "
                f"{unreviewed}\n"
                f"  Review each against docs/design-note-prompt-drift.md §5, update the "
                f"prompt if it needs a line, then record the disposition in "
                f"app/prompt_review.py."
            )
    assert not problems, "\n".join(problems)


def test_every_guided_tool_is_named_in_that_agents_prompt():
    """The secretary guard, generalised to all nine — with the list now produced
    by the review process instead of maintained beside it.

    A tool marked `guided` is one whose schema leaves a real decision unmade. If
    the prompt does not mention it, the agent ships it reachable-but-unoffered,
    which is the defect in miniature.
    """
    problems = []
    for agent, spec in sorted(DEFAULT_AGENTS.items()):
        missing = [t for t in guided_tools(agent) if t not in spec.system]
        if missing:
            problems.append(f"{agent}: prompt has no guidance for {missing}")
    assert not problems, "\n".join(problems)


def test_the_ledger_names_no_tool_the_agent_lacks():
    """Reverse join — a ledger entry for a removed tool is a stale review record
    asserting something untrue.

    NOTE THE ASYMMETRY, which is deliberate: roster GROWTH fails, roster
    SHRINKAGE does not. Removing a tool leaves a harmless extra sentence in a
    prompt; adding one leaves a capability nobody is told about. Only one of
    those is a defect and the guard should not pretend otherwise.
    """
    problems = []
    for agent, entries in sorted(LEDGER.items()):
        spec = DEFAULT_AGENTS.get(agent)
        if spec is None:
            problems.append(f"ledger names unknown agent {agent!r}")
            continue
        orphans = sorted(set(entries) - set(spec.tools))
        if orphans:
            problems.append(f"{agent}: ledger reviews tools it does not have: {orphans}")
    assert not problems, "\n".join(problems)


def test_every_disposition_is_one_of_the_two():
    for agent, entries in LEDGER.items():
        for tool, (disp, _) in entries.items():
            assert disp in (GUIDED, SELF_DESCRIBING), f"{agent}/{tool}: {disp!r}"


def test_every_guided_entry_records_a_reason():
    """The reason is what turns the file into a record of JUDGMENT rather than a
    list of strings — a later reader can disagree with the call instead of merely
    observing it."""
    thin = [f"{a}/{t}" for a, e in LEDGER.items()
            for t, (d, r) in e.items() if d == GUIDED and len(r.strip()) < 15]
    assert not thin, f"guided entries with no real reason: {thin}"


def test_the_assessment_was_not_a_rubber_stamp():
    """A ledger that starts as a rubber stamp teaches the next reader that it is
    one. If nothing was marked guided, the assessment was skipped rather than
    passed."""
    total = sum(len(e) for e in LEDGER.values())
    guided = sum(1 for e in LEDGER.values() for d, _ in e.values() if d == GUIDED)
    assert guided > 0, "nothing marked guided — the assessment was skipped"
    assert guided < total, "everything marked guided — the distinction was not drawn"


def test_the_standalone_judgment_list_is_retired():
    """Two lists of the same thing is the dead-runbook defect, and this repo has
    paid for that lesson twice. `_JUDGMENT_TOOLS` lived in
    test_agents_expansion.py; its entries now live in the ledger."""
    from pathlib import Path
    src = (Path(__file__).parent / "test_agents_expansion.py").read_text(encoding="utf-8")
    assert "_JUDGMENT_TOOLS" not in src, \
        "the standalone judgment list still exists alongside the ledger"
