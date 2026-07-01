from app.models import Job, Memory
from app.orchestrator import run
from fakes import install_llm, say, use_tool_then


def test_plain_qa_no_tools(db, monkeypatch):
    install_llm(monkeypatch, say("Three names: Nimbus, Atlas, Sentinel."))
    reply = run(db, channel="web", thread_key="web:admin:1", user_text="name my server", actor="admin")
    assert "Atlas" in reply


def test_remember_via_archivist_delegation(db, monkeypatch):
    from fakes import ScriptedLLM, response, text_block, tool_block
    llm = ScriptedLLM(
        response([tool_block("delegate", {"agent": "archivist", "task": "remember the garage code note"})],
                 stop_reason="tool_use"),                                   # main -> delegate
        response([tool_block("remember_fact",
                             {"content": "Matt's garage code is on file", "category": "context"})],
                 stop_reason="tool_use"),                                   # archivist -> remember_fact
        response([text_block("Saved.")], stop_reason="end_turn"),          # archivist synth
        response([text_block("Done — noted.")], stop_reason="end_turn"),   # main synth
    )
    install_llm(monkeypatch, llm)
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
