"""Episodic memory (TDD #14) — the §8 test table, one test per row.

The distiller turns a closed conversation into a dated, embedded Episode with
VERBATIM quotes for its load-bearing claims. The one unacceptable failure is a
fabricated quote (test 6); the one structural trap is quietly mirroring voice
turns into `messages` (test 16). Both are enforced here, not by convention.
"""

import json
from datetime import date, datetime, timezone

import pytest
from fakes import ScriptedLLM, install_llm, response, say, text_block

from app.config import settings
from app.handlers.base import Context, build_registry
from app.models import Conversation, Episode, EpisodeQuote, Job, Memory, Message, VoiceTurn


# ── Helpers ──────────────────────────────────────────────────────────────────
def _voice_call(db, sid="CA_EP", turns=None):
    """Persist a finished call's turns — the voice cold store, as-is."""
    turns = turns or []
    for i, (user_text, reply) in enumerate(turns):
        db.add(VoiceTurn(call_sid=sid, turn=i, status="done",
                         user_text=user_text, reply=reply))
    db.commit()
    return sid


TRANSLATOR_TURNS = [
    ("Remember that wearable translator idea? I want to talk it through.",
     "The wearable language-translation device — go ahead."),
    ("I think the market's ready. Let's build the wearable translator prototype this fall.",
     "Noted. A fall prototype for the wearable translator."),
    ("Yes. And I'll budget five thousand dollars for parts.",
     "Five thousand for parts, understood."),
]


def _distill_json(**over) -> str:
    """A well-formed distiller response; override fields per test."""
    payload = {
        "title": "Wearable translator prototype decision",
        "summary": "Discussed the wearable language-translation device; decided "
                   "to build a prototype this fall with a parts budget.",
        "topics": ["hardware", "translation", "product-idea"],
        "action_items": ["Build wearable translator prototype this fall"],
        "salience": 0.9,
        "quotes": [
            {"speaker": "owner", "kind": "decision",
             "quote": "Let's build the wearable translator prototype this fall."},
        ],
    }
    payload.update(over)
    return json.dumps(payload)


def _distill(db, sid="CA_EP") -> str:
    from app.episodic import distill_episode

    return distill_episode(db, channel="voice", thread_key=sid, source_ref=sid)


def _ctx(db, channel="web"):
    return Context(db=db, channel=channel, actor="tester", thread_key="t")


# ── 1. model / migration ─────────────────────────────────────────────────────
def test_episode_tables_exist_and_quotes_cascade(db):
    """Deleting an episode must take its quotes with it — an orphaned 'verbatim
    quote' with no episode context is exactly the kind of debris that gets
    misread later."""
    ep = Episode(channel="voice", thread_key="CA_X", occurred_on=date(2026, 7, 16),
                 occurred_at=datetime(2026, 7, 16, 11, tzinfo=timezone.utc),
                 title="t", summary="s")
    ep.quotes.append(EpisodeQuote(speaker="owner", quote="q1", kind="decision"))
    ep.quotes.append(EpisodeQuote(speaker="jarvis", quote="q2", kind="commitment"))
    db.add(ep)
    db.commit()

    assert db.query(EpisodeQuote).count() == 2
    db.delete(ep)
    db.commit()
    assert db.query(EpisodeQuote).count() == 0


def test_migration_chain_includes_episodes():
    """The migration exists and chains off 0011 — never create_all-only."""
    import pathlib

    mig = pathlib.Path(__file__).parent.parent / "alembic" / "versions" / "0012_episodes.py"
    text = mig.read_text(encoding="utf-8")
    assert 'revision = "0012_episodes"' in text
    assert 'down_revision = "0011_google_documents"' in text
    assert "episode_quotes" in text and "ondelete=\"CASCADE\"" in text.replace("'", '"')


# ── 2. trigger: call end enqueues, never distills inline ─────────────────────
def test_voice_call_end_enqueues_distill_job(client, db, monkeypatch):
    """Distillation is a JOB (it makes an LLM call, which must not block a
    hangup). The status webhook only enqueues."""
    install_llm(monkeypatch, say("SHOULD NOT BE CALLED INLINE"))
    sid = _voice_call(db, "CA_TRIGGER", TRANSLATOR_TURNS)

    r = client.post("/api/voice/status",
                    data={"From": "+15551230000", "CallSid": sid,
                          "CallStatus": "completed"})
    assert r.status_code == 200

    jobs = db.query(Job).filter(Job.kind == "distill_episode").all()
    assert len(jobs) == 1, "call end must enqueue exactly one distill job"
    assert json.loads(jobs[0].payload)["thread_key"] == sid
    assert db.query(Episode).count() == 0, "distillation ran inline — it must not"


def test_call_end_does_not_enqueue_twice(client, db, monkeypatch):
    """Belt-and-braces like email_transcript: a duplicate status callback must
    not spawn a second distill job for the same call."""
    install_llm(monkeypatch, say("unused"))
    sid = _voice_call(db, "CA_DUP", TRANSLATOR_TURNS)

    for _ in range(2):
        client.post("/api/voice/status",
                    data={"From": "+15551230000", "CallSid": sid,
                          "CallStatus": "completed"})

    assert db.query(Job).filter(Job.kind == "distill_episode").count() == 1


# ── 3. spam guard ────────────────────────────────────────────────────────────
def test_short_call_is_not_an_episode(db, monkeypatch):
    """A one-line 'you have mail' that got hung up on is not an episode."""
    install_llm(monkeypatch, say("SHOULD NOT BE CALLED AT ALL"))
    sid = _voice_call(db, "CA_SHORT", [("ok thanks bye", "Goodbye.")])

    result = _distill(db, sid)

    assert db.query(Episode).count() == 0
    assert "skip" in result.lower()


# ── 4. distillation produces a full episode ──────────────────────────────────
def test_multi_turn_call_produces_episode(db, monkeypatch):
    install_llm(monkeypatch, ScriptedLLM(response([text_block(_distill_json())])))
    sid = _voice_call(db, "CA_FULL", TRANSLATOR_TURNS)

    result = _distill(db, sid)

    ep = db.query(Episode).one()
    assert "episode" in result.lower()
    assert ep.channel == "voice" and ep.source_ref == sid
    assert ep.title and ep.summary
    assert "translation" in ep.topics
    assert ep.embedding, "episode must be embedded for semantic recall"
    assert ep.occurred_on is not None and ep.occurred_at is not None
    assert 0.0 <= ep.salience <= 1.0


def test_distilling_twice_does_not_duplicate(db, monkeypatch):
    """Job retries happen (that's the queue's whole design). A retry after a
    partial failure must not mint a second episode of the same call."""
    install_llm(monkeypatch, ScriptedLLM(response([text_block(_distill_json())])))
    sid = _voice_call(db, "CA_RETRY", TRANSLATOR_TURNS)

    _distill(db, sid)
    result = _distill(db, sid)

    assert db.query(Episode).count() == 1
    assert "already" in result.lower()


# ── 5. verbatim quotes ───────────────────────────────────────────────────────
def test_decision_quote_stored_verbatim_with_provenance(db, monkeypatch):
    install_llm(monkeypatch, ScriptedLLM(response([text_block(_distill_json())])))
    sid = _voice_call(db, "CA_Q", TRANSLATOR_TURNS)

    _distill(db, sid)

    q = db.query(EpisodeQuote).one()
    assert q.quote == "Let's build the wearable translator prototype this fall."
    assert q.kind == "decision" and q.speaker == "owner"
    assert q.turn_ref, "a quote must point back at the raw turn it came from"


# ── 6. QUOTE VALIDATION — the load-bearing test ──────────────────────────────
def test_fabricated_quote_is_dropped_never_stored(db, monkeypatch, caplog):
    """The one unacceptable failure: a 'quote' the owner never said, laundered
    into 'your exact words.' Anything not a verbatim substring of a raw turn
    (speaker-matched) is DROPPED and logged — the episode survives, the lie
    does not."""
    quotes = [
        {"speaker": "owner", "kind": "decision",
         "quote": "Let's build the wearable translator prototype this fall."},
        {"speaker": "owner", "kind": "commitment",
         "quote": "I will wire you ten million dollars tomorrow."},   # fabricated
        {"speaker": "owner", "kind": "decision",
         "quote": "Yes. And I'll budget five thousand dollars for parts."},  # real
        {"speaker": "jarvis", "kind": "commitment",
         "quote": "Let's build the wearable translator prototype this fall."},  # wrong speaker
    ]
    install_llm(monkeypatch,
                ScriptedLLM(response([text_block(_distill_json(quotes=quotes))])))
    sid = _voice_call(db, "CA_FAB", TRANSLATOR_TURNS)

    with caplog.at_level("WARNING"):
        _distill(db, sid)

    stored = [q.quote for q in db.query(EpisodeQuote).all()]
    assert "I will wire you ten million dollars tomorrow." not in stored
    assert len(stored) == 2, "only the two speaker-matched verbatim quotes survive"
    assert any("quote" in r.message.lower() for r in caplog.records), \
        "dropped quotes must be logged loudly"


# ── 7. embedding / search fallback ───────────────────────────────────────────
def test_episode_search_works_on_cosine_fallback(db, monkeypatch):
    """Dev/tests run SQLite + local embeddings — search must work there too,
    same portable pattern as the Memory store."""
    from app.episodic import search_episodes

    install_llm(monkeypatch, ScriptedLLM(response([text_block(_distill_json())])))
    sid = _voice_call(db, "CA_EMB", TRANSLATOR_TURNS)
    _distill(db, sid)

    hits = search_episodes(db, "wearable translator prototype")
    assert hits, "semantic search over episodes returned nothing"
    ep, sim = hits[0]
    assert ep.source_ref == sid
    assert isinstance(sim, float)


# ── 8/9. recall: semantic + temporal ─────────────────────────────────────────
def _old_episode(db, *, title, summary, topics, on, salience=0.8, thread="CA_OLD"):
    from app.embeddings import embed

    ep = Episode(channel="voice", thread_key=thread, source_ref=thread,
                 occurred_on=on, occurred_at=datetime(on.year, on.month, on.day,
                                                      12, tzinfo=timezone.utc),
                 title=title, summary=summary, topics=json.dumps(topics),
                 salience=salience,
                 embedding=json.dumps(embed(f"{title} {summary} {' '.join(topics)}")))
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return ep


def test_recall_episodes_finds_it_years_later(db):
    """THE target capability: 'remember when we talked about that wearable
    language-translation device a couple of years ago?' must return a real,
    dated answer."""
    _old_episode(db, title="Wearable translator idea",
                 summary="Discussed a wearable language-translation device worn "
                         "as a pendant; owner wanted to revisit after the boat season.",
                 topics=["hardware", "translation", "product-idea"],
                 on=date(2024, 7, 1))
    _old_episode(db, title="Fly billing check",
                 summary="Reviewed hosting spend and credit balance.",
                 topics=["infra"], on=date(2026, 7, 1), thread="CA_BILL")

    reg = build_registry()
    out = reg.execute("recall_episodes", {"query": "wearable translator"}, _ctx(db))

    assert "Wearable translator idea" in out
    assert "2024-07-01" in out, "the DATE is the point of episodic memory"


def test_recall_episodes_since_until_filters(db):
    _old_episode(db, title="Translator chat, early",
                 summary="wearable translator first mention",
                 topics=["translation"], on=date(2024, 7, 1), thread="CA_A")
    _old_episode(db, title="Translator chat, late",
                 summary="wearable translator revisited",
                 topics=["translation"], on=date(2026, 6, 1), thread="CA_B")

    reg = build_registry()
    out = reg.execute("recall_episodes",
                      {"query": "wearable translator", "since": "2026-01-01"},
                      _ctx(db))
    assert "Translator chat, late" in out and "Translator chat, early" not in out

    out = reg.execute("recall_episodes",
                      {"query": "wearable translator", "until": "2025-01-01"},
                      _ctx(db))
    assert "Translator chat, early" in out and "Translator chat, late" not in out


# ── 10/11. unified recall + precedence ───────────────────────────────────────
def test_unified_recall_ranks_quotes_above_inferred_facts(db):
    """Precedence: a verbatim quote outranks an episode summary outranks an
    inferred fact. The output must carry that ordering, because the model
    reading it has no other way to know which source to trust."""
    from app import vectorstore

    ep = _old_episode(db, title="Wearable translator decision",
                      summary="Decided to prototype the wearable translator.",
                      topics=["translation"], on=date(2025, 3, 1))
    db.add(EpisodeQuote(episode_id=ep.id, speaker="owner", kind="decision",
                        quote="Let's build the wearable translator prototype this fall."))
    db.commit()
    vectorstore.add(db, Memory(content="Owner is interested in translation gadgets",
                               category="projects", source="reflector"))

    reg = build_registry()
    out = reg.execute("recall", {"query": "wearable translator"}, _ctx(db))

    assert "Let's build the wearable translator prototype" in out
    assert "translation gadgets" in out
    quote_pos = out.index("Let's build the wearable translator prototype")
    fact_pos = out.index("translation gadgets")
    assert quote_pos < fact_pos, "quote-anchored recall must rank above inferred facts"


def test_tier1_ground_truth_outranks_episodes(db, monkeypatch):
    """An episode that contradicts configured ground truth must not present
    itself as authoritative — same preamble rule Tier-2 facts already obey."""
    monkeypatch.setattr(settings, "owner_home_airport", "SEA")
    ep = _old_episode(db, title="Airport mixup",
                      summary="Owner mentioned flying out of SFO for the Napa trip.",
                      topics=["travel"], on=date(2026, 5, 1))
    db.add(EpisodeQuote(episode_id=ep.id, speaker="owner", kind="key_fact",
                        quote="flying out of SFO"))
    db.commit()

    reg = build_registry()
    out = reg.execute("recall", {"query": "airport SFO travel"}, _ctx(db))

    assert "recollection" in out.lower() or "configured" in out.lower(), \
        "recall output must remind that configured ground truth outranks it"


# ── 12/13. routing + voice reachability ──────────────────────────────────────
def test_archivist_advertises_past_conversation_recall():
    """CANARY (the Docs bug, again): a capability the description doesn't
    advertise is invisible — the orchestrator never routes to it."""
    from app.agents import DEFAULT_AGENTS

    arch = DEFAULT_AGENTS["archivist"]
    assert "PAST CONVERSATIONS" in arch.description
    for tool in ("recall_episodes", "recall", "forget_episode"):
        assert tool in arch.tools, f"archivist roster missing {tool}"


def test_recall_tools_are_voice_allowlisted():
    """Reachable on a call — and the archivist's roster must remain a subset of
    the voice allowlist, or the subset check silently kills the whole agent
    over voice."""
    from app.agents import DEFAULT_AGENTS
    from app.channels.voice_pipeline import VOICE_TOOLS_PHASE1

    assert "recall_episodes" in VOICE_TOOLS_PHASE1
    assert set(DEFAULT_AGENTS["archivist"].tools).issubset(VOICE_TOOLS_PHASE1)


# ── 14. forgetting ───────────────────────────────────────────────────────────
def test_forget_episode_removes_it_from_recall(db):
    """Memory you can't correct is worse than none."""
    ep = _old_episode(db, title="Wearable translator idea",
                      summary="wearable translator discussion",
                      topics=["translation"], on=date(2024, 7, 1))

    reg = build_registry()
    out = reg.execute("forget_episode", {"episode_id": ep.id}, _ctx(db))
    assert "forgotten" in out.lower()

    out = reg.execute("recall_episodes", {"query": "wearable translator"}, _ctx(db))
    assert "Wearable translator idea" not in out
    assert db.query(Episode).count() == 0


# ── 15. cross-channel shape ──────────────────────────────────────────────────
def test_close_episode_works_for_text_channels_too(db, monkeypatch):
    """Design check: the pipeline is channel-agnostic. An SMS thread distills
    through the SAME close_episode/job/Episode shape — no migration needed
    when text channels turn this on."""
    from app.episodic import close_episode, distill_episode

    convo = Conversation(channel="sms", thread_key="+15551230000")
    db.add(convo)
    db.commit()
    texts = [("user", "Let's plan the Napa trip in August."),
             ("assistant", "August Napa trip — noted."),
             ("user", "Book the tasting room for the 6th."),
             ("assistant", "Tasting room on the 6th, will do.")]
    for role, content in texts:
        db.add(Message(conversation_id=convo.id, role=role, content=content))
    db.commit()

    install_llm(monkeypatch, ScriptedLLM(response([text_block(_distill_json(
        title="Napa trip planning", topics=["travel"],
        quotes=[{"speaker": "owner", "kind": "decision",
                 "quote": "Book the tasting room for the 6th."}]))])))

    job = close_episode(db, channel="sms", thread_key="+15551230000",
                        source_ref=f"conversation:{convo.id}")
    assert job is not None and job.kind == "distill_episode"

    distill_episode(db, channel="sms", thread_key="+15551230000",
                    source_ref=f"conversation:{convo.id}")

    ep = db.query(Episode).one()
    assert ep.channel == "sms" and ep.title == "Napa trip planning"
    assert db.query(EpisodeQuote).one().quote == "Book the tasting room for the 6th."


# ── 16. NO DOUBLE-WRITE — the rejected design must stay rejected ─────────────
def test_distillation_never_writes_turns_into_messages(db, monkeypatch):
    """The design fork (TDD §3): raw stores stay pristine, episodes layer on
    top. If a future 'helpful' change starts mirroring voice turns into
    `messages`, quotes would validate against reformatted copies and the
    faithfulness guarantee quietly rots. This test is the tripwire."""
    install_llm(monkeypatch, ScriptedLLM(response([text_block(_distill_json())])))
    sid = _voice_call(db, "CA_PURE", TRANSLATOR_TURNS)
    turns_before = db.query(VoiceTurn).count()
    messages_before = db.query(Message).count()

    _distill(db, sid)

    assert db.query(Episode).count() == 1, "episode layer populated"
    assert db.query(Message).count() == messages_before, \
        "distillation wrote into messages — the rejected mirror design"
    assert db.query(VoiceTurn).count() == turns_before, \
        "distillation must never mutate the raw voice cold store"
