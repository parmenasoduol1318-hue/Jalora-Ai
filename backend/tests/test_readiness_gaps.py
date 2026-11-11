"""Targeted production-readiness gap tests.

These cover checklist items that the broader Phase X test files touch only
indirectly, or not at all: API-level input validation (empty/ambiguous
prompts), IDOR on conversation deletion, SQL-injection safety on document
search, and a *recovery* path when the configured provider comes back as
offline so the turn produces a structured error instead of an HTTP 500.
"""
from __future__ import annotations

import httpx


# --------------------------------------------------------------------------- #
# 1.11  invalid / empty / ambiguous prompts at the API boundary                #
# --------------------------------------------------------------------------- #
def test_empty_message_is_rejected_by_validation(client, auth_headers):
    """An empty prompt must be rejected at the schema boundary (HTTP 422),
    never forwarded to the model graph."""
    headers = auth_headers()
    resp = client.post("/api/chat", json={"message": ""}, headers=headers)
    assert resp.status_code == 422, resp.text
    # FastAPI/Pydantic validation error shape, never an unstructured 500.
    body = resp.json()
    assert "detail" in body
    assert resp.headers.get("content-type", "").startswith(
        "application/json"
    )


def test_missing_message_field_is_rejected(client, auth_headers):
    """A body that omits `message` entirely must 422 — not crash the chat path."""
    headers = auth_headers()
    resp = client.post("/api/chat", json={"stream": True}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["detail"]


# --------------------------------------------------------------------------- #
# 2.13  IDOR — users must not access another user's conversations            #
# --------------------------------------------------------------------------- #
def test_conversation_messages_are_scoped_to_owner(client, auth_headers):
    """User B cannot read user A's conversation messages (IDOR)."""
    from langchain_core.messages import AIMessage

    headers_a = auth_headers()
    headers_b = auth_headers("b-idor@test.com")

    resp = client.post(
        "/api/chat",
        json={"message": "hello"},
        headers=headers_a,
    )
    assert resp.status_code == 200
    conv_id = resp.json()["conversation_id"]

    # B can list conversations but must NOT see A's, nor A's messages.
    b_list = client.get("/api/conversations", headers=headers_b).json()
    assert all(c["id"] != conv_id for c in b_list)
    assert (
        client.get(
            f"/api/conversations/{conv_id}/messages", headers=headers_b
        ).status_code
        == 404
    )


def test_conversation_delete_is_scoped_to_owner(client, auth_headers):
    """User B cannot delete user A's conversation (IDOR)."""
    headers_a = auth_headers()
    headers_b = auth_headers("bdel@test.com")

    conv = client.post(
        "/api/conversations", json={"title": "private"}, headers=headers_a
    )
    assert conv.status_code == 201
    conv_id = conv.json()["id"]

    # owner can delete
    assert (
        client.delete(f"/api/conversations/{conv_id}", headers=headers_a).status_code
        == 204
    )
    # second delete by the owner (now gone) -> 404
    assert (
        client.delete(f"/api/conversations/{conv_id}", headers=headers_a).status_code
        == 404
    )

    # a fresh conversation for the owner is still invisible to B
    conv2 = client.post(
        "/api/conversations", json={"title": "again"}, headers=headers_a
    ).json()
    assert (
        client.delete(
            f"/api/conversations/{conv2['id']}", headers=headers_b
        ).status_code
        == 404
    )


# --------------------------------------------------------------------------- #
# §40 / §16  document search must be SQL-injection safe                       #
# --------------------------------------------------------------------------- #
def test_document_search_injection_returns_empty_not_error(
    client, auth_headers, monkeypatch
):
    """A SQL-injection shaped query must be treated as a literal search string
    (parameterized) and return a clean result, never a 500 or a leakage error."""
    payloads = [
        "' OR '1'='1",
        "x'; DROP TABLE documents; --",
        "' UNION SELECT password FROM users --",
    ]
    # search_files is async and uses SQLAlchemy bound parameters — injecting SQL
    # syntax must not execute or escape the user's scope. End-to-end through the
    # API: the endpoint must return 200 with a results list, regardless of the
    # injection payload.
    headers = auth_headers()
    for payload in payloads:
        resp = client.get(
            "/api/documents/search",
            params={"q": payload},
            headers=headers,
        )
        assert resp.status_code == 200, f"{payload}: {resp.status_code} {resp.text}"
        assert isinstance(resp.json()["results"], list)

    # empty/blank query is rejected by the endpoint, not forwarded
    assert client.get(
        "/api/documents/search", params={"q": ""}, headers=headers
    ).status_code == 400


# --------------------------------------------------------------------------- #
# 1.12 / 1.13  Ollama unavailable / model failure recovery path               #
# --------------------------------------------------------------------------- #
def test_model_invocation_failure_returns_structured_error(client, auth_headers):
    """When the configured provider cannot be reached, the turn must surface a
    structured, non-leaky error (HTTP 4xx/5y) instead of an HTTP 500 crash or
    raw exception text in the body."""
    from app.api.deps import get_router
    from app.main import app
    from app.services.model_router import ProviderSpec, TaskRoute

    class OfflineRouter:
        """Routes to a provider whose model layer raises a connection error."""

        def route(self, **kwargs) -> TaskRoute:
            spec = ProviderSpec(
                "ollama", "openai", "http://localhost:11434/v1", None,
                "qwen2.5:7b", "qwen2.5:7b", requires_key=False,
            )
            return TaskRoute(provider=spec, model="qwen2.5:7b", reason="offline")

        def get_model(self, route: TaskRoute):
            raise httpx.ConnectError("Connection refused")

        def route_chain(self, **kwargs):
            return []  # no cross-provider fallback in this test

        def is_configured(self, name: str) -> bool:
            return True

    headers = auth_headers()
    app.dependency_overrides[get_router] = lambda: OfflineRouter()
    try:
        resp = client.post(
            "/api/chat", json={"message": "hi"}, headers=headers
        )
    finally:
        app.dependency_overrides.pop(get_router, None)

    # Must NOT be a raw 500; must be a structured Jalora error.
    assert resp.status_code in (429, 502, 503), resp.text
    body = resp.json()
    assert body.get("success") is False
    err = body.get("error", {})
    assert err.get("code")
    assert err.get("details")
    # The original exception text / traceback must never reach the client.
    assert "Connection refused" not in resp.text
    assert "Traceback" not in resp.text
    assert "ConnectError" not in resp.text
