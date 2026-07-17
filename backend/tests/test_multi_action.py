"""Multi-action buffering (TDD-multi-action-buffering): 'do this, that, and the
other' -> N gated actions read back as one batch and cleared with a single reply.

Tests first. Drives the real orchestrator loop with a scripted LLM that calls two
gated tools (send_email) in one turn, then asserts on the batch behavior.
"""
from app.models import ActionAudit, PendingConfirmation
from fakes import install_llm, response, say, text_block, tool_block


def _two_email_turn(monkeypatch):
    """A scripted LLM that queues two emails in one turn, then reports."""
    sent = []
    monkeypatch.setattr("app.notifier.send_email",
                        lambda *a, **k: sent.append(a) or "msg-id")
    from fakes import ScriptedLLM
    llm = ScriptedLLM(
        response([tool_block("send_email",
                             {"to": "a@example.com", "subject": "Alpha", "body": "a"},
                             id="t1")], stop_reason="tool_use"),
        response([tool_block("send_email",
                             {"to": "b@example.com", "subject": "Bravo", "body": "b"},
                             id="t2")], stop_reason="tool_use"),
        response([text_block("Two emails queued — reply confirm to send both.")],
                 stop_reason="end_turn"),
    )
    install_llm(monkeypatch, llm)
    return sent


def test_two_gated_actions_share_a_batch_and_do_not_execute(db, monkeypatch):
    from app.orchestrator import run
    sent = _two_email_turn(monkeypatch)
    run(db, channel="sms", thread_key="+1555", user_text="email Alpha and email Bravo", actor="+1555")

    pend = db.query(PendingConfirmation).order_by(PendingConfirmation.id).all()
    assert len(pend) == 2, "both gated actions should be buffered"
    assert pend[0].batch_id and pend[0].batch_id == pend[1].batch_id, "same batch"
    assert all(p.status == "pending" for p in pend)
    assert sent == [], "nothing sent before confirmation"


def test_one_confirm_executes_the_whole_batch(db, monkeypatch):
    from app.orchestrator import run
    sent = _two_email_turn(monkeypatch)
    run(db, channel="sms", thread_key="+1555", user_text="email Alpha and email Bravo", actor="+1555")

    reply = run(db, channel="sms", thread_key="+1555", user_text="confirm", actor="+1555")

    assert len(sent) == 2, "a single confirm sends BOTH emails"
    assert db.query(PendingConfirmation).filter(PendingConfirmation.status == "done").count() == 2
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 2
    assert "Alpha" in reply and "Bravo" in reply, "combined summary names both deliverables"


def test_one_cancel_cancels_the_whole_batch(db, monkeypatch):
    from app.orchestrator import run
    sent = _two_email_turn(monkeypatch)
    run(db, channel="sms", thread_key="+1555", user_text="email Alpha and email Bravo", actor="+1555")

    run(db, channel="sms", thread_key="+1555", user_text="cancel", actor="+1555")

    assert sent == []
    assert db.query(PendingConfirmation).filter(PendingConfirmation.status == "cancelled").count() == 2
    assert db.query(ActionAudit).filter(ActionAudit.status == "confirmed").count() == 0


def test_ungated_actions_run_before_gated_ones_buffer(db, monkeypatch):
    """Explicit ordering: no-confirmation work happens FIRST, even when the model
    emits the gated tool BEFORE the ungated one. The ungated tool's spy records
    how many gated actions were buffered at the moment it ran — must be zero."""
    import app.handlers.datetime_tools as dt
    from app.orchestrator import run

    real = dt._get_current_datetime
    seen = {}

    def spy(args, ctx):
        seen["pending_at_exec"] = ctx.db.query(PendingConfirmation).count()
        return real(args, ctx)

    monkeypatch.setattr(dt, "_get_current_datetime", spy)
    monkeypatch.setattr("app.notifier.send_email", lambda *a, **k: "msg-id")

    from fakes import ScriptedLLM
    # NOTE: the GATED tool (send_email) is emitted FIRST, the ungated one second.
    llm = ScriptedLLM(
        response([tool_block("send_email",
                             {"to": "a@example.com", "subject": "S", "body": "b"}, id="t1"),
                  tool_block("get_current_datetime", {}, id="t2")],
                 stop_reason="tool_use"),
        response([text_block("Time's noted; email queued — reply confirm.")], stop_reason="end_turn"),
    )
    install_llm(monkeypatch, llm)

    run(db, channel="sms", thread_key="+1555",
        user_text="what time is it, and email S", actor="+1555")

    assert seen.get("pending_at_exec") == 0, \
        "the ungated action must execute before any gated action is buffered"
    assert db.query(PendingConfirmation).filter_by(status="pending").count() == 1


def test_a_single_gated_action_still_confirms_normally(db, monkeypatch):
    """Don't regress the common case: one action, one 'yes', one deliverable."""
    from app.orchestrator import run
    sent = []
    monkeypatch.setattr("app.notifier.send_email", lambda *a, **k: sent.append(a) or "msg-id")
    from fakes import ScriptedLLM
    llm = ScriptedLLM(
        response([tool_block("send_email",
                             {"to": "a@example.com", "subject": "Solo", "body": "x"}, id="t1")],
                 stop_reason="tool_use"),
        response([text_block("Queued — reply confirm.")], stop_reason="end_turn"),
    )
    install_llm(monkeypatch, llm)
    run(db, channel="sms", thread_key="+1555", user_text="email Solo", actor="+1555")

    reply = run(db, channel="sms", thread_key="+1555", user_text="confirm", actor="+1555")
    assert len(sent) == 1
    assert "Solo" in reply
    assert db.query(PendingConfirmation).filter(PendingConfirmation.status == "done").count() == 1
