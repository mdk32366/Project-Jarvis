from fakes import install_llm, ScriptedLLM, response, text_block, tool_block, say


def test_agents_seeded(client, auth_headers):
    agents = client.get("/api/agents", headers=auth_headers).json()
    names = {a["name"] for a in agents}
    assert {"researcher", "finance", "archivist", "scheduling"} <= names


def test_agents_require_auth(client):
    assert client.get("/api/agents").status_code == 401
    assert client.get("/api/audit").status_code == 401


def test_agent_crud(client, auth_headers):
    # create
    r = client.post("/api/agents", headers=auth_headers, json={
        "name": "legal", "description": "Legal research", "system_prompt": "You are a paralegal.",
        "tools": [], "enabled": True})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    # duplicate name rejected
    assert client.post("/api/agents", headers=auth_headers, json={"name": "legal"}).status_code == 409
    # update
    r = client.put(f"/api/agents/{aid}", headers=auth_headers, json={
        "name": "legal", "description": "Updated", "system_prompt": "New prompt.",
        "tools": ["get_stock_price"], "enabled": False})
    assert r.json()["description"] == "Updated" and r.json()["tools"] == ["get_stock_price"]
    # delete
    assert client.delete(f"/api/agents/{aid}", headers=auth_headers).json() == {"deleted": aid}
    assert client.delete(f"/api/agents/{aid}", headers=auth_headers).status_code == 404


def test_assignable_tools(client, auth_headers):
    tools = client.get("/api/agents/tools", headers=auth_headers).json()["tools"]
    assert "get_stock_price" in tools and "remember_fact" in tools and "calendar_lookup" in tools
    assert "delegate" not in tools and "place_stock_order" not in tools


def test_audit_records_delegation(client, auth_headers, monkeypatch):
    llm = ScriptedLLM(
        response([tool_block("delegate", {"agent": "researcher", "task": "x"})], stop_reason="tool_use"),
        response([text_block("sub")], stop_reason="end_turn"),
        response([text_block("final")], stop_reason="end_turn"),
    )
    install_llm(monkeypatch, llm)
    client.post("/api/chat", json={"message": "hi", "thread_key": "t"}, headers=auth_headers)
    audit = client.get("/api/audit", headers=auth_headers).json()
    assert any(row["tool"] == "delegate" for row in audit)


def test_scheduling_delegation_stub(db, monkeypatch):
    from app.orchestrator import run
    llm = ScriptedLLM(
        response([tool_block("delegate", {"agent": "scheduling", "task": "what's on today?"})], stop_reason="tool_use"),
        response([tool_block("calendar_lookup", {"range": "today"})], stop_reason="tool_use"),  # scheduling agent
        response([text_block("Calendar isn't connected yet.")], stop_reason="end_turn"),        # scheduling synth
        response([text_block("Your calendar isn't connected yet.")], stop_reason="end_turn"),   # main synth
    )
    install_llm(monkeypatch, llm)
    reply = run(db, channel="web", thread_key="web:admin:1", user_text="what's on today?", actor="admin")
    assert "isn't connected" in reply.lower() or "not yet connected" in reply.lower()
