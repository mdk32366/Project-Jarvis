"""Voice hold/wait state — the 'let me wait for the research' path.

Reproduces the bug: a slow turn timed out, the 'wait or call back' choice did
nothing useful, and a silent waiting caller got looped with 'Still there?'. The
fix: an explicit hold state that plays music / reassurance without listening,
speaks the answer when ready, and hands off to email after a budget.
"""
import time

from app.channels import voice_pipeline as vp
from app.models import Job, VoiceTurn


def _seed_turn(db, call_sid, turn, status="pending", reply="", notify=False):
    row = VoiceTurn(call_sid=call_sid, turn=turn, status=status,
                    user_text="how do I change the fuel filters", reply=reply,
                    notify_email=notify)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ── vocabulary ───────────────────────────────────────────────────────────────
def test_wait_words_hold_callback_words_hand_off():
    assert vp.wants_to_hold("I want to wait") is True
    assert vp.wants_to_hold("keep going") is True
    assert vp.wants_to_hold("") is True                 # silence/unrecognized -> hold
    assert vp.wants_to_hold("uhh what") is True
    assert vp.wants_callback("just email it to me") is True
    assert vp.wants_callback("call me back") is True
    assert vp.wants_to_hold("call me back") is False


# ── hold TwiML never listens (that was the loop) ─────────────────────────────
def test_hold_twiml_has_no_gather():
    xml = vp.twiml_hold(turn=1, since=int(time.time()), intro=True)
    assert "<Gather" not in xml            # no listening -> no 'Still there?' loop
    assert "Still there?" not in xml
    assert "/api/voice/hold" in xml        # it re-checks itself


def test_hold_twiml_plays_configured_music(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "voice_hold_music_url", "https://cdn.example.com/spy.mp3")
    xml = vp.twiml_hold(turn=1, since=int(time.time()))
    assert "<Play>https://cdn.example.com/spy.mp3</Play>" in xml


# ── choosing to wait enters the hold loop on the SAME turn ───────────────────
def test_choice_wait_enters_hold(client, db):
    _seed_turn(db, "CA_HOLD", 0, status="pending")
    r = client.post("/api/voice/hold_choice?turn=0",
                    data={"From": "+15551230000", "CallSid": "CA_HOLD",
                          "SpeechResult": "I'll wait"})
    assert r.status_code == 200
    assert "/api/voice/hold" in r.text
    assert "<Gather" not in r.text


def test_choice_email_hands_off_and_flags_notify(client, db):
    row = _seed_turn(db, "CA_OFF", 0, status="pending")
    r = client.post("/api/voice/hold_choice?turn=0",
                    data={"From": "+15551230000", "CallSid": "CA_OFF",
                          "SpeechResult": "just email it to me"})
    assert "<Hangup" in r.text
    db.refresh(row)
    assert row.notify_email is True


# ── holding until the answer is ready speaks it ──────────────────────────────
def test_hold_speaks_answer_when_done(client, db):
    _seed_turn(db, "CA_DONE", 0, status="done",
               reply="Loosen the bleed screw, then prime the pump.")
    r = client.post("/api/voice/hold?turn=0&since=%d" % int(time.time()),
                    data={"From": "+15551230000", "CallSid": "CA_DONE"})
    assert "prime the pump" in r.text.lower()
    assert "<Gather" in r.text             # conversation resumes


def test_hold_past_budget_hands_off_to_email(client, db):
    row = _seed_turn(db, "CA_LONG", 0, status="pending")
    # since far enough in the past to exceed voice_hold_max_seconds (300s).
    r = client.post("/api/voice/hold?turn=0&since=%d" % (int(time.time()) - 10_000),
                    data={"From": "+15551230000", "CallSid": "CA_LONG"})
    assert "<Hangup" in r.text
    db.refresh(row)
    assert row.notify_email is True


# ── the finished answer actually gets emailed (notify_email honored) ─────────
def test_run_turn_emails_when_notify_set(db, monkeypatch):
    from fakes import install_llm, say
    install_llm(monkeypatch, say("Here is the full fuel-filter procedure."))
    _seed_turn(db, "CA_EMAIL", 0, status="pending", notify=True)

    vp.run_turn("CA_EMAIL", 0, "+15551230000", "how do I change the fuel filters")

    jobs = db.query(Job).filter(Job.kind == "email_copy").all()
    assert len(jobs) == 1
    assert "fuel-filter" in jobs[0].payload
