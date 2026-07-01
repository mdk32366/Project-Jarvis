from app import reflector
from app.models import Conversation, Memory, Message
from fakes import ScriptedLLM, install_llm, response, text_block


def _seed_convo(db):
    c = Conversation(channel="sms", thread_key="+1555")
    db.add(c); db.commit(); db.refresh(c)
    db.add(Message(conversation_id=c.id, role="user", content="My accountant is Jane Doe and I hate 8am meetings."))
    db.add(Message(conversation_id=c.id, role="assistant", content="Noted."))
    db.commit()
    return c


def test_parse_facts_handles_fenced_json():
    raw = '```json\n[{"content":"X","category":"people"}]\n```'
    assert reflector._parse_facts(raw) == [{"content": "X", "category": "people"}]
    assert reflector._parse_facts("no json here") == []


def test_reflect_stores_facts(db, monkeypatch):
    c = _seed_convo(db)
    facts_json = '[{"content":"Matt\'s accountant is Jane Doe","category":"people","sensitive":false},' \
                 '{"content":"Matt dislikes 8am meetings","category":"preferences","sensitive":false}]'
    install_llm(monkeypatch, ScriptedLLM(response([text_block(facts_json)])))
    n = reflector.reflect_conversation(db, c.id)
    assert n == 2
    contents = {m.content for m in db.query(Memory).all()}
    assert "Matt's accountant is Jane Doe" in contents


def test_reflect_dedupes_semantically(db, monkeypatch):
    c = _seed_convo(db)
    # pre-existing near-identical memory
    from app import vectorstore
    vectorstore.add(db, Memory(content="Matt's accountant is Jane Doe", category="people"))
    facts_json = '[{"content":"Matt\'s accountant is Jane Doe","category":"people"}]'
    install_llm(monkeypatch, ScriptedLLM(response([text_block(facts_json)])))
    n = reflector.reflect_conversation(db, c.id)
    assert n == 0  # duplicate not stored again
    assert db.query(Memory).filter(Memory.content == "Matt's accountant is Jane Doe").count() == 1
