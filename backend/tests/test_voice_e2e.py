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
