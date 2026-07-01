from app.models import Job, Memory
from app.orchestrator import run
from fakes import install_llm, say, use_tool_then


def test_plain_qa_no_tools(db, monkeypatch):
    install_llm(monkeypatch, say("Three names: Nimbus, Atlas, Sentinel."))
    reply = run(db, channel="web", thread_key="web:admin:1", user_text="name my server", actor="admin")
    assert "Atlas" in reply


def test_remember_fact_tool_persists(db, monkeypatch):
    install_llm(monkeypatch, use_tool_then(
        "Got it — I'll remember that.", "remember_fact",
        {"content": "Matt's garage code is on file", "category": "context"}))
    run(db, channel="web", thread_key="web:admin:1", user_text="remember my garage code note", actor="admin")
    assert db.query(Memory).filter(Memory.content == "Matt's garage code is on file").count() == 1


def test_turn_enqueues_reflect_job(db, monkeypatch):
    install_llm(monkeypatch, say("ok"))
    run(db, channel="web", thread_key="web:admin:1", user_text="hi", actor="admin")
    assert db.query(Job).filter(Job.kind == "reflect").count() == 1


def test_conversation_history_persists(db, monkeypatch):
    install_llm(monkeypatch, say("noted"))
    run(db, channel="web", thread_key="web:admin:x", user_text="my ticker is AMD", actor="admin")
    install_llm(monkeypatch, say("You said AMD."))
    reply = run(db, channel="web", thread_key="web:admin:x", user_text="what did I say?", actor="admin")
    # second call should have received prior turns in its messages
    assert "AMD" in reply
