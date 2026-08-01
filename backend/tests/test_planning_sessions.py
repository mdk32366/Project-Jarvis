"""Planning sessions: the tables, the notes, and THE GATE (TDD #2 §5, §6, §10).

The named test in here is `test_the_07_20_regression_*`: the actual observed
failure that created this TDD. Everything else is scaffolding around it.

Nothing in this build can emit — `emit_tdd` is a later order by design (§9), and
one test asserts its absence so "gate before emission" is a fact rather than an
intention.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app.handlers.base import Context, build_registry
from app.models import PlanningNote, PlanningSession
from app.planning import (ALL_SLOTS, REQUIRED_SLOTS, SLOT_QUESTIONS,
                          compose_slots, session_readiness)

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def ctx(db):
    return Context(db=db, channel="web", actor="admin", thread_key="t1")


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """Classification is stubbed off by default. A test that wants to prove
    classification behaviour patches it explicitly — the rest must not make a
    network call to file a note."""
    monkeypatch.setattr("app.handlers.planning._classify", lambda content: None)


# Substantive filler: over the 120-char floor, and real prose rather than
# repeated characters, so it is a fair stand-in for a filled slot.
def _real(n: int = 160) -> str:
    base = ("The morning brief narrated an error dump aloud because a section guard "
            "keyed off a prefix the raw error string did not carry, and nobody heard "
            "it until it was read out on a call. ")
    return (base * 3)[:n]


_REJECTED_OK = ("Considered a nightly cron instead of a per-tick enqueuer, but it cannot "
                "recover a missed run after a redeploy, and that is the failure mode we "
                "actually hit twice this month, so it lost on exactly the thing we needed.")


def _session(db, slots: dict[str, str] | None = None, *, channel="web") -> PlanningSession:
    s = PlanningSession(topic="A thing worth designing", target="jarvis", status="open")
    db.add(s)
    db.commit()
    db.refresh(s)
    for slot, content in (slots or {}).items():
        db.add(PlanningNote(session_id=s.id, slot=slot, content=content, channel=channel))
    db.commit()
    db.refresh(s)
    return s


def _complete() -> dict[str, str]:
    d = {s: _real() for s in REQUIRED_SLOTS}
    d["rejected"] = _REJECTED_OK
    d["data_model"] = _real()
    return d


# ── 1. THE NAMED TEST — the 07-20 regression (§10) ───────────────────────────
def test_the_07_20_regression_all_placeholders_is_not_ready(db):
    """THE ACTUAL OBSERVED FAILURE THAT CREATED THIS TDD.

    On 2026-07-20 JARVIS was asked for a TDD and produced one with every section
    present and every section a placeholder. This is that artifact, and the gate
    must refuse it — naming every slot, not silently emitting with a warning.
    """
    placeholders = {
        "problem": "TBD",
        "goals": "To be determined",
        "non_goals": "[details to follow]",
        "approach": "TODO",
        "rejected": "N/A",
        "tests": "<fill in>",
        "open_questions": "Coming soon",
        "data_model": "TBD",
    }
    s = _session(db, placeholders)

    r = session_readiness(s.notes)

    assert not r.ready, "the 07-20 artifact must not pass the gate"
    named = {v.slot for v in r.missing}
    assert named == set(ALL_SLOTS), f"gate missed: {set(ALL_SLOTS) - named}"
    # And the refusal is actionable — it carries the question that fills each gap.
    assert all(v.question for v in r.missing)


def test_placeholders_are_caught_by_substance_not_absence(db):
    """A slot that is PRESENT and long-ish but made of filler still fails. The
    gate judges substance; presence is not evidence of anything."""
    s = _session(db, {**_complete(),
                      "problem": "TBD. TODO. To be determined. Details to follow. [more]"})
    r = session_readiness(s.notes)
    assert not r.ready
    assert [v.slot for v in r.missing] == ["problem"]


def test_a_long_wall_of_placeholders_is_still_not_ready(db):
    """THE TEST THAT ACTUALLY EXERCISES PLACEHOLDER DETECTION.

    Found by deliberately breaking the gate: with substance-stripping disabled,
    every other placeholder test still passed, because each fixture was shorter
    than the 120-char floor and the LENGTH check was doing all the work. The
    placeholder logic looked guarded and was not.

    This slot is 200+ characters of pure filler — over the floor, so length
    cannot save it. Only stripping the placeholders and re-measuring catches it,
    which is the 07-20 artifact's real shape: sections long enough to look
    written.
    """
    wall = ("TBD. TODO. To be determined. Details to follow. [details] <fill in> "
            "Coming soon. Placeholder. TBD. TODO. To be decided. [more to come] "
            "Fill this in. None yet. To be defined. <TBD> Details to follow.")
    assert len(wall) > 120, "precondition: this must clear the length floor"

    s = _session(db, {**_complete(), "approach": wall})
    r = session_readiness(s.notes)

    assert not r.ready, "a long wall of placeholders passed the gate"
    assert [v.slot for v in r.missing] == ["approach"]

    # The discriminating assertion: the gate measured far LESS real content than
    # the slot's raw length, which is only possible if the placeholders were
    # stripped before measuring. A gate reading raw length would see 200+ here
    # and wave it through — which is exactly what the planted defect did.
    import re as _re
    measured = int(_re.search(r"only (\d+) characters", r.missing[0].reason).group(1))
    assert measured < 40, f"placeholders were not stripped (measured {measured})"
    assert measured < len(wall) / 4


def test_a_real_slot_mentioning_todo_still_passes(db):
    """The other side of that boundary, and it matters: a genuine paragraph that
    happens to contain the word TODO is not a placeholder. A rule that punished
    it would teach people to avoid the word rather than to write more."""
    s = _session(db, {**_complete(),
                      "problem": _real() + " There is still a TODO on the threshold."})
    assert session_readiness(s.notes).ready


# ── 2. The two unfakeable slots (§5.2) ───────────────────────────────────────
def test_empty_rejected_refuses_and_says_why(db):
    slots = _complete()
    slots.pop("rejected")
    r = session_readiness(_session(db, slots).notes)

    assert not r.ready
    assert [v.slot for v in r.missing] == ["rejected"]
    assert "why it lost" in r.summary() or "reason" in r.summary().lower()


def test_an_alternative_without_a_reason_refuses(db):
    """'Considered Redis' is a list, not a rejection. The reason it lost is the
    part that cannot be produced from a topic name alone."""
    s = _session(db, {**_complete(),
                      "rejected": "We considered Redis. We also considered Postgres. "
                                  "And we looked at SQLite as well as a flat file on disk "
                                  "and a hosted queue service from one of the big clouds."})
    r = session_readiness(s.notes)

    assert not r.ready
    assert [v.slot for v in r.missing] == ["rejected"]
    assert "no reason it lost" in r.missing[0].reason


def test_a_rejection_with_a_reason_passes(db):
    assert session_readiness(_session(db, _complete()).notes).ready


def test_empty_open_questions_refuses_with_the_why(db):
    """§5.2: an empty open-questions is evidence of insufficient thought, not of
    thoroughness — and the refusal has to SAY that, or it reads as a nagging
    required field."""
    slots = _complete()
    slots.pop("open_questions")
    r = session_readiness(_session(db, slots).notes)

    assert not r.ready
    assert [v.slot for v in r.missing] == ["open_questions"]
    assert "insufficient thought" in r.summary()


# ── 3. Length floor + data_model's conditional (§5.1, §5.3) ──────────────────
def test_a_short_slot_refuses(db):
    s = _session(db, {**_complete(), "goals": "Make it work properly."})
    r = session_readiness(s.notes)
    assert not r.ready
    assert [v.slot for v in r.missing] == ["goals"]
    assert "characters of real content" in r.missing[0].reason


def test_the_floor_is_a_setting_not_a_constant(db):
    """§11 admits 120 is arbitrary. It is tunable, and the gate honours the
    argument rather than a module constant."""
    s = _session(db, {**_complete(), "goals": "Short but deliberate."})
    assert not session_readiness(s.notes).ready
    assert session_readiness(s.notes, min_chars=10).ready


def test_data_model_may_be_explicitly_not_applicable(db):
    """'No schema change' is a design statement. Silence is not — so the explicit
    N/A passes and the absence does not."""
    slots = _complete()
    slots["data_model"] = "N/A"
    assert session_readiness(_session(db, slots).notes).ready

    slots.pop("data_model")
    assert not session_readiness(_session(db, slots).notes).ready


def test_a_complete_session_is_ready(db):
    r = session_readiness(_session(db, _complete()).notes)
    assert r.ready
    assert set(r.filled) == set(ALL_SLOTS)
    assert "Ready" in r.summary()


# ── 4. Notes are immutable + cross-channel (§6.2, §4.1) ──────────────────────
def test_reclassifying_a_note_never_rewrites_its_content(db):
    """The raw capture is the EVIDENCE a real conversation happened. A system
    whose evidence can be edited by the thing being judged has no evidence."""
    s = _session(db)
    original = "Considered a cron; it can't recover a missed run, so it lost."
    n = PlanningNote(session_id=s.id, slot="approach", content=original, channel="sms")
    db.add(n)
    db.commit()
    db.refresh(n)

    n.slot = "rejected"          # reclassify — the ONLY legal mutation
    db.commit()
    db.refresh(n)

    assert n.slot == "rejected"
    assert n.content == original, "content must be byte-identical after a reclassify"


def test_notes_accumulate_across_channels(db, ctx, monkeypatch):
    """§4.1: SMS at the dock, voice on the drive, the keyboard later — one
    session. Nothing depends on a continuous conversation."""
    from app.handlers.planning import _add_planning_note, _start_planning

    _start_planning({"topic": "cross-channel"}, ctx)
    for channel, text_ in (("sms", "one"), ("voice", "two"), ("web", "three")):
        _add_planning_note({"content": text_, "slot": "problem"},
                           Context(db=db, channel=channel, actor="a", thread_key="t"))

    s = db.query(PlanningSession).filter_by(status="open").one()
    assert {n.channel for n in s.notes} == {"sms", "voice", "web"}
    assert compose_slots(s.notes)["problem"] == "one\n\ntwo\n\nthree"


def test_an_unclassified_note_is_visible_not_silently_dropped(db, ctx):
    """Classification fails to None, never to a guess — and the unplaced note has
    to SHOW, because a confidently wrong slot quietly pads someone else's
    substance and helps a thin session through the gate."""
    from app.handlers.planning import _add_planning_note, _planning_status, _start_planning

    _start_planning({"topic": "t"}, ctx)
    _add_planning_note({"content": "mumble mumble"}, ctx)

    assert session_readiness(
        db.query(PlanningSession).one().notes).unclassified == 1
    assert "unclassified" in _planning_status({}, ctx)


# ── 5. One open session (§4.1) ───────────────────────────────────────────────
def test_a_second_session_is_refused_while_one_is_open(db, ctx):
    from app.handlers.planning import _start_planning

    _start_planning({"topic": "first"}, ctx)
    msg = _start_planning({"topic": "second"}, ctx)

    assert "already an open planning session" in msg
    assert db.query(PlanningSession).count() == 1
    assert "unambiguous home" in msg, "the refusal should say WHY one at a time"


def test_abandoning_requires_a_reason_and_keeps_the_notes(db, ctx):
    from app.handlers.planning import _abandon_planning, _add_planning_note, _start_planning

    _start_planning({"topic": "t"}, ctx)
    _add_planning_note({"content": "a real thought", "slot": "problem"}, ctx)

    assert "Why are we abandoning" in _abandon_planning({}, ctx)
    assert db.query(PlanningSession).one().status == "open"

    _abandon_planning({"reason": "superseded by the other design"}, ctx)
    s = db.query(PlanningSession).one()
    assert s.status == "abandoned"
    assert any(n.content == "a real thought" for n in s.notes), "notes are kept"


# ── 6. GATE BEFORE EMISSION (§9) — asserted, not intended ────────────────────
def test_nothing_in_this_build_can_emit(db):
    """§9's ordering as a fact rather than a promise. Emission is a later order;
    a system that emits with a gate bolted on afterwards is how gates end up
    bypassable, and the only way to know the gate came first is to check that
    nothing can emit yet."""
    reg = build_registry(include_delegate=False)
    assert not reg.has("emit_tdd")

    # Checked against what the module DOES, not what it mentions — the docstring
    # names emit_tdd precisely to record that it is deferred, and a test that
    # forbade the word would push that reasoning out of the code.
    src = (BACKEND / "app" / "handlers" / "planning.py").read_text(encoding="utf-8")
    assert '"name": "emit_tdd"' not in src, "an emit tool is registered"
    assert "def _emit" not in src, "an emit handler exists"
    assert "commit_document(" not in src, "an emit path is wired to TDD #3"
    assert "from app.handlers.repos" not in src, "the writer is imported"


def test_the_tools_are_registered_and_ungated():
    reg = build_registry(include_delegate=False)
    for name in ("start_planning", "add_planning_note", "planning_status",
                 "next_planning_question", "abandon_planning"):
        assert reg.has(name), f"{name} not registered"
        assert not reg.is_gated(name), "capturing a thought is reversible bookkeeping"


def test_the_secretary_roster_stays_a_voice_subset():
    """The gotcha that bites every time a tool joins a voice-reachable agent: the
    roster must be a subset of VOICE_TOOLS_PHASE1 or the whole agent silently
    drops off voice."""
    from app.agents import DEFAULT_AGENTS
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1

    extra = set(DEFAULT_AGENTS["secretary"].tools) - VOICE_TOOLS_PHASE1
    assert not extra, f"secretary needs {extra} added to VOICE_TOOLS_PHASE1"


def test_every_required_slot_has_a_question():
    """A refusal that can't say what would fix it is a dead end."""
    for slot in ALL_SLOTS:
        assert SLOT_QUESTIONS.get(slot), f"no question for {slot}"


# ── 7. Migration ─────────────────────────────────────────────────────────────
def _alembic(db_path: Path, *args: str):
    env = {**os.environ, "DATABASE_URL": f"sqlite+pysqlite:///{db_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          cwd=BACKEND, env=env, capture_output=True, text=True, timeout=300)


def test_planning_migration_roundtrips(tmp_path):
    db_file = tmp_path / "m.db"
    # This revision, not `head` — see the note in test_github_writes.py. Pinning
    # the global head makes a per-migration test fail on the NEXT migration.
    up = _alembic(db_file, "upgrade", "0027_planning_sessions")
    assert up.returncode == 0, f"{up.stdout}\n{up.stderr}"

    engine = create_engine(f"sqlite+pysqlite:///{db_file}")
    try:
        names = inspect(engine).get_table_names()
        assert "planning_session" in names and "planning_note" in names
        with engine.connect() as c:
            v = c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert v == "0027_planning_sessions"
    finally:
        engine.dispose()

    down = _alembic(db_file, "downgrade", "-1")
    assert down.returncode == 0, f"{down.stdout}\n{down.stderr}"

    engine = create_engine(f"sqlite+pysqlite:///{db_file}")
    try:
        names = inspect(engine).get_table_names()
        assert "planning_session" not in names and "planning_note" not in names
    finally:
        engine.dispose()


def test_migration_chains_off_the_confirmed_head():
    """Confirmed against `alembic heads` at build time, not taken from the draft
    — which said 0023, four slots stale."""
    mig = (BACKEND / "alembic" / "versions" / "0027_planning_sessions.py").read_text(
        encoding="utf-8")
    assert 'revision = "0027_planning_sessions"' in mig
    assert 'down_revision = "0026_github_write_log"' in mig
