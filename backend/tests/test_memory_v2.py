"""P5 — Memory v2: classification, pluggable vector store (SQL/Chroma), provenance."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config import get_settings
from app.context import ToolContext, set_tool_context
from app.services import memory_classify, memory_service, vector_store
from app.tools import registry


def _headers(client, email="memv2@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "V2 User",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}, resp.json()["user"]["id"]


# --------------------------------------------------------------------------- #
# classification (§13)                                                         #
# --------------------------------------------------------------------------- #
def test_classify_kind_rules():
    classify = memory_classify.classify_kind
    assert classify("Ping me at jo@acme.example about the report") == "contact"
    assert classify("call +254 712 345 678 tomorrow") == "contact"
    assert classify("my wife Grace prefers window seats") == "person"
    assert classify("I love thick-crust pizza") == "preference"
    assert classify("the launch deadline is Friday and the sprint ends Sunday") == "project"
    assert classify("the sky is blue") == "fact"
    assert classify("") == "fact"


def test_add_memory_auto_classified_via_api(client):
    headers, _ = _headers(client, "auto@test.com")
    created = client.post("/api/memory", json={
        "content": "I always want answers in bullet points", "kind": "auto",
    }, headers=headers)
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "preference"

    default = client.post("/api/memory", json={
        "content": "the sky is blue",
    }, headers=headers)
    assert default.status_code == 201
    assert default.json()["kind"] == "fact"  # API default unchanged


def test_store_memory_tool_classifies_and_records_provenance(client):
    headers, user_id = _headers(client, "toolcls@test.com")
    set_tool_context(ToolContext(user_id=user_id, run_id=None))
    try:
        spec = registry.get("store_memory")
        result = asyncio.run(spec.tool.ainvoke(
            {"content": "User loves Kenyan coffee with no sugar"}))
    finally:
        set_tool_context(None)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["data"]["kind"] == "preference"

    listed = client.get("/api/memory", headers=headers).json()
    assert len(listed) == 1
    assert listed[0]["source"] == "agent"      # provenance
    assert listed[0]["kind"] == "preference"   # classification


def test_provenance_source_for_user_additions(client):
    headers, _ = _headers(client, "prov@test.com")
    created = client.post("/api/memory", json={
        "content": "My boss is named Amina", "kind": "person",
    }, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["source"] == "user"
    assert body["kind"] == "person"
    assert body["created_at"]


# --------------------------------------------------------------------------- #
# SQL vector store (offline default)                                          #
# --------------------------------------------------------------------------- #
def test_sql_store_search_delete_roundtrip(client):
    headers, _ = _headers(client, "sqlsrch@test.com")
    assert vector_store.get_vector_store().name == "sql"
    client.post("/api/memory", json={"content": "I prefer espresso over filter coffee"},
                headers=headers)
    client.post("/api/memory", json={"content": "The office wifi password rotation is monthly"},
                headers=headers)

    hits = client.post("/api/memory/search", json={"content": "coffee preference"},
                       headers=headers).json()
    assert hits and "espresso" in hits[0]["memory"]["content"]

    memory_id = hits[0]["memory"]["id"]
    assert client.delete(f"/api/memory/{memory_id}", headers=headers).status_code == 204
    hits2 = client.post("/api/memory/search", json={"content": "coffee preference"},
                        headers=headers).json()
    assert all(h["memory"]["id"] != memory_id for h in hits2)


def test_store_failure_falls_back_to_sql(client, monkeypatch):
    headers, _ = _headers(client, "fallback@test.com")
    client.post("/api/memory", json={"content": "I prefer espresso over filter coffee"},
                headers=headers)

    def _boom():
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(vector_store, "get_vector_store", _boom)
    hits = client.post("/api/memory/search", json={"content": "coffee preference"},
                       headers=headers).json()
    assert hits and "espresso" in hits[0]["memory"]["content"]


# --------------------------------------------------------------------------- #
# ChromaDB index (§12)                                                         #
# --------------------------------------------------------------------------- #
@pytest.fixture
def chroma_store(monkeypatch, tmp_path):
    pytest.importorskip("chromadb", reason="chromadb optional vector backend not installed")
    monkeypatch.setenv("JALORA_MEMORY_VECTORSTORE", "chroma")
    monkeypatch.setenv("JALORA_CHROMA_DIR", str(tmp_path / "chroma"))
    get_settings.cache_clear()
    vector_store.reset()
    store = vector_store.get_vector_store()
    yield store
    vector_store.reset()
    get_settings.cache_clear()


def test_chroma_resolution_when_selected(chroma_store):
    assert chroma_store.name == "chroma"


def test_chroma_end_to_end(client, chroma_store):
    headers, user_id = _headers(client, "chroma@test.com")
    assert vector_store.get_vector_store().name == "chroma"

    client.post("/api/memory", json={"content": "I prefer dark mode interfaces"},
                headers=headers)
    client.post("/api/memory", json={"content": "My cat is called Luna"}, headers=headers)
    assert chroma_store._indexed_count(user_id) == 2

    hits = client.post("/api/memory/search", json={"content": "dark mode preference"},
                       headers=headers).json()
    assert hits and "dark mode" in hits[0]["memory"]["content"]

    memory_id = hits[0]["memory"]["id"]
    assert client.delete(f"/api/memory/{memory_id}", headers=headers).status_code == 204
    assert chroma_store._indexed_count(user_id) == 1
    hits2 = client.post("/api/memory/search", json={"content": "dark mode preference"},
                        headers=headers).json()
    assert all(h["memory"]["id"] != memory_id for h in hits2)


def test_chroma_lazy_reindex_when_index_drifts(client, chroma_store):
    headers, user_id = _headers(client, "reindex@test.com")
    client.post("/api/memory", json={"content": "I prefer dark mode interfaces"},
                headers=headers)
    client.post("/api/memory", json={"content": "My cat is called Luna"}, headers=headers)
    assert chroma_store._indexed_count(user_id) == 2

    # simulate drift: index loses entries while SQL rows survive
    chroma_store._collection().delete(where={"user_id": user_id})
    assert chroma_store._indexed_count(user_id) == 0

    hits = client.post("/api/memory/search", json={"content": "dark mode preference"},
                       headers=headers).json()
    assert hits, "search should lazily rebuild the chroma index from SQL"
    assert chroma_store._indexed_count(user_id) == 2


def test_auto_resolution_prefers_chroma_when_available(monkeypatch, tmp_path):
    monkeypatch.delenv("JALORA_MEMORY_VECTORSTORE", raising=False)
    monkeypatch.setenv("JALORA_CHROMA_DIR", str(tmp_path / "auto_chroma"))
    get_settings.cache_clear()
    vector_store.reset()
    try:
        store = vector_store.get_vector_store()
        assert store.name == ("chroma" if vector_store.chroma_available() else "sql")
    finally:
        vector_store.reset()
        get_settings.cache_clear()
