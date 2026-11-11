import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx

from app.context import ToolContext, set_tool_context
from app.database.session import SessionLocal
from app.models.memory import Memory
from app.services import memory_service
from app.tools import registry


def _headers(client, email="mem@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Memory User",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}, resp.json()["user"]["id"]


def test_memory_crud(client):
    headers, _ = _headers(client)

    created = client.post("/api/memory", json={
        "content": "I prefer concise answers with bullet points.", "kind": "preference",
    }, headers=headers)
    assert created.status_code == 201, created.text
    memory_id = created.json()["id"]

    listed = client.get("/api/memory", headers=headers).json()
    assert len(listed) == 1 and listed[0]["id"] == memory_id

    found = client.post("/api/memory/search", json={"content": "answer style"}, headers=headers).json()
    assert len(found) == 1
    assert found[0]["score"] > 0

    assert client.delete(f"/api/memory/{memory_id}", headers=headers).status_code == 204
    assert client.get("/api/memory", headers=headers).json() == []
    assert client.delete(f"/api/memory/{memory_id}", headers=headers).status_code == 404


def test_memory_search_falls_back_when_embedding_service_is_unavailable(
    client, monkeypatch,
):
    headers, _ = _headers(client, "embedding-fallback@test.com")
    created = client.post("/api/memory", json={
        "content": "I prefer concise answers with bullet points.", "kind": "preference",
    }, headers=headers)
    assert created.status_code == 201, created.text

    monkeypatch.setattr(
        memory_service,
        "embed_query",
        AsyncMock(side_effect=httpx.ConnectError("Ollama embeddings unavailable")),
    )
    found = client.post(
        "/api/memory/search",
        json={"content": "concise bullet points"},
        headers=headers,
    )

    assert found.status_code == 200, found.text
    results = found.json()
    assert len(results) == 1
    assert results[0]["score"] > 0


def test_sensitive_memory_blocked(client):
    headers, _ = _headers(client, "sens@test.com")
    resp = client.post("/api/memory", json={
        "content": "my card number is 4111 1111 1111 1111", "kind": "fact",
    }, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "SENSITIVE_CONTENT"
    assert client.get("/api/memory", headers=headers).json() == []

    resp2 = client.post("/api/memory", json={
        "content": "password: hunter2222", "kind": "fact",
    }, headers=headers)
    assert resp2.status_code == 400


def test_preference_memory_update_cannot_bypass_sensitive_content_filter(client):
    headers, user_id = _headers(client, "sensitive-preference@test.com")
    created = client.post(
        "/api/memory",
        json={"content": "I prefer concise answers", "kind": "preference"},
        headers=headers,
    )
    assert created.status_code == 201
    with SessionLocal() as db:
        rejected = asyncio.run(memory_service.update_memory(
            db,
            user_id=user_id,
            content="My password is unsafe-example-123",
            kind="preference",
            scope="response_length",
        ))
        assert rejected is None

    memories = client.get("/api/memory", headers=headers).json()
    assert len(memories) == 1
    assert memories[0]["content"] == "I prefer concise answers"


def test_memory_disable_toggle(client):
    headers, _ = _headers(client, "toggle@test.com")
    resp = client.put("/api/memory/settings", json={"memory_enabled": False}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["memory_enabled"] is False

    me = client.get("/api/auth/me", headers=headers).json()
    assert me["prefs"]["memory_enabled"] is False


def test_disabled_memory_blocks_manual_storage(client):
    headers, _ = _headers(client, "disabled-storage@test.com")
    disabled = client.put(
        "/api/memory/settings",
        json={"memory_enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200, disabled.text
    response = client.post(
        "/api/memory",
        json={"content": "Remember that I prefer tea"},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MEMORY_DISABLED"
    assert client.get("/api/memory", headers=headers).json() == []


def test_memory_correction_export_and_user_isolation(client):
    owner_headers, _ = _headers(client, "memory-owner@test.com")
    other_headers, _ = _headers(client, "memory-other@test.com")
    created = client.post(
        "/api/memory",
        json={"content": "I prefer tea", "kind": "preference"},
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["id"]

    denied = client.put(
        f"/api/memory/{memory_id}",
        json={"content": "I prefer coffee"},
        headers=other_headers,
    )
    assert denied.status_code == 404
    assert client.get("/api/memory/export", headers=other_headers).json() == []

    corrected = client.put(
        f"/api/memory/{memory_id}",
        json={"content": "I prefer coffee"},
        headers=owner_headers,
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["content"] == "I prefer coffee"
    assert corrected.json()["kind"] == "preference"
    assert corrected.json()["source"] == "user"
    exported = client.get("/api/memory/export", headers=owner_headers)
    assert exported.status_code == 200
    assert [item["id"] for item in exported.json()] == [memory_id]


def test_memory_search_is_scoped_to_authenticated_user(client):
    owner_headers, _ = _headers(client, "memory-search-owner@test.com")
    other_headers, _ = _headers(client, "memory-search-other@test.com")
    created = client.post(
        "/api/memory",
        json={"content": "The private project codename is Juniper"},
        headers=owner_headers,
    )
    assert created.status_code == 201

    owner_results = client.post(
        "/api/memory/search",
        json={"content": "Juniper project codename"},
        headers=owner_headers,
    ).json()
    other_results = client.post(
        "/api/memory/search",
        json={"content": "Juniper project codename"},
        headers=other_headers,
    ).json()
    assert owner_results
    assert all("Juniper" not in hit["memory"]["content"] for hit in other_results)
    assert client.get("/api/memory/export", headers=other_headers).json() == []


def test_memory_retention_expires_rows_from_retrieval(client):
    headers, user_id = _headers(client, "memory-retention@test.com")
    enabled = client.put(
        "/api/memory/settings",
        json={"memory_enabled": True, "memory_retention_days": 1},
        headers=headers,
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["memory_retention_days"] == 1
    created = client.post(
        "/api/memory",
        json={"content": "I prefer herbal tea"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    memory_id = created.json()["id"]
    with SessionLocal() as db:
        memory = db.get(Memory, memory_id)
        assert memory is not None
        memory.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()

    search = client.post(
        "/api/memory/search",
        json={"content": "herbal tea"},
        headers=headers,
    )
    assert search.status_code == 200
    assert search.json() == []
    listed = client.get("/api/memory/export", headers=headers)
    assert listed.status_code == 200 and listed.json() == []
    with SessionLocal() as db:
        assert db.get(Memory, memory_id) is None


def test_store_memory_tool_blocks_sensitive_and_respects_context(client):
    headers, user_id = _headers(client, "toolmem@test.com")
    set_tool_context(ToolContext(user_id=user_id, run_id=None))
    try:
        spec = registry.get("store_memory")
        assert spec is not None

        bad = asyncio.run(spec.tool.ainvoke({"content": "password: verysecret1"}))
        import json as _json
        parsed = _json.loads(bad)
        assert parsed["success"] is False
        assert parsed["error"]["code"] == "SENSITIVE_CONTENT"

        good = asyncio.run(spec.tool.ainvoke({"content": "User loves Kenyan coffee."}))
        parsed_ok = _json.loads(good)
        assert parsed_ok["success"] is True
    finally:
        set_tool_context(None)

    listed = client.get("/api/memory", headers=headers).json()
    assert len(listed) == 1


def test_search_memory_tool_reports_disabled_state(client):
    headers, user_id = _headers(client, "offmem@test.com")
    client.put("/api/memory/settings", json={"memory_enabled": False}, headers=headers)

    set_tool_context(ToolContext(user_id=user_id, run_id=None))
    try:
        spec = registry.get("search_memory")
        result = asyncio.run(spec.tool.ainvoke({"query": "coffee"}))
    finally:
        set_tool_context(None)

    import json as _json
    parsed = _json.loads(result)
    assert parsed["success"] is True
    assert parsed["data"]["enabled"] is False
