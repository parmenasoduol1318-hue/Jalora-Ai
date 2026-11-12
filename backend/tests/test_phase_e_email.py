"""P8: Phase E — email/Gmail (§34–38, §60, §76).

Covers: tool registration & permissions, the deterministic email_mock provider
(CRUD + search/read/draft/send), Gmail OAuth state CSRF with encrypted token
storage, send gated by approval (API-level and in-graph), the auto-reply rule
matrix (match / no-match / frequency-cap), and inbound -> summary storage.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import ssl
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.api.deps import get_router
from app.config import get_settings
from app.context import ToolContext, set_tool_context
from app.database.session import SessionLocal
from app.main import app
from app.models.agent import ActivityLog, AgentRun, Approval
from app.models.conversation import Conversation, Message
from app.models.integration import Integration
from app.models.task import Task, TaskStep
from app.models.user import User
from app.services import email as email_service
from app.services import email_inbound
from app.services.credential_store import fernet_decrypt
from app.tools import registry
from app.tools.base import PermissionLevel


def _invoke(name: str, args: dict, user_id: str) -> dict:
    spec = registry.get(name)
    assert spec is not None, f"tool {name} is not registered"
    set_tool_context(ToolContext(user_id=user_id, run_id="test-run"))
    try:
        raw = asyncio.run(spec.tool.ainvoke(args))
    finally:
        set_tool_context(None)
    return json.loads(raw)


@pytest.fixture(autouse=True)
def clean_mailboxes():
    email_service._MOCK.reset_all()
    yield
    email_service._MOCK.reset_all()


@pytest.fixture(autouse=True)
def deterministic_email_composition(monkeypatch):
    async def compose(recipient, request_text, *, prefs=None):
        return email_service.generate_email_draft(recipient, request_text)

    monkeypatch.setattr(
        email_service, "generate_email_draft_with_model", compose
    )


@pytest.fixture
def email_settings(monkeypatch):
    """Apply email env vars for one test, always restoring cached settings."""
    def _set(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _register_user(client, auth_headers, email: str = "user@test.com") -> dict:
    headers = auth_headers(email)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        user_id = user.id
    return {"headers": headers, "user_id": user_id}


def _set_autonomy(user_id: str, level: int) -> None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        prefs = dict(user.prefs or {})
        prefs["autonomy"] = level
        user.prefs = prefs
        db.commit()


def _set_tool_policy(user_id: str, tool_name: str, policy: str) -> None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        prefs = dict(user.prefs or {})
        tool_policies = dict(prefs.get("tool_policies") or {})
        tool_policies[tool_name] = policy
        prefs["tool_policies"] = tool_policies
        user.prefs = prefs
        db.commit()


def _create_mock_integration(client, headers, address: str = "") -> dict:
    resp = client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": address or get_settings().gmail_from},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _approve_action_response(client, headers, response) -> dict:
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["status"] == "paused_approval"
    approved = client.post(
        f"/api/approvals/{result['approval']['id']}/approve",
        headers=headers,
        json={},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    return result


def _create_rule(client, headers, **overrides) -> dict:
    body = {
        "name": "Bank auto-reply",
        "enabled": True,
        "sender_pattern": "kcbgroup",
        "topic_pattern": "application",
        "template": "Thank you {name}, we received {subject}.",
        "max_per_day": 1,
        "max_per_hour": 0,
        "requires_approval": False,
        "priority": 0,
    }
    body.update(overrides)
    resp = client.post("/api/emails/rules", headers=headers, json=body)
    _approve_action_response(client, headers, resp)
    listing = client.get("/api/emails/rules", headers=headers)
    assert listing.status_code == 200, listing.text
    rules = listing.json()
    created = next((rule for rule in rules if rule["name"] == body["name"]), None)
    assert created is not None
    return created


# --------------------------------------------------------------------------- #
# tools: registration + permissions                                            #
# --------------------------------------------------------------------------- #
def test_email_tools_registered_with_expected_permissions():
    expected = {
        "search_email": PermissionLevel.AUTO,
        "read_email": PermissionLevel.AUTO,
        "list_threads": PermissionLevel.AUTO,
        "compose_email": PermissionLevel.AUTO,
        "send_email": PermissionLevel.CONFIRM,
    }
    for name, level in expected.items():
        spec = registry.get(name)
        assert spec is not None, f"{name} missing"
        assert spec.permission == level
        assert spec.category == "email"
    manifest = {e["name"]: e for e in registry.manifest()}
    assert manifest["send_email"]["permission"] == 3
    assert manifest["search_email"]["permission"] == 1


# --------------------------------------------------------------------------- #
# integrations CRUD (mock)                                                     #
# --------------------------------------------------------------------------- #
def test_integration_crud(client, auth_headers):
    env = _register_user(client, auth_headers)

    created = client.post("/api/integrations", headers=env["headers"],
                          json={"provider": "email_mock"}).json()
    assert created["provider"] == "email_mock"
    assert created["status"] == "connected"
    assert created["config"]["email_address"] == get_settings().gmail_from

    # upsert on the same (user, provider)
    again = client.post("/api/integrations", headers=env["headers"],
                        json={"provider": "email_mock"}).json()
    assert again["id"] == created["id"]

    rows = client.get("/api/integrations", headers=env["headers"]).json()
    assert len(rows) == 1
    # public config must never leak secret-ish keys
    assert "token" not in json.dumps(rows)

    patched = client.patch(f"/api/integrations/{created['id']}",
                           headers=env["headers"],
                           json={"status": "disabled"}).json()
    assert patched["status"] == "disabled"

    tested = client.post(f"/api/integrations/{created['id']}/test",
                         headers=env["headers"]).json()
    assert tested["ok"] is True
    assert tested["provider"] == "email_mock"

    client.delete(f"/api/integrations/{created['id']}", headers=env["headers"])
    assert client.get("/api/integrations", headers=env["headers"]).json() == []


def test_generic_gmail_integration_starts_disabled(client, auth_headers):
    env = _register_user(client, auth_headers)
    row = client.post("/api/integrations", headers=env["headers"],
                      json={"provider": "gmail"}).json()
    assert row["status"] == "disabled"
    assert client.post("/api/integrations", headers=env["headers"],
                       json={"provider": "carrier_pigeon"}).status_code == 400


def test_gmail_start_requires_credentials(client, auth_headers):
    env = _register_user(client, auth_headers)
    resp = client.post("/api/integrations/gmail/start", headers=env["headers"])
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


# --------------------------------------------------------------------------- #
# Gmail OAuth: state CSRF + encrypted token storage                            #
# --------------------------------------------------------------------------- #
def test_gmail_oauth_state_csrf_and_encrypted_tokens(
        client, auth_headers, email_settings, monkeypatch):
    email_settings(JALORA_GMAIL_CLIENT_ID="client_test_42",
                   JALORA_GMAIL_CLIENT_SECRET="secret_test_42")
    env = _register_user(client, auth_headers)

    start = client.post("/api/integrations/gmail/start", headers=env["headers"])
    assert start.status_code == 200, start.text
    url = start.json()["authorization_url"]
    state = re.search(r"[&?]state=([^&]+)", url).group(1)

    # tampered state -> rejected before any network call (CSRF)
    resp = client.get("/api/integrations/gmail/callback",
                      params={"code": "c", "state": state + "x"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "INVALID_STATE"

    # wrong/empty state also rejected
    assert client.get("/api/integrations/gmail/callback",
                      params={"code": "c", "state": "bogus"}).status_code == 403
    assert client.get("/api/integrations/gmail/callback",
                      params={"code": "c"}).status_code == 400

    # valid state + successful exchange -> tokens stored encrypted, plaintext
    # never persists and never appears on the wire
    async def _fake_tokens(data: dict) -> dict:
        return {"access_token": "plain-token-123", "refresh_token": "plain-refresh",
                "expires_in": 3600}

    monkeypatch.setattr(email_service, "_gmail_token_request", _fake_tokens)
    ok = client.get("/api/integrations/gmail/callback",
                    params={"code": "validcode", "state": state})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "connected"
    assert ok.json()["email_address"] == get_settings().gmail_from

    with SessionLocal() as db:
        row = db.scalar(select(Integration).where(
            Integration.provider == "gmail", Integration.user_id == env["user_id"]))
        assert row is not None
        raw_access = (row.config or {})["access_token"]
        assert "plain-token-123" not in raw_access
        assert fernet_decrypt(raw_access) == "plain-token-123"
        assert fernet_decrypt((row.config or {})["refresh_token"]) == "plain-refresh"

    # the public serializer never echoes encrypted blobs either
    exposed = client.get("/api/integrations", headers=env["headers"]).json()
    assert "plain-token-123" not in json.dumps(exposed)


# --------------------------------------------------------------------------- #
# Gmail OAuth: redirect URI derivation + browser vs API callback responses     #
# --------------------------------------------------------------------------- #
def test_gmail_redirect_uri_derived_from_public_base_url(email_settings):
    email_settings(JALORA_PUBLIC_BASE_URL="https://app.example.com",
                   JALORA_GMAIL_REDIRECT_URI="")
    assert get_settings().gmail_callback_url == \
        "https://app.example.com/api/integrations/gmail/callback"
    url = email_service.gmail_authorization_url("st")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Fapi%2Fintegrations%2Fgmail%2Fcallback" in url


def test_gmail_redirect_uri_explicit_override_wins(email_settings):
    email_settings(JALORA_PUBLIC_BASE_URL="https://app.example.com",
                   JALORA_GMAIL_REDIRECT_URI="https://x.example.com/cb")
    assert get_settings().gmail_callback_url == "https://x.example.com/cb"


def test_gmail_callback_redirects_browser_on_google_error(client, email_settings):
    email_settings(JALORA_GMAIL_CLIENT_ID="client_test_42",
                   JALORA_GMAIL_CLIENT_SECRET="secret_test_42",
                   JALORA_PUBLIC_BASE_URL="https://jalora-ai.onrender.com",
                   JALORA_FRONTEND_BASE_URL="https://jalora-ai.vercel.app")
    resp = client.get(
        "/api/integrations/gmail/callback",
        params={"error": "access_denied"},
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == \
        "https://jalora-ai.vercel.app/?gmail=error&reason=access_denied"

    # missing code/state also redirect browsers, while API clients get JSON
    for _ in range(2):
        api_resp = client.get(
            "/api/integrations/gmail/callback",
            params={"error": "access_denied"},
            headers={"Accept": "application/json"},
        )
        assert api_resp.status_code == 400
        assert api_resp.json()["error"]["code"] == "OAUTH_DENIED"


def test_gmail_callback_keeps_local_spa_redirect_when_frontend_url_is_unset(
        client, email_settings):
    email_settings(
        JALORA_PUBLIC_BASE_URL="http://localhost:5173",
        JALORA_FRONTEND_BASE_URL="",
    )
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"error": "access_denied"},
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "http://localhost:5173/?gmail=error&reason=access_denied"
    )


def test_gmail_callback_redirects_browser_on_success(
        client, auth_headers, email_settings, monkeypatch):
    email_settings(JALORA_GMAIL_CLIENT_ID="client_test_42",
                   JALORA_GMAIL_CLIENT_SECRET="secret_test_42",
                   JALORA_PUBLIC_BASE_URL="https://jalora-ai.onrender.com",
                   JALORA_FRONTEND_BASE_URL="https://jalora-ai.vercel.app")
    env = _register_user(client, auth_headers)

    start = client.post("/api/integrations/gmail/start", headers=env["headers"])
    state = re.search(r"[&?]state=([^&]+)", start.json()["authorization_url"]).group(1)

    async def _fake_tokens(data: dict) -> dict:
        return {"access_token": "plain-token-123", "refresh_token": "plain-refresh",
                "expires_in": 3600}

    monkeypatch.setattr(email_service, "_gmail_token_request", _fake_tokens)
    resp = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "validcode", "state": state},
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith(
        "https://jalora-ai.vercel.app/?gmail=connected&email="
    )
    assert "jaloraai1318%40gmail.com" in loc


def test_gmail_ssl_context_requires_verification(email_settings, monkeypatch):
    email_settings(JALORA_GMAIL_CLIENT_ID="client_test_42",
                   JALORA_GMAIL_CLIENT_SECRET="secret_test_42")
    context = email_service._gmail_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True

    # A configured bundle is passed to the standard verifying context rather
    # than changing verification to False or trusting arbitrary certificates.
    import certifi
    monkeypatch.setenv("JALORA_GMAIL_CA_BUNDLE", certifi.where())
    configured = email_service._gmail_ssl_context()
    assert configured.verify_mode == ssl.CERT_REQUIRED
    assert configured.check_hostname is True


def test_gmail_token_request_passes_tls_context_and_handles_transport_error(
        email_settings, monkeypatch):
    email_settings(JALORA_GMAIL_CLIENT_ID="client_test_42",
                   JALORA_GMAIL_CLIENT_SECRET="secret_test_42")
    captured = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return type("Response", (), {
                "status_code": 200,
                "json": lambda self: {"access_token": "access"},
            })()

    monkeypatch.setattr(email_service.httpx, "AsyncClient", _FakeClient)
    payload = asyncio.run(email_service._gmail_token_request({
        "code": "authorization-code", "grant_type": "authorization_code",
    }))
    assert payload == {"access_token": "access"}
    assert isinstance(captured["verify"], ssl.SSLContext)
    assert captured["verify"].verify_mode == ssl.CERT_REQUIRED
    assert captured["verify"].check_hostname is True
    assert captured["url"] == email_service.GMAIL_TOKEN_URL

    class _FailingClient(_FakeClient):
        async def post(self, url, **kwargs):
            raise email_service.httpx.ConnectError("certificate verify failed")

    monkeypatch.setattr(email_service.httpx, "AsyncClient", _FailingClient)
    with pytest.raises(email_service.EmailError, match="TLS configuration"):
        asyncio.run(email_service._gmail_token_request({
            "code": "authorization-code", "grant_type": "authorization_code",
        }))


# --------------------------------------------------------------------------- #
# mailbox: search / read / threads / draft (mock provider)                     #
# --------------------------------------------------------------------------- #
def test_mock_search_finds_bank_application_and_read(client, auth_headers):
    env = _register_user(client, auth_headers)
    email_service.seed_demo(env["user_id"])

    search = _invoke("search_email",
                     {"query": "bank application", "max_results": 10},
                     env["user_id"])
    assert search["success"] is True
    results = search["data"]["results"]
    ids = {r["id"] for r in results}
    assert "msg-bank-001" in ids and "msg-bank-002" in ids
    assert "msg-news-001" not in ids

    read = _invoke("read_email", {"email_id": "msg-bank-002"}, env["user_id"])
    assert read["success"] is True
    body = read["data"]["email"]
    assert "APPROVED" in body["body"]
    assert "kenyacommercialbank.example" in body["from_addr"]

    missing = _invoke("read_email", {"email_id": "nope"}, env["user_id"])
    assert missing["success"] is False
    assert missing["error"]["code"] == "EMAIL_NOT_FOUND"


def test_mock_threads_and_compose_never_sends(client, auth_headers):
    env = _register_user(client, auth_headers)
    email_service.seed_demo(env["user_id"])

    threads = _invoke("list_threads", {}, env["user_id"])
    assert threads["success"] is True
    assert len(threads["data"]["threads"]) == 4
    assert threads["data"]["provider"] == "email_mock"

    before = email_service._MOCK.sent_count(env["user_id"])
    draft = _invoke("compose_email",
                    {"to": "ada@example.com", "subject": "Hello",
                     "body": "Just drafting."}, env["user_id"])
    assert draft["success"] is True
    assert draft["data"]["status"] == "draft"
    assert email_service._MOCK.sent_count(env["user_id"]) == before
    assert len(email_service._MOCK.drafts.get(env["user_id"], [])) == 1

    # same guarantee through the REST API
    api_draft = client.post("/api/emails/compose", headers=env["headers"],
                            json={"to": "a@b.example", "subject": "API draft",
                                  "body": "draft body"})
    assert api_draft.status_code == 200, api_draft.text
    assert api_draft.json()["status"] == "draft"
    assert email_service._MOCK.sent_count(env["user_id"]) == before


def test_email_reply_assist_returns_editable_draft_without_saving_or_sending(
    client, auth_headers
):
    env = _register_user(client, auth_headers, "reply-draft@test.com")
    email_service.seed_demo(env["user_id"])

    class FakeRoute:
        label = "test:small"

    attempts = []

    class FakeModel:
        def model_copy(self, *, update):
            return self

        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, messages):
            attempts.append(1)
            assert "APPROVED" in messages[1].content
            assert "do not send" in messages[0].content.lower()
            if len(attempts) == 1:
                raise RuntimeError("temporary model outage")
            return AIMessage(content="Thank you for the update.")

    class FakeRouter:
        def route(self, **kwargs):
            assert kwargs["complexity"] == "simple"
            assert kwargs["prefer_private"] is True
            return FakeRoute()

        def get_model(self, _route):
            return FakeModel()

    app.dependency_overrides[get_router] = FakeRouter
    try:
        response = client.post(
            "/api/emails/msg-bank-002/reply-draft",
            headers=env["headers"],
            json={},
        )
    finally:
        app.dependency_overrides.pop(get_router, None)

    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["status"] == "editable_draft"
    assert draft["to"] == "no-reply@kenyacommercialbank.example"
    assert draft["subject"] == "Re: Your Loan Application KB-88122 has been Approved"
    assert draft["body"] == "Thank you for the update."
    assert draft["model"] == "test:small"
    with SessionLocal() as db:
        task = db.get(Task, draft["task_id"])
        assert task is not None
        assert task.user_id == env["user_id"]
        assert task.status == "completed"
        run = db.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
        assert run is not None and run.status == "completed"
        task_steps = db.scalars(
            select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.idx)
        ).all()
        assert len(task_steps) == 1
        assert task_steps[0].status == "done"
        result_data = task_steps[0].result_data["execution_result"]["output"]
        assert result_data["attempts_used"] == 2
    assert len(attempts) == 2
    assert email_service._MOCK.sent_count(env["user_id"]) == 0
    assert email_service._MOCK.drafts.get(env["user_id"], []) == []


def test_email_reply_draft_failure_is_checkpointed_after_one_retry(client, auth_headers):
    env = _register_user(client, auth_headers, "reply-draft-failure@test.com")
    email_service.seed_demo(env["user_id"])
    attempts = []

    class FakeRoute:
        label = "test:small"

    class FakeModel:
        def model_copy(self, *, update):
            return self

        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, _messages):
            attempts.append(1)
            return AIMessage(content="")

    class FakeRouter:
        def route(self, **_kwargs):
            return FakeRoute()

        def get_model(self, _route):
            return FakeModel()

    app.dependency_overrides[get_router] = FakeRouter
    try:
        response = client.post(
            "/api/emails/msg-bank-002/reply-draft",
            headers=env["headers"],
            json={},
        )
    finally:
        app.dependency_overrides.pop(get_router, None)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REPLY_GENERATION_FAILED"
    assert len(attempts) == 2
    with SessionLocal() as db:
        task = db.scalar(
            select(Task).where(Task.user_id == env["user_id"])
            .order_by(Task.created_at.desc())
        )
        assert task is not None
        assert task.status == "failed"
        run = db.scalar(select(AgentRun).where(AgentRun.task_id == task.id))
        assert run is not None and run.status == "error"
        task_steps = db.scalars(
            select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.idx)
        ).all()
        assert len(task_steps) == 1
        assert task_steps[0].status == "failed"
    assert email_service._MOCK.sent_count(env["user_id"]) == 0


def test_batch_compose_creates_one_distinct_draft_per_recipient(
    client, auth_headers, monkeypatch
):
    env = _register_user(client, auth_headers)
    calls = []

    async def record_draft(**kwargs):
        calls.append(kwargs)
        return {
            "draft_id": f"draft-{len(calls)}",
            "provider": "email_mock",
        }

    monkeypatch.setattr(email_service, "compose_draft", record_draft)
    result = _invoke("compose_email_batch", {
        "emails": [
            {
                "to": "one@example.org",
                "subject": "Partnership with Vox Kenta",
                "body": "Hello One, introducing Vox Kenta.",
            },
            {
                "to": "two@example.org",
                "subject": "Legal review for Vox Kenta",
                "body": "Hello Two, may we discuss Vox Kenta?",
            },
        ],
    }, env["user_id"])

    assert result["success"] is True
    assert len(result["data"]["created"]) == 2
    assert [draft["to"] for draft in result["data"]["created"]] == [
        "one@example.org", "two@example.org",
    ]
    assert calls[0]["body"] != calls[1]["body"]
    assert email_service._MOCK.sent_count(env["user_id"]) == 0


def test_batch_compose_reports_partial_success_without_claiming_all_drafts(
    client, auth_headers, monkeypatch
):
    env = _register_user(client, auth_headers)

    async def fail_second(**kwargs):
        if kwargs["recipient"] == "two@example.org":
            raise email_service.EmailError("PROVIDER_NOT_CONFIGURED", "Draft failed.")
        return {"draft_id": "draft-one", "provider": "email_mock"}

    monkeypatch.setattr(email_service, "compose_draft", fail_second)
    result = _invoke("compose_email_batch", {
        "emails": [
            {"to": "one@example.org", "subject": "One", "body": "Message one."},
            {"to": "two@example.org", "subject": "Two", "body": "Message two."},
        ],
    }, env["user_id"])

    assert result["success"] is False
    assert result["error"]["code"] == "PARTIAL_EMAIL_BATCH"
    assert result["data"]["created"] == [{
        "to": "one@example.org",
        "subject": "One",
        "draft_id": "draft-one",
        "provider": "email_mock",
    }]
    assert result["data"]["failed"] == [{
        "to": "two@example.org",
        "error": "Draft failed.",
    }]
    assert email_service._MOCK.sent_count(env["user_id"]) == 0


# --------------------------------------------------------------------------- #
# send gated by approval: REST + in-graph                                      #
# --------------------------------------------------------------------------- #
def test_email_api_send_pauses_for_approval_then_sends(client, auth_headers):
    env = _register_user(client, auth_headers)
    _create_mock_integration(client, env["headers"])

    sent = client.post("/api/emails/send", headers=env["headers"], json={
        "to": "bo@example.com", "subject": "Hi", "body": "Approved body",
    })
    assert sent.status_code == 200, sent.text
    payload = sent.json()
    assert payload["status"] == "paused_approval"
    approval_id = payload["approval"]["id"]
    assert payload["approval"]["tool_name"] == "send_email"
    assert payload["approval"]["standalone"] is False
    assert payload["approval"]["task_id"]
    assert payload["approval"]["run_id"]
    assert payload["approval"]["conversation_id"]
    assert email_service._MOCK.sent_count(env["user_id"]) == 0

    # approval appears in the shared approvals list
    pending = client.get("/api/approvals", headers=env["headers"]).json()
    assert any(a["id"] == approval_id and a["status"] == "pending"
               for a in pending)
    shared = next(a for a in pending if a["id"] == approval_id)
    assert shared["task_id"] == payload["approval"]["task_id"]
    assert shared["run_id"] == payload["approval"]["run_id"]

    result = client.post(f"/api/emails/send/{approval_id}/approve",
                         headers=env["headers"])
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "sent"
    assert result.json()["sent"]["mock"] is True
    assert email_service._MOCK.sent_count(env["user_id"]) == 1

    # Replaying the same approval cannot invoke the provider a second time.
    duplicate = client.post(f"/api/emails/send/{approval_id}/approve",
                            headers=env["headers"])
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "INVALID_APPROVAL"
    assert email_service._MOCK.sent_count(env["user_id"]) == 1


def test_email_api_send_rejection_never_sends(client, auth_headers):
    env = _register_user(client, auth_headers)
    _create_mock_integration(client, env["headers"])

    paused = client.post("/api/emails/send", headers=env["headers"], json={
        "to": "bo@example.com", "subject": "Hold", "body": "don't",
    }).json()
    resp = client.post(f"/api/emails/send/{paused['approval']['id']}/reject",
                       headers=env["headers"])
    assert resp.json()["status"] == "rejected"
    assert email_service._MOCK.sent_count(env["user_id"]) == 0


def test_direct_email_approval_uses_shared_task_and_edit_lifecycle(
        client, auth_headers, monkeypatch):
    from app.database.session import SessionLocal
    from app.models.agent import AgentRun, Approval
    from app.models.task import Task, TaskStep

    env = _register_user(client, auth_headers, "canonical-email@test.com")
    _create_mock_integration(client, env["headers"])
    sent_arguments = {}
    original_send = email_service.send_email

    async def capture_send(**kwargs):
        sent_arguments.update(kwargs)
        return await original_send(**kwargs)

    monkeypatch.setattr(email_service, "send_email", capture_send)
    prepared = client.post("/api/emails/send", headers=env["headers"], json={
        "to": "review@example.com",
        "subject": "Original subject",
        "body": "Original body",
    })
    assert prepared.status_code == 200, prepared.text
    pending = prepared.json()["approval"]
    approval_id = pending["id"]

    task_detail = client.get(
        f"/api/tasks/{pending['task_id']}", headers=env["headers"],
    )
    assert task_detail.status_code == 200, task_detail.text
    assert task_detail.json()["run_id"] == pending["run_id"]
    assert task_detail.json()["status"] == "waiting_for_approval"

    edited_arguments = {
        **pending["arguments"],
        "subject": "Approved subject",
        "body": "Approved replacement body",
    }
    approval_record = next(
        row for row in client.get(
            "/api/approvals", headers=env["headers"],
        ).json()
        if row["id"] == approval_id
    )
    edited = client.patch(
        f"/api/approvals/{approval_id}",
        headers=env["headers"],
        json={
            "arguments": edited_arguments,
            "payload_version": approval_record["payload_version"],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["arguments"]["body"] == "Approved replacement body"
    assert email_service._MOCK.sent_count(env["user_id"]) == 0

    approved = client.post(
        f"/api/approvals/{approval_id}/approve", headers=env["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert sent_arguments["recipient"] == "review@example.com"
    assert sent_arguments["subject"] == "Approved subject"
    assert sent_arguments["body"] == "Approved replacement body"

    with SessionLocal() as db:
        task = db.get(Task, pending["task_id"])
        run = db.get(AgentRun, pending["run_id"])
        approval = db.get(Approval, approval_id)
        step = db.get(TaskStep, approval.task_step_id)
        assert task is not None and task.status == "completed"
        assert run is not None and run.status == "completed"
        assert approval is not None and approval.status == "approved"
        assert step is not None and step.status == "done"
        assert step.arguments["body"] == "Approved replacement body"


def test_canonical_email_reconciliation_updates_task_without_retry(
        client, auth_headers, monkeypatch):
    from app.models.agent import AgentRun
    from app.models.task import Task, TaskStep, TaskStepAttempt

    env = _register_user(client, auth_headers, "canonical-email-unknown@test.com")
    _create_mock_integration(client, env["headers"])
    send_calls = []

    async def uncertain_send(**kwargs):
        send_calls.append(kwargs)
        raise TimeoutError("provider response timed out")

    monkeypatch.setattr(email_service, "send_email", uncertain_send)
    prepared = client.post("/api/emails/send", headers=env["headers"], json={
        "to": "unknown@example.com",
        "subject": "Reconcile this",
        "body": "The provider may have accepted this message.",
    })
    assert prepared.status_code == 200, prepared.text
    pending = prepared.json()["approval"]
    approved = client.post(
        f"/api/emails/send/{pending['id']}/approve", headers=env["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "outcome_unknown"
    assert len(send_calls) == 1

    async def provider_reconcile(**kwargs):
        return {
            "status": "provider_confirmed",
            "matches": 1,
            "provider_receipt": {
                "provider": "gmail",
                "message_id": "reconciled-message-id",
                "to": "unknown@example.com",
                "subject": "Reconcile this",
                "status": "sent",
            },
        }

    monkeypatch.setattr(email_service, "reconcile_sent_email", provider_reconcile)
    reconciled = client.post(
        f"/api/emails/send/{pending['id']}/reconcile",
        headers=env["headers"],
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["status"] == "provider_confirmed"
    assert len(send_calls) == 1

    with SessionLocal() as db:
        task = db.get(Task, pending["task_id"])
        run = db.get(AgentRun, pending["run_id"])
        approval = db.get(Approval, pending["id"])
        step = db.get(TaskStep, approval.task_step_id)
        attempt = db.scalar(
            select(TaskStepAttempt)
            .where(TaskStepAttempt.step_id == step.id)
            .order_by(TaskStepAttempt.attempt_number.desc())
        )
        assert task is not None and task.status == "completed"
        assert run is not None and run.status == "completed"
        assert approval is not None and approval.status == "approved"
        assert step is not None
        assert step.outcome_status == "provider_accepted"
        assert step.verification_status == "pending"
        assert attempt is not None and attempt.state == "completed"
        assert attempt.outcome_status == "provider_accepted"


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_matches"),
    [
        (
            {
                "status": "provider_confirmed",
                "matches": 1,
                "provider_receipt": {
                    "provider": "gmail",
                    "message_id": "gmail-message-123",
                    "thread_id": "gmail-thread-123",
                },
            },
            "provider_confirmed",
            None,
        ),
        ({"status": "outcome_unknown", "matches": 0}, "outcome_unknown", 0),
        ({"status": "outcome_unknown", "matches": 2}, "outcome_unknown", 2),
    ],
)
def test_unknown_email_outcome_reconciliation_is_safe(
    client, auth_headers, monkeypatch, outcome, expected_status, expected_matches,
):
    env = _register_user(client, auth_headers)
    with SessionLocal() as db:
        approval = Approval(
            user_id=env["user_id"],
            run_id="",
            tool_name="send_email",
            arguments={
                "to": "recipient@example.org",
                "subject": "Reviewed subject",
                "body": "Reviewed message",
                "regards": "Regards",
            },
            status="outcome_unknown",
            decided_at=datetime.now(timezone.utc),
        )
        db.add(approval)
        db.commit()
        db.refresh(approval)
        approval_id = approval.id

    calls = []

    async def reconcile(**kwargs):
        calls.append(kwargs)
        return outcome

    monkeypatch.setattr(email_service, "reconcile_sent_email", reconcile)
    response = client.post(
        f"/api/emails/send/{approval_id}/reconcile",
        headers=env["headers"],
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == expected_status
    assert len(calls) == 1
    assert calls[0]["user_id"] == env["user_id"]
    assert calls[0]["recipient"] == "recipient@example.org"
    assert calls[0]["regards"] == "Regards"

    with SessionLocal() as db:
        saved = db.get(Approval, approval_id)
        assert saved is not None
        if expected_status == "provider_confirmed":
            assert saved.status == "approved"
            assert saved.result["provider_receipt"]["message_id"] == "gmail-message-123"
            assert result["provider_receipt"]["message_id"] == "gmail-message-123"
        else:
            assert saved.status == "outcome_unknown"
            assert result["matches"] == expected_matches
            assert result["retry_allowed"] is False


@pytest.mark.parametrize(
    ("message_offset_minutes", "expected_status"),
    [(0, "provider_confirmed"), (-11, "outcome_unknown"), (11, "outcome_unknown")],
)
def test_email_reconciliation_matches_only_recent_exact_gmail_message_without_retry(
    monkeypatch, message_offset_minutes, expected_status,
):
    from app.models.integration import Integration

    now = datetime.now(timezone.utc)
    body = "The exact reviewed email body.\n\nRegards"
    encoded_body = base64.urlsafe_b64encode(body.encode()).decode()
    calls = []

    async def gmail_request(self, method, path, *, json=None, params=None):
        calls.append((method, path, params))
        if path == "messages":
            return {"messages": [{"id": "message-7"}]}
        return {
            "id": "message-7",
            "threadId": "thread-7",
            "internalDate": str(
                int((now + timedelta(minutes=message_offset_minutes)).timestamp() * 1000)
            ),
            "payload": {
                "headers": [
                    {"name": "To", "value": "Recipient <person@example.org>"},
                    {"name": "Subject", "value": "Reviewed subject"},
                ],
                "body": {"data": encoded_body},
            },
        }

    monkeypatch.setattr(email_service, "resolve_provider", lambda *_args, **_kw: "gmail")
    monkeypatch.setattr(
        email_service,
        "get_integration",
        lambda *_args, **_kw: Integration(
            user_id="user-1",
            provider="gmail",
            status="connected",
            config={},
        ),
    )
    monkeypatch.setattr(email_service._Gmail, "_request", gmail_request)

    result = asyncio.run(email_service.reconcile_sent_email(
        user_id="user-1",
        recipient="person@example.org",
        subject="Reviewed subject",
        body="The exact reviewed email body.",
        regards="Regards",
        submitted_at=now,
    ))

    assert result["status"] == expected_status
    if expected_status == "provider_confirmed":
        assert result["provider_receipt"]["message_id"] == "message-7"
    else:
        assert result["matches"] == 0
    assert len(calls) == 2
    assert calls[0][0:2] == ("GET", "messages")
    assert "in:sent" in calls[0][2]["q"]
    assert calls[1][0:2] == ("GET", "messages/message-7")


def test_email_api_send_requires_approval_when_autonomy_3(client, auth_headers):
    env = _register_user(client, auth_headers)
    _set_autonomy(env["user_id"], 3)
    _create_mock_integration(client, env["headers"])

    resp = client.post("/api/emails/send", headers=env["headers"], json={
        "to": "direct@example.com", "subject": "Auto", "body": "go",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "paused_approval"
    assert resp.json()["approval"]["standalone"] is False
    assert resp.json()["approval"]["task_id"]
    assert resp.json()["approval"]["run_id"]
    assert email_service._MOCK.sent_count(env["user_id"]) == 0
    approval_id = resp.json()["approval"]["id"]
    approved = client.post(
        f"/api/emails/send/{approval_id}/approve", headers=env["headers"]
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "sent"
    assert email_service._MOCK.sent_count(env["user_id"]) == 1


def test_email_task_stays_reviewable_and_keeps_signoff_separate(
        client, auth_headers, monkeypatch):
    sent_arguments = {}
    original_send = email_service.send_email

    async def capture_send(**kwargs):
        sent_arguments.update(kwargs)
        return await original_send(**kwargs)

    monkeypatch.setattr(email_service, "send_email", capture_send)
    env = _register_user(client, auth_headers)
    _set_autonomy(env["user_id"], 3)
    _create_mock_integration(client, env["headers"])

    response = client.post("/api/emails/task", headers=env["headers"], json={
        "recipient": "junioroduol@gmail.com",
        "request": (
            "Send an email to junioroduol@gmail.com and tell him that im ready "
            "now for the showing of my current app capabilities"
        ),
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "paused_approval"
    assert payload["approval"]["standalone"] is False
    assert payload["approval"]["task_id"]
    assert payload["approval"]["run_id"]
    arguments = payload["approval"]["arguments"]
    assert arguments["to"] == "junioroduol@gmail.com"
    assert "capabilities" in arguments["subject"].lower()
    assert "ready" in arguments["body"].lower()
    assert "please fill out the approval form" not in arguments["body"].lower()
    assert arguments["regards"] == "Regards,"
    assert email_service._MOCK.sent_count(env["user_id"]) == 0

    approval_id = payload["approval"]["id"]
    updated = client.patch(
        f"/api/emails/send/{approval_id}",
        headers=env["headers"],
        json={
            **arguments,
            "subject": "Ready to demonstrate my app",
            "body": "I'm ready to show you my current app capabilities.",
            "regards": "Regards,\nParmenas",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["arguments"]["regards"] == "Regards,\nParmenas"
    assert email_service._MOCK.sent_count(env["user_id"]) == 0
    approved = client.post(
        f"/api/emails/send/{approval_id}/approve", headers=env["headers"]
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "sent"
    assert sent_arguments["body"] == (
        "I'm ready to show you my current app capabilities.\n\nRegards,\nParmenas"
    )
    assert email_service._MOCK.sent_count(env["user_id"]) == 1


def test_email_task_generates_a_friendly_check_in_before_approval(client, auth_headers):
    env = _register_user(client, auth_headers, "friendly-check-in@test.com")
    response = client.post("/api/emails/task", headers=env["headers"], json={
        "recipient": "parmenasoduol1318@gmail.com",
        "request": (
            "Send an email to parmenasoduol1318@gmail.com to ask him how he is doing"
        ),
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "paused_approval"
    assert payload["draft"]["to"] == "parmenasoduol1318@gmail.com"
    assert payload["draft"]["subject"] == "How Are You"
    assert payload["draft"]["body"] == (
        "Hi Parmenasoduol,\n\nI hope you're doing well. I wanted to check in and "
        "see how you are doing."
    )
    assert payload["approval"]["arguments"]["body"] == payload["draft"]["body"]
    assert email_service._MOCK.sent_count(env["user_id"]) == 0


def test_email_task_preserves_and_sends_multiple_to_cc_and_bcc_recipients(
        client, auth_headers, monkeypatch):
    env = _register_user(client, auth_headers, "multiple-recipients@test.com")
    _create_mock_integration(client, env["headers"])

    async def model_draft(recipient, request_text, *, prefs=None):
        draft = email_service.generate_email_draft(recipient, request_text)
        draft.update({
            "subject": "Ready to Share the Project Demo",
            "body": "I am ready to show you the current project capabilities.",
            "regards": "Warm regards,",
            "generation_method": "model",
            "valid": True,
        })
        return draft

    monkeypatch.setattr(email_service, "generate_email_draft_with_model", model_draft)
    response = client.post("/api/emails/task", headers=env["headers"], json={
        "request": (
            "Send an email to first@example.com and second@example.com "
            "cc copy@example.com and bcc hidden@example.com and tell them I am ready."
        ),
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["draft"]["to"] == "first@example.com, second@example.com"
    assert payload["draft"]["cc"] == "copy@example.com"
    assert payload["draft"]["bcc"] == "hidden@example.com"
    assert payload["approval"]["generation_method"] == "model"
    assert email_service._MOCK.sent_count(env["user_id"]) == 0

    approval_id = payload["approval"]["id"]
    approved = client.post(
        f"/api/emails/send/{approval_id}/approve", headers=env["headers"]
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["sent"]["to"] == "first@example.com, second@example.com", approved.json()
    sent = email_service._MOCK.sent[env["user_id"]][-1]
    assert sent["cc"] == "copy@example.com"
    assert sent["bcc"] == "hidden@example.com"


def test_email_attachment_upload_skips_document_embedding_and_is_sendable(
        client, auth_headers, monkeypatch, tmp_path):
    env = _register_user(client, auth_headers, "email-attachment@test.com")
    _create_mock_integration(client, env["headers"])
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)

    uploaded = client.post(
        "/api/emails/attachments",
        headers=env["headers"],
        files={"file": ("project-notes.txt", b"Ready for the demo.", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    attachment = uploaded.json()
    assert attachment["filename"] == "project-notes.txt"
    assert (tmp_path / env["user_id"] / "email-attachments").is_dir()

    pending = client.post("/api/emails/send", headers=env["headers"], json={
        "to": "recipient@example.com",
        "subject": "Project notes",
        "body": "Please see the attachment.",
        "attachments": [attachment],
    })
    assert pending.status_code == 200, pending.text
    approved = client.post(
        f"/api/emails/send/{pending.json()['approval']['id']}/approve",
        headers=env["headers"],
    )
    assert approved.status_code == 200, approved.text
    sent_attachment = email_service._MOCK.sent[env["user_id"]][-1]["attachments"][0]
    assert sent_attachment["filename"] == "project-notes.txt"


def test_email_task_rejects_invalid_generated_draft_without_provider_call(
        client, auth_headers, monkeypatch):
    env = _register_user(client, auth_headers)
    _set_autonomy(env["user_id"], 3)
    _create_mock_integration(client, env["headers"])

    monkeypatch.setattr(
        email_service,
        "generate_email_draft",
        lambda recipient, request_text: {
            "to": recipient,
            "subject": "[Missing subject]",
            "body": "Hello [Recipient's Name]",
            "confidence": 0.1,
            "valid": False,
            "validation_errors": ["body contains unresolved placeholders."],
        },
    )
    response = client.post("/api/emails/task", headers=env["headers"], json={
        "recipient": "recipient@example.com",
        "request": "send the requested message",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "INVALID_DRAFT"
    assert email_service._MOCK.sent_count(env["user_id"]) == 0


def test_send_email_tool_pauses_for_approval_in_graph(
        client, fake_router, auth_headers):
    env = _register_user(client, auth_headers)
    _set_autonomy(env["user_id"], 3)
    _create_mock_integration(client, env["headers"])
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "send_email",
            "args": {"to": "boss@example.com", "subject": "Weekly report",
                     "body": "Here is this week's report."},
            "id": "call_email_1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Email sent to your boss."))

    resp = client.post("/api/chat",
                       json={"message": "Send the weekly report to my boss"},
                       headers=env["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "paused_approval"
    assert data["approval"]["tool_name"] == "send_email"
    result = client.post(
        f"/api/approvals/{data['approval']['id']}/approve",
        headers=env["headers"],
    )
    assert result.json()["status"] == "completed"
    assert email_service._MOCK.sent_count(env["user_id"]) == 1


def test_task_engine_email_requires_approval_even_at_full_autonomy():
    from app.services.task_engine import AgentSpec, EmailAgentHandler, TaskEngine

    engine = object.__new__(TaskEngine)
    spec = AgentSpec(
        name="email", action_type="email", permission=3,
        handler=EmailAgentHandler(),
    )
    preferences = {
        "autonomy": 4,
        "tool_policies": {"send_email": "automatic"},
    }
    assert TaskEngine._step_requires_approval(
        engine, spec, {"tool": "send_email"}, preferences
    )
    proposal = spec.handler.propose(
        user_id="user-id",
        request="Send an email to junior@example.com and say hello",
        recipient="junior@example.com",
    )
    assert proposal[1]["arguments"]["regards"] == "Regards,"


def test_user_email_sentence_sends_after_approval_in_graph(
        client, fake_router, auth_headers):
    """Regression for the exact sentence a user sent after connecting Gmail:
    'send email to X and tell him to attend meeting on friday at 8 am'.

    classify_intent routes it to 'scheduling', whose tool set includes
    send_email, so the agent must be able to call send_email, pause for
    approval, and dispatch after approval — not refuse with 'I can't send'.
    """
    env = _register_user(client, auth_headers)
    _create_mock_integration(client, env["headers"])

    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "send_email",
            "args": {"to": "internetmoney1318@gmail.com",
                     "subject": "Meeting on Friday",
                     "body": "Please attend the meeting on Friday at 8 am."},
            "id": "call_user_email_1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Noted, dispatching now."))

    resp = client.post("/api/chat", json={
        "message": "send email to internetmoney1318@gmail.com and tell him to "
                   "attend meeting on friday at 8 am",
    }, headers=env["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "paused_approval"
    assert data["approval"]["tool_name"] == "send_email"
    assert email_service._MOCK.sent_count(env["user_id"]) == 0

    result = client.post(
        f"/api/approvals/{data['approval']['id']}/approve",
        headers=env["headers"],
    )
    assert result.json()["status"] == "completed"
    assert email_service._MOCK.sent_count(env["user_id"]) == 1


def test_plain_email_request_rejects_send_application_tool(
    client, fake_router, auth_headers, caplog
):
    env = _register_user(client, auth_headers)
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "send_application",
            "args": {
                "draft_id": "my_draft_id",
                "recipient_email": "julio@julio.example",
            },
            "id": "wrong_application_tool",
            "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="I prepared a regular email draft for your review."
    ))
    response = client.post(
        "/api/chat",
        json={"message": (
            "Send an email to junioroduol@gmail.com and tell him I am ready "
            "to show my current app capabilities."
        )},
        headers=env["headers"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["approval"] is None
    assert response.json()["status"] != "paused_approval"
    assert (
        "Tool 'send_application' rejected because it was not selected"
        in caplog.text
    )


# --------------------------------------------------------------------------- #
# auto-reply rule matrix (§38): match / no-match / frequency-cap               #
# --------------------------------------------------------------------------- #
def test_auto_reply_rule_matrix(client, auth_headers):
    env = _register_user(client, auth_headers)
    _set_autonomy(env["user_id"], 3)
    _set_tool_policy(env["user_id"], "send_email", "automatic")
    _create_mock_integration(client, env["headers"])
    _create_rule(client, env["headers"])

    def inbound(body: dict) -> dict:
        resp = client.post("/api/integrations/gmail/inbound", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()

    # 1) rule matches -> shared approval is required before sending
    first = inbound({
        "to": get_settings().gmail_from, "from_addr": "no-reply@kcbgroup.example",
        "subject": "Loan Application KB-88122 update",
        "body": "We are still reviewing your application.",
        "message_id": "inb-1",
    })
    assert first["status"] == "received"
    assert first["matched_user"] is True
    assert first["auto_reply"] == "requires_approval"
    assert first["auto_reply_task_id"]
    assert first["auto_reply_approval_id"]
    assert email_service._MOCK.sent_count(env["user_id"]) == 0

    approved = client.post(
        f"/api/approvals/{first['auto_reply_approval_id']}/approve",
        headers=env["headers"],
        json={},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert email_service._MOCK.sent_count(env["user_id"]) == 1
    with SessionLocal() as db:
        task = db.get(Task, first["auto_reply_task_id"])
        run = db.scalar(select(AgentRun).where(
            AgentRun.task_id == first["auto_reply_task_id"]))
        step = db.scalar(select(TaskStep).where(
            TaskStep.task_id == first["auto_reply_task_id"],
            TaskStep.tool_id == "send_email"))
        assert task is not None and task.status == "completed"
        assert run is not None and run.status == "completed"
        assert step is not None and step.status == "done"

    duplicate = inbound({
        "to": get_settings().gmail_from, "from_addr": "no-reply@kcbgroup.example",
        "subject": "Loan Application KB-88122 update",
        "body": "We are still reviewing your application.",
        "message_id": "inb-1",
    })
    assert duplicate["status"] == "duplicate"
    assert email_service._MOCK.sent_count(env["user_id"]) == 1

    # 2) sent reply consumed the configured daily cap
    second = inbound({
        "to": get_settings().gmail_from, "from_addr": "no-reply@kcbgroup.example",
        "subject": "Your application #2",
        "body": "Another status update about your application.",
        "message_id": "inb-2",
    })
    assert second["auto_reply"] == "rate_limited"
    assert email_service._MOCK.sent_count(env["user_id"]) == 1

    # 3) no matching rule (sender outside pattern) -> no decision
    third = inbound({
        "to": get_settings().gmail_from, "from_addr": "news@daily.example",
        "subject": "Weekly promo",
        "body": "Check out this week's offers.",
        "message_id": "inb-3",
    })
    assert third["auto_reply"] == "no_rule"
    assert email_service._MOCK.sent_count(env["user_id"]) == 1

    # summary + message storage for every received inbound
    with SessionLocal() as db:
        conv = db.scalar(select(Conversation).where(
            Conversation.title == email_inbound.INBOX_TITLE))
        assert conv is not None
        msgs = db.scalars(
            select(Message).where(Message.conversation_id == conv.id)
        ).all()
        assert len(msgs) == 3
        assert "application" in msgs[0].meta["classification"]
        inbound_logs = db.scalars(select(ActivityLog).where(
            ActivityLog.tool == "email_inbound")).all()
        assert len(inbound_logs) == 3
        replies = db.scalars(select(ActivityLog).where(
            ActivityLog.tool == "auto_reply")).all()
        assert len(replies) == 1


def test_auto_reply_rule_requires_approval_holds(client, auth_headers):
    env = _register_user(client, auth_headers)
    _set_autonomy(env["user_id"], 3)
    _set_tool_policy(env["user_id"], "send_email", "automatic")
    _create_mock_integration(client, env["headers"])
    _create_rule(client, env["headers"], requires_approval=True,
                 max_per_day=0)

    resp = client.post("/api/integrations/gmail/inbound", json={
        "to": get_settings().gmail_from, "from_addr": "no-reply@kcbgroup.example",
        "subject": "Application update",
        "body": "Your application progressed.",
        "message_id": "inb-hold",
    })
    result = resp.json()
    assert result["auto_reply"] == "requires_approval"
    assert result["auto_reply_task_id"]
    assert result["auto_reply_approval_id"]
    assert email_service._MOCK.sent_count(env["user_id"]) == 0
    with SessionLocal() as db:
        approval = db.get(Approval, result["auto_reply_approval_id"])
        task = db.get(Task, result["auto_reply_task_id"])
        run = db.scalar(select(AgentRun).where(
            AgentRun.task_id == result["auto_reply_task_id"]))
        audit = db.scalar(select(ActivityLog).where(
            ActivityLog.user_id == env["user_id"],
            ActivityLog.tool == "auto_reply",
        ).order_by(ActivityLog.created_at.desc()))
        assert approval is not None and approval.status == "pending"
        assert approval.task_id == result["auto_reply_task_id"]
        assert audit is not None
        assert audit.meta["auto_reply_rule_id"]
        assert audit.meta["inbound_message_id"] == "inb-hold"
        assert task is not None
        assert run is not None


def test_auto_reply_rules_crud(client, auth_headers):
    env = _register_user(client, auth_headers)
    _create_mock_integration(client, env["headers"])

    create_response = client.post("/api/emails/rules", headers=env["headers"], json={
        "name": "Out of office", "sender_pattern": "any",
        "template": "Thanks {name}.",
    })
    assert create_response.status_code == 202
    assert client.get(
        "/api/emails/rules", headers=env["headers"]
    ).json() == []
    _approve_action_response(client, env["headers"], create_response)

    listing = client.get("/api/emails/rules", headers=env["headers"]).json()
    assert len(listing) == 1
    assert listing[0]["name"] == "Out of office"
    rule_id = listing[0]["id"]

    toggle_response = client.patch(
        f"/api/emails/rules/{rule_id}",
        headers=env["headers"],
        json={"enabled": False},
    )
    assert client.get(
        "/api/emails/rules", headers=env["headers"]
    ).json()[0]["enabled"] is True
    _approve_action_response(client, env["headers"], toggle_response)
    toggled = client.get("/api/emails/rules", headers=env["headers"]).json()
    assert toggled[0]["enabled"] is False

    delete_response = client.delete(
        f"/api/emails/rules/{rule_id}", headers=env["headers"]
    )
    assert len(client.get(
        "/api/emails/rules", headers=env["headers"]
    ).json()) == 1
    _approve_action_response(client, env["headers"], delete_response)
    assert client.get("/api/emails/rules", headers=env["headers"]).json() == []


# --------------------------------------------------------------------------- #
# inbound webhook: signature + summary stored                                  #
# --------------------------------------------------------------------------- #
def test_inbound_webhook_signature_enforced_when_configured(
        client, auth_headers, email_settings):
    email_settings(JALORA_GMAIL_PUSH_TOKEN="push_token_abc_77")
    env = _register_user(client, auth_headers)
    _create_mock_integration(client, env["headers"])

    body = {
        "to": get_settings().gmail_from, "from_addr": "no-reply@kcbgroup.example",
        "subject": "Signed app update", "body": "Application status update.",
        "message_id": "inb-signed",
    }
    # missing token -> rejected before processing
    resp = client.post("/api/integrations/gmail/inbound", json=body)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"

    # correct token -> processed
    ok = client.post("/api/integrations/gmail/inbound?token=push_token_abc_77",
                     json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["auto_reply"] == "no_rule"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Message.id))) == 1


def test_inbound_summary_stored_and_duplicates_skipped(
        client, auth_headers):
    env = _register_user(client, auth_headers)
    _create_mock_integration(client, env["headers"])

    payload = {
        "to": get_settings().gmail_from, "from_addr": "careers@acme.example",
        "subject": "Interview invitation",
        "body": "We invite you to a technical interview next week.",
        "message_id": "inb-careers-1",
    }
    first = client.post("/api/integrations/gmail/inbound", json=payload).json()
    assert first["status"] == "received"
    # summary fallback is deterministic (no model configured here)
    assert "technical interview" in first["summary"]

    dup = client.post("/api/integrations/gmail/inbound", json=payload).json()
    assert dup["status"] == "duplicate"

    with SessionLocal() as db:
        assert db.scalar(select(func.count(Message.id))) == 1


# --------------------------------------------------------------------------- #
# acceptance: "find emails from my bank about my application and summarize"    #
# --------------------------------------------------------------------------- #
def test_acceptance_bank_application_summary(client, fake_router, auth_headers):
    env = _register_user(client, auth_headers)
    email_service.seed_demo(env["user_id"])
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [
                {
                    "id": "find-messages",
                    "description": "Find matching bank application emails",
                    "tool": "search_email",
                    "arguments": {"query": "bank application"},
                },
                {
                    "id": "read-message",
                    "description": "Read a matching message and summarize it",
                    "tool": "read_email",
                    "arguments": {"email_id": "msg-bank-002"},
                    "dependencies": ["find-messages"],
                },
            ]},
            "id": "call_plan",
            "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "search_email",
            "args": {"query": "bank application"},
            "id": "call_s1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "read_email",
            "args": {"email_id": "msg-bank-002"},
            "id": "call_s2", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="From your bank: your loan application KB-88122 was APPROVED "
                "and will be disbursed within 24 hours."))

    resp = client.post("/api/chat",
                       json={"message": "Find emails from my bank about my "
                                        "application and summarise them"},
                       headers=env["headers"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "completed"
    assert "APPROVED" in data["reply"]
    assert "KB-88122" in data["reply"]
    # read-only tools never paused the run
    assert data["approval"] is None
