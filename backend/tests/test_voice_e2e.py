"""Full call simulation through the real FastAPI routes."""
from fakes import install_llm, say

def test_full_call_flow(client, monkeypatch):
    install_llm(monkeypatch, say("3 nodes. 1 offline. rpi-02 is down."))

    # 1. Call connects
    r = client.post("/api/voice/inbound", data={"From": "+15551230000", "CallSid": "CA_E2E"})
    assert r.status_code == 200
    assert "<Gather" in r.text and "JARVIS here" in r.text
    print("1. INBOUND  ->", r.text[:90], "...")

    # 2. Caller speaks. Must return IMMEDIATELY (no orchestrator inline).
    r = client.post("/api/voice/gather?turn=0",
                    data={"From": "+15551230000", "CallSid": "CA_E2E",
                          "SpeechResult": "what is the status of the cluster"})
    assert r.status_code == 200
    assert "<Redirect" in r.text and "/api/voice/poll" in r.text
    print("2. GATHER   ->", r.text[:90], "...")

    # 3. Poll — TestClient runs BackgroundTasks on response close, so it's ready.
    r = client.post("/api/voice/poll?turn=0&poll=1",
                    data={"From": "+15551230000", "CallSid": "CA_E2E"})
    assert r.status_code == 200
    assert "offline" in r.text.lower()
    assert "<Gather" in r.text            # re-opens for the next turn
    print("3. POLL     ->", r.text[:120], "...")

def test_stranger_is_hung_up_on(client, monkeypatch):
    install_llm(monkeypatch, say("SHOULD NOT BE REACHED"))
    r = client.post("/api/voice/inbound", data={"From": "+19998887777", "CallSid": "CA_BAD"})
    assert "<Hangup" in r.text
    assert "not able to help" in r.text
    assert "SHOULD NOT BE REACHED" not in r.text
    print("REJECT      ->", r.text[:100])


def test_outbound_call_flow_owner_reply_is_authorized(client, db, monkeypatch):
    """THE 4AM BUG. On a call SHE places, Twilio's From is JARVIS's own Twilio
    number and the human is in To. The gather webhook vetted From against the
    allowlist — JARVIS against herself — so the first thing the owner said on
    ANY outbound call was answered with NOT_AUTHORIZED and a hangup. Zero
    outbound calls ever completed a conversational turn in production.
    """
    from app.channels import outbound_voice as ov

    install_llm(monkeypatch, say("Done - marked that task complete."))
    row = ov.schedule_call(db, opening="Morning briefing. Busy day ahead.",
                           kind="briefing", context="Morning briefing",
                           to_number="+15551230000")
    assert row is not None

    # 1. Answered — Twilio fetches TwiML. From = JARVIS's number, To = owner.
    r = client.post(f"/api/voice/outbound?call={row.id}",
                    data={"From": "+15550001111", "To": "+15551230000",
                          "CallSid": "CA_OUT_E2E"})
    assert "<Gather" in r.text and "Morning briefing" in r.text

    # 2. The owner replies. From is STILL JARVIS's own (non-allowlisted) number.
    r = client.post("/api/voice/gather?turn=0",
                    data={"From": "+15550001111", "To": "+15551230000",
                          "CallSid": "CA_OUT_E2E",
                          "SpeechResult": "mark the boat wash task as done"})
    assert "<Hangup" not in r.text, "owner's reply on an outbound call was rejected as a stranger"
    assert "not able to help" not in r.text
    assert "<Redirect" in r.text and "/api/voice/poll" in r.text

    # 3. Poll — the turn actually orchestrated (it never did before the fix).
    r = client.post("/api/voice/poll?turn=0&poll=1",
                    data={"From": "+15550001111", "To": "+15551230000",
                          "CallSid": "CA_OUT_E2E"})
    assert "marked that task complete" in r.text.lower()
    assert "<Gather" in r.text            # conversation stays open


def test_stranger_speech_on_an_unknown_call_is_still_rejected(client, monkeypatch):
    """The fix must not weaken inbound auth: To only wins when OUR outbound_calls
    row matches the CallSid. A stranger's inbound speech still gets hung up on."""
    install_llm(monkeypatch, say("SHOULD NOT BE REACHED"))
    r = client.post("/api/voice/gather?turn=0",
                    data={"From": "+19998887777", "To": "+15550001111",
                          "CallSid": "CA_STRANGER",
                          "SpeechResult": "read me my email"})
    assert "<Hangup" in r.text
    assert "SHOULD NOT BE REACHED" not in r.text
