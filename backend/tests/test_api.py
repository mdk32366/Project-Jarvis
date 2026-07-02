from fakes import install_llm, say


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["database"] == "connected"


def test_login_and_me(client, auth_headers):
    r = client.get("/api/auth/me", headers=auth_headers)
    assert r.status_code == 200 and r.json()["username"] == "admin"


def test_login_bad_password(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_protected_routes_require_auth(client):
    assert client.get("/api/memory").status_code == 401
    assert client.get("/api/conversations").status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_chat_endpoint(client, auth_headers, monkeypatch):
    install_llm(monkeypatch, say("Hello from JARVIS."))
    r = client.post("/api/chat", json={"message": "hi", "thread_key": "t1"}, headers=auth_headers)
    assert r.status_code == 200
    assert "JARVIS" in r.json()["reply"]


def test_memory_crud(client, auth_headers):
    r = client.post("/api/memory", json={"content": "test fact", "category": "context"}, headers=auth_headers)
    assert r.status_code == 200
    mid = r.json()["id"]
    assert any(m["content"] == "test fact" for m in client.get("/api/memory", headers=auth_headers).json())
    assert client.delete(f"/api/memory/{mid}", headers=auth_headers).json() == {"deleted": mid}
    assert client.delete("/api/memory/999999", headers=auth_headers).status_code == 404


def test_sms_webhook_whitelisted(client, monkeypatch, stub_sms):
    install_llm(monkeypatch, say("Reply via SMS."))
    r = client.post("/api/sms/inbound", data={"From": "+15551230000", "Body": "hi"})
    assert r.status_code == 200
    assert "<Message>Reply via SMS.</Message>" in r.text
    assert r.headers["content-type"].startswith("application/xml")


def test_sms_webhook_non_whitelisted_empty(client, monkeypatch, stub_sms):
    install_llm(monkeypatch, say("should not reach"))
    r = client.post("/api/sms/inbound", data={"From": "+19998887777", "Body": "hi"})
    assert r.status_code == 200
    assert r.text.strip().endswith("<Response></Response>")


def test_jobs_endpoint(client, auth_headers, monkeypatch):
    install_llm(monkeypatch, say("hi"))
    client.post("/api/chat", json={"message": "hi", "thread_key": "t1"}, headers=auth_headers)
    jobs = client.get("/api/jobs", headers=auth_headers).json()
    assert any(j["kind"] == "reflect" for j in jobs)


def test_change_password_flow(client, auth_headers):
    # wrong current -> 400
    assert client.post("/api/auth/change-password", headers=auth_headers,
                       json={"current_password": "nope", "new_password": "supersecret1"}).status_code == 400
    # too short -> 400
    assert client.post("/api/auth/change-password", headers=auth_headers,
                       json={"current_password": "testpass", "new_password": "short"}).status_code == 400
    # success
    r = client.post("/api/auth/change-password", headers=auth_headers,
                    json={"current_password": "testpass", "new_password": "supersecret1"})
    assert r.status_code == 200
    # old password no longer works, new one does
    assert client.post("/api/auth/login", data={"username": "admin", "password": "testpass"}).status_code == 401
    assert client.post("/api/auth/login", data={"username": "admin", "password": "supersecret1"}).status_code == 200
