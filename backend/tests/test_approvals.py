"""Approval / human-in-the-loop flow for Level 3 tools (spec §24)."""
import json
import os
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from app.errors import ToolResult
from app.database.session import SessionLocal
from app.models.agent import ActivityLog, Approval
from app.models.task import Task, TaskStep, TaskStepAttempt
from app.services import chat_service, email as email_service


def _headers(client, email="appr@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Approver",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_level3_tool_pauses_then_approves_and_executes(client, fake_router, confirm_calls):
    headers = _headers(client)
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "ping_external", "args": {"message": "hello world"},
            "id": "call_pause_1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Message delivered successfully."))

    resp = client.post("/api/chat", json={"message": "Ping the external system"},
                       headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "paused_approval"
    approval = data["approval"]
    assert approval is not None
    assert approval["tool_name"] == "ping_external"
    assert approval["arguments"]["message"] == "hello world"
    assert approval["level"] == 3

    with SessionLocal() as db:
        approval_row = db.get(Approval, approval["id"])
        assert approval_row is not None
        assert approval_row.task_id == data["task_id"]
        assert approval_row.task_step_id
        step_id = approval_row.task_step_id
        step = db.get(TaskStep, step_id)
        assert step is not None
        assert step.outcome_status == "awaiting_approval"
        assert step.approval_id == approval["id"]

    # nothing executed yet — no silent high-risk actions
    assert confirm_calls == []

    pending = client.get("/api/approvals", headers=headers).json()
    assert len(pending) == 1 and pending[0]["status"] == "pending"
    not_skippable = client.post(
        f"/api/approvals/{approval['id']}/skip", headers=headers
    )
    assert not_skippable.status_code == 400
    assert client.get("/api/approvals", headers=headers).json()[0]["status"] == "pending"

    # approve -> run resumes, tool executes, agent finishes
    result = client.post(f"/api/approvals/{approval['id']}/approve", headers=headers)
    assert result.status_code == 200, result.text
    final = result.json()
    assert final["status"] == "completed"
    assert final["reply"] == "Message delivered successfully."
    assert confirm_calls == [{"message": "hello world"}]

    # approvals list reflects the decision
    after = client.get("/api/approvals", headers=headers).json()
    assert after[0]["status"] == "approved"
    with SessionLocal() as db:
        approval_row = db.get(Approval, approval["id"])
        task = db.get(Task, data["task_id"])
        step = db.get(TaskStep, step_id)
        assert approval_row is not None
        assert approval_row.decision_status == "approved"
        assert approval_row.claim_status == "finished"
        assert task is not None and task.status == "completed"
        assert step is not None and step.outcome_status == "succeeded"
        attempt = db.query(TaskStepAttempt).filter_by(step_id=step_id).one()
        assert attempt.state == "completed"
        assert attempt.provider_idempotency_key
        assert step.result_data["execution_result"]["approval_id"] == approval["id"]
        audit = db.query(ActivityLog).filter_by(attempt_id=attempt.id).one()
        assert audit.task_id == data["task_id"]
        assert audit.task_step_id == step_id
        assert audit.action_id == step.action_id
        assert audit.outcome_status == "succeeded"

    # activity feed contains both the approval request and the tool execution
    activity = client.get("/api/activity", headers=headers).json()
    tools_seen = {a["tool"] for a in activity}
    assert "ping_external" in tools_seen
    assert any(a["tool"] == "ping_external" and a["success"] for a in activity)


def test_level3_tool_rejection_skips_execution(client, fake_router, confirm_calls):
    headers = _headers(client, "reject@test.com")
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "ping_external", "args": {"message": "do not send"},
            "id": "call_reject_1", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Understood — nothing was sent."))

    data = client.post("/api/chat", json={"message": "Ping"},
                       headers=headers).json()
    assert data["status"] == "paused_approval"

    result = client.post(f"/api/approvals/{data['approval']['id']}/reject",
                         headers=headers)
    assert result.status_code == 200
    assert result.json()["status"] == "completed"
    assert confirm_calls == []  # never executed

    activity = client.get("/api/activity", headers=headers).json()
    decision = next(a for a in activity if a["agent"] == "approval")
    assert decision["approval_status"] == "rejected"


def test_structured_user_input_prompt_recovers_when_run_state_is_missing(
    client, fake_router
):
    headers = _headers(client, "structured-prompt@test.com")
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "request_user_input",
            "args": {
                "title": "Deployment target",
                "question": "Which environment should I use?",
                "input_type": "choice",
                "options": ["Test", "Production"],
                "required": True,
            },
            "id": "request-deployment-target",
            "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="The task continued with Test."))

    response = client.post(
        "/api/chat",
        json={"message": "Deploy the application.", "mode": "agent"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "paused_approval"
    approval = data["approval"]
    assert approval["tool_name"] == "request_user_input"
    assert approval["arguments"]["input_type"] == "choice"

    from app.database.session import SessionLocal
    from app.models.agent import AgentRun
    from app.models.conversation import Message

    with SessionLocal() as db:
        run = db.get(AgentRun, data["run_id"])
        assert run is not None
        run.state_json = None
        message = db.query(Message).filter(
            Message.conversation_id == run.conversation_id,
            Message.meta["run_id"].as_string() == run.id,
        ).filter(Message.tool_calls.is_not(None)).first()
        assert message is not None
        original_meta = dict(message.meta)
        serialized = dict(original_meta["lc"])
        serialized_data = dict(serialized["data"])
        serialized_calls = [
            dict(call) for call in serialized_data.get("tool_calls", [])
        ]
        assert serialized_calls
        serialized_calls[0]["name"] = "send_email"
        serialized_data["tool_calls"] = serialized_calls
        serialized["data"] = serialized_data
        message.meta = {**original_meta, "lc": serialized}
        db.commit()

    mismatched_recovery = client.patch(
        f"/api/approvals/{approval['id']}",
        json={"arguments": {
            "title": "Deployment target",
            "question": "Which environment should I use?",
            "input_type": "choice",
            "options": ["Test", "Production"],
            "required": True,
            "answer": "Test",
        }},
        headers=headers,
    )
    assert mismatched_recovery.status_code == 400
    assert mismatched_recovery.json()["error"]["code"] == "RUN_RESUMABLE"

    with SessionLocal() as db:
        run = db.get(AgentRun, data["run_id"])
        assert run is not None
        message = db.query(Message).filter(
            Message.conversation_id == run.conversation_id,
            Message.meta["run_id"].as_string() == run.id,
        ).filter(Message.tool_calls.is_not(None)).first()
        assert message is not None
        repaired_meta = dict(message.meta)
        repaired_lc = dict(repaired_meta["lc"])
        repaired_data = dict(repaired_lc["data"])
        repaired_calls = [
            dict(call) for call in repaired_data.get("tool_calls", [])
        ]
        repaired_calls[0]["name"] = "request_user_input"
        repaired_data["tool_calls"] = repaired_calls
        repaired_lc["data"] = repaired_data
        repaired_meta["lc"] = repaired_lc
        message.meta = repaired_meta
        db.commit()

    updated = client.patch(
        f"/api/approvals/{approval['id']}",
        json={"arguments": {
            "title": "Deployment target",
            "question": "Which environment should I use?",
            "input_type": "choice",
            "options": ["Test", "Production"],
            "required": True,
            "answer": "Test",
        }},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["arguments"]["answer"] == "Test"

    with SessionLocal() as db:
        run = db.get(AgentRun, data["run_id"])
        assert run is not None
        run.state_json = None
        db.commit()

    resumed = client.post(
        f"/api/approvals/{approval['id']}/approve",
        headers=headers,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "completed"
    assert resumed.json()["reply"] == "The task continued with Test."


def test_double_decision_rejected(client, fake_router):
    headers = _headers(client, "double@test.com")
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "ping_external", "args": {"message": "once"},
            "id": "call_once", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="done"))

    data = client.post("/api/chat", json={"message": "Ping"}, headers=headers).json()
    approval_id = data["approval"]["id"]

    assert client.post(f"/api/approvals/{approval_id}/approve", headers=headers).status_code == 200
    second = client.post(f"/api/approvals/{approval_id}/approve", headers=headers)
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "APPROVAL_DECIDED"


def test_stale_concurrent_approval_claim_cannot_dispatch_twice(
    client, fake_router
):
    headers = _headers(client, "concurrent-approval@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "ping_external",
        "args": {"message": "claim once"},
        "id": "concurrent-claim",
        "type": "tool_call",
    }]))
    data = client.post(
        "/api/chat", json={"message": "Ping"}, headers=headers
    ).json()

    from app.database.session import SessionLocal
    from app.errors import BadRequestError
    from app.models.agent import Approval
    from app.services.approval_lifecycle import claim_approval

    first_session = SessionLocal()
    stale_session = SessionLocal()
    try:
        first = first_session.get(Approval, data["approval"]["id"])
        stale = stale_session.get(Approval, data["approval"]["id"])
        assert first is not None and stale is not None
        actor_id = first.user_id
        stale_session.expunge(stale)
        stale_session.close()

        claim_approval(
            first_session,
            first,
            actor_id=actor_id,
            decision="approved",
        )
        first_session.commit()

        retry_session = SessionLocal()
        try:
            retry_session.add(stale)
            with pytest.raises(BadRequestError) as error:
                claim_approval(
                    retry_session,
                    stale,
                    actor_id=actor_id,
                    decision="approved",
                )
            assert error.value.code == "APPROVAL_DECIDED"
        finally:
            retry_session.rollback()
            retry_session.close()
    finally:
        first_session.close()


def test_policy_is_rechecked_before_approval_claim(client, fake_router, confirm_calls):
    headers = _headers(client, "approval-policy-recheck@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "ping_external",
        "args": {"message": "policy changed"},
        "id": "policy-recheck",
        "type": "tool_call",
    }]))
    data = client.post(
        "/api/chat", json={"message": "Ping"}, headers=headers
    ).json()
    approval_id = data["approval"]["id"]

    changed = client.patch(
        "/api/settings",
        json={"tool_policies": {"ping_external": "denied"}},
        headers=headers,
    )
    assert changed.status_code == 200

    response = client.post(
        f"/api/approvals/{approval_id}/approve", headers=headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TOOL_DENIED"
    assert client.get("/api/approvals", headers=headers).json()[0]["status"] == "pending"
    assert confirm_calls == []


def test_revoking_pending_approval_is_durable_and_does_not_dispatch(
    client, fake_router, confirm_calls
):
    headers = _headers(client, "revoke-approval@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "ping_external",
        "args": {"message": "must not run"},
        "id": "revoke-call",
        "type": "tool_call",
    }]))
    data = client.post(
        "/api/chat", json={"message": "Ping"}, headers=headers
    ).json()

    response = client.post(
        f"/api/approvals/{data['approval']['id']}/revoke",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert confirm_calls == []

    from app.database.session import SessionLocal
    from app.models.agent import Approval
    from app.models.task import TaskStep

    with SessionLocal() as db:
        approval = db.get(Approval, data["approval"]["id"])
        assert approval is not None
        assert approval.status == "revoked"
        assert approval.decision_status == "revoked"
        assert approval.revoked_at is not None
        step = db.get(TaskStep, approval.task_step_id)
        assert step is not None and step.outcome_status == "revoked"


def test_expired_approval_fails_paused_run_and_task(client, fake_router):
    headers = _headers(client, "expired-approval@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "ping_external",
        "args": {"message": "expired"},
        "id": "expired-call",
        "type": "tool_call",
    }]))
    data = client.post(
        "/api/chat", json={"message": "Ping"}, headers=headers
    ).json()

    from datetime import timedelta
    from app.database.session import SessionLocal
    from app.models.agent import AgentRun, Approval
    from app.models.task import Task, TaskStep
    from app.models.user import utcnow

    with SessionLocal() as db:
        approval = db.get(Approval, data["approval"]["id"])
        assert approval is not None
        approval.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    rows = client.get("/api/approvals", headers=headers)
    assert rows.status_code == 200
    assert rows.json()[0]["status"] == "expired"
    with SessionLocal() as db:
        approval = db.get(Approval, data["approval"]["id"])
        run = db.get(AgentRun, data["run_id"])
        task = db.get(Task, data["task_id"])
        assert approval is not None and approval.decision_status == "expired"
        assert run is not None and run.status == "failed"
        assert task is not None and task.status == "failed"
        step = db.get(TaskStep, approval.task_step_id)
        assert step is not None and step.error_code == "APPROVAL_EXPIRED"


def test_multi_recipient_email_batch_is_reviewed_before_any_send(
    client, fake_router, monkeypatch
):
    """All personalized emails must be reviewed before one batch approval."""
    from conftest import FakeModel

    headers = _headers(client, "multi-email-approval@test.com")
    settings = chat_service.get_settings()
    monkeypatch.setattr(
        chat_service,
        "get_settings",
        lambda: settings.model_copy(update={"agent_max_tool_calls": 16}),
    )
    integration = client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": "sender@example.com"},
    })
    assert integration.status_code == 200, integration.text

    recipients = [
        "ti-kenya@example.org",
        "code-for-africa@example.org",
        "katiba@example.org",
        "open-contracting@example.org",
        "kituo@example.org",
        "article19@example.org",
        "eacc@example.org",
        "gif@example.org",
    ]
    send_steps = [
        {
            "id": f"send_{index}",
            "description": f"Send tailored email to {recipient}",
            "tool": "send_email",
            "arguments": {"to": recipient},
            "dependencies": [],
        }
        for index, recipient in enumerate(recipients)
    ]
    send_calls = [{
        "name": "send_email",
        "args": {
            "to": recipient,
            "subject": f"Partnership proposal for {recipient}",
            "body": f"Recipient-specific partnership context for {recipient}.",
        },
        "id": f"send_call_{index}",
        "type": "tool_call",
    } for index, recipient in enumerate(recipients)]

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": send_steps + [{
            "id": "final_response",
            "description": "Confirm the send results",
            "kind": "response",
            "dependencies": [step["id"] for step in send_steps],
        }]},
        "id": "multi_email_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=send_calls))

    model_call_count = {"count": 0}

    class ModelUnavailableAfterDispatch(FakeModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            model_call_count["count"] += 1
            if model_call_count["count"] > 2:
                raise RuntimeError("model unavailable after dispatch")
            async for chunk in super()._astream(
                messages, stop, run_manager, **kwargs
            ):
                yield chunk

    model = ModelUnavailableAfterDispatch(
        script=fake_router.script,
        tape=fake_router.tape,
    )
    fake_router.model = model

    sent_to: list[str] = []
    sent_bodies: list[str] = []
    original_send = email_service.send_email

    async def capture_send(**kwargs):
        sent_to.append(kwargs["recipient"])
        sent_bodies.append(kwargs["body"])
        return await original_send(**kwargs)

    monkeypatch.setattr(email_service, "send_email", capture_send)
    response = client.post("/api/chat", headers=headers, json={
        "message": (
            "Send these eight individually tailored outreach emails to "
            + ", ".join(recipients)
            + "."
        ),
    })
    assert response.status_code == 200, response.text
    result = response.json()

    assert result["status"] == "paused_approval", result
    approval = result["approval"]
    assert approval["tool_name"] == "send_email_batch"
    assert [email["to"] for email in approval["arguments"]["emails"]] == recipients
    assert [
        email["subject"] for email in approval["arguments"]["emails"]
    ] == [f"Partnership proposal for {recipient}" for recipient in recipients]
    assert sent_to == []

    from app.database.session import SessionLocal
    from app.models.agent import AgentRun, Approval
    from app.models.conversation import Message

    with SessionLocal() as db:
        approval_row = db.get(Approval, approval["id"])
        assert approval_row is not None
        assert approval_row.plan_step_id
        run = db.get(AgentRun, result["run_id"])
        assert run is not None
        run_messages = db.query(Message).filter(
            Message.conversation_id == run.conversation_id,
            Message.meta["run_id"].as_string() == run.id,
        ).all()
        persisted_calls = [
            call
            for row in run_messages
            for call in ((row.meta or {}).get("lc") or {}).get("data", {}).get(
                "tool_calls", []
            )
            if call.get("id") == approval_row.arguments["call_id"]
        ]
        assert len(persisted_calls) == 1
        assert persisted_calls[0]["name"] == "send_email_batch"
        run.state_json = None
        db.commit()

    edited_emails = approval["arguments"]["emails"]
    edited_emails[0]["body"] = "Edited message reviewed by the user."
    edited = client.patch(
        f"/api/approvals/{approval['id']}",
        headers=headers,
        json={"arguments": {"emails": edited_emails}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["arguments"]["emails"][0]["body"] == (
        "Edited message reviewed by the user."
    )
    assert sent_to == []

    with SessionLocal() as db:
        run = db.get(AgentRun, result["run_id"])
        assert run is not None
        run.state_json = None
        db.commit()

    result_response = client.post(
        f"/api/approvals/{approval['id']}/approve", headers=headers
    )
    assert result_response.status_code == 200, result_response.text
    result = result_response.json()

    assert result["status"] == "completed", (
        result["reply"],
        result.get("plan"),
        sent_to,
        model_call_count["count"],
        [
            (item["tool"], item["success"], item["error_code"])
            for item in client.get("/api/activity", headers=headers).json()
        ],
    )
    assert sent_to == recipients
    assert sent_bodies[0] == "Edited message reviewed by the user."
    assert len(sent_bodies) == len(recipients)
    approvals = client.get("/api/approvals", headers=headers).json()
    email_approvals = [
        item for item in approvals if item["tool_name"] == "send_email_batch"
    ]
    assert len(email_approvals) == 1
    assert email_approvals[0]["status"] == "approved"
    assert model_call_count["count"] == 2


def test_rejecting_multi_recipient_email_batch_sends_nothing(
    client, fake_router, monkeypatch
):
    headers = _headers(client, "multi-email-rejection@test.com")
    sent_to: list[str] = []
    original_send = email_service.send_email

    async def capture_send(**kwargs):
        sent_to.append(kwargs["recipient"])
        return await original_send(**kwargs)

    monkeypatch.setattr(email_service, "send_email", capture_send)

    recipients = ["one@example.org", "two@example.org"]
    send_steps = [
        {
            "id": f"send_{index}",
            "description": f"Send tailored email to {recipient}",
            "tool": "send_email",
            "arguments": {"to": recipient},
            "dependencies": [],
        }
        for index, recipient in enumerate(recipients)
    ]
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": send_steps},
        "id": "multi_email_rejection_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
            "name": "send_email",
            "args": {
                "to": recipient,
                "subject": f"Partnership proposal for {recipient}",
                "body": f"Tailored partnership context for {recipient}.",
            },
            "id": f"rejection_send_{index}",
            "type": "tool_call",
        } for index, recipient in enumerate(recipients)]))

    response = client.post("/api/chat", headers=headers, json={
        "message": "Send separate emails to two recipients: "
                   + ", ".join(recipients),
    })
    assert response.status_code == 200, response.text
    approval = response.json()["approval"]
    assert approval["tool_name"] == "send_email_batch"
    assert len(approval["arguments"]["emails"]) == 2
    assert sent_to == []

    rejected = client.post(
        f"/api/approvals/{approval['id']}/reject", headers=headers
    )
    assert rejected.status_code == 200, rejected.text
    assert sent_to == []
    saved_approval = next(
        item for item in client.get("/api/approvals", headers=headers).json()
        if item["id"] == approval["id"]
    )
    assert saved_approval["status"] == "rejected"


def test_public_organization_outreach_is_approved_one_email_at_a_time(
    client, fake_router, monkeypatch
):
    headers = _headers(client, "sequential-org-outreach@test.com")
    integration = client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": "sender@example.com"},
    })
    assert integration.status_code == 200, integration.text

    from app.services import search_service

    async def fake_search(query: str, max_results: int):
        organizations = [
            ("Transparency International Kenya", "ti-kenya.example.org"),
            ("Code for Africa", "code-for-africa.example.org"),
            ("Katiba Institute", "katiba.example.org"),
            ("Open Contracting Partnership", "open-contracting.example.org"),
            ("Kituo Cha Sheria", "kituo.example.org"),
            ("ARTICLE 19 Eastern Africa", "article19.example.org"),
            ("Ethics and Anti-Corruption Commission", "eacc.example.org"),
            ("Global Innovation Fund", "gif.example.org"),
        ]
        return [
            search_service.SearchResult(
                title=f"{organization} contact",
                url=f"https://{domain}/contact",
                snippet=f"Contact {organization} at info@{domain}.",
            )
            for organization, domain in organizations
            if organization in query
        ]

    monkeypatch.setattr(search_service, "search", fake_search)

    recipients = [
        "info@ti-kenya.example.org",
        "info@code-for-africa.example.org",
        "info@katiba.example.org",
        "info@open-contracting.example.org",
        "info@kituo.example.org",
        "info@article19.example.org",
        "info@eacc.example.org",
        "info@gif.example.org",
    ]
    organizations = [
        "Transparency International Kenya",
        "Code for Africa",
        "Katiba Institute",
        "Open Contracting Partnership",
        "Kituo Cha Sheria",
        "ARTICLE 19 Eastern Africa",
        "Ethics and Anti-Corruption Commission",
        "Global Innovation Fund",
    ]
    from app.tools import registry

    opened_urls: list[str] = []
    async def fake_open_url(url: str):
        opened_urls.append(url)
        recipient = next(
            address for address in recipients
            if address.split("@", 1)[1] in url
        )
        return ToolResult.ok(
            "Opened official contact page.",
            {
                "url": url,
                "title": f"{organizations[recipients.index(recipient)]} contact",
                "text": f"Contact: {recipient}",
                "public_emails": [recipient],
            },
        ).model_dump_json()

    monkeypatch.setattr(
        registry.get("open_url").tool,
        "coroutine",
        fake_open_url,
    )
    send_steps = [
        {
            "id": f"send_{index}",
            "description": f"Send tailored outreach to {recipient}",
            "tool": "send_email",
            "arguments": {"to": recipient},
            "dependencies": [0],
        }
        for index, recipient in enumerate(recipients)
    ]
    steps = [
        {
            "id": "public-contacts",
            "description": "Find public contacts for all eight organizations",
            "tool": "search_public_contacts",
            "arguments": {
                "organizations": organizations,
            },
            "dependencies": [],
        },
        *send_steps,
    ]
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": steps},
        "id": "org_outreach_plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "search_public_contacts",
        "args": {
            "organizations": organizations,
        },
        "id": "org_contact_search",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "open_url",
        "args": {"url": f"https://{recipient.split('@', 1)[1]}/contact"},
        "id": f"org_verify_{index}",
        "type": "tool_call",
    } for index, recipient in enumerate(recipients)]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
            "name": "send_email",
            "args": {
                "to": recipient,
                "subject": f"Partnership opportunity for {recipient}",
                "body": f"Tailored outreach for {recipient}.",
            },
            "id": f"org_send_{index}",
            "type": "tool_call",
        } for index, recipient in enumerate(recipients)]))
    fake_router.script.append(
        AIMessage(content="All requested outreach emails were sent.")
    )

    sent_to: list[str] = []
    sent_bodies: list[str] = []
    original_send = email_service.send_email

    async def capture_send(**kwargs):
        sent_to.append(kwargs["recipient"])
        sent_bodies.append(kwargs["body"])
        return await original_send(**kwargs)

    monkeypatch.setattr(email_service, "send_email", capture_send)
    response = client.post("/api/chat", headers=headers, json={
        "message": (
            "Find contact emails for eight organizations, draft tailored "
            "outreach, and send each after my approval."
        ),
    })
    assert response.status_code == 200, response.text
    response_data = response.json()
    first_approval = response_data.get("approval")
    assert len(opened_urls) == len(recipients)
    assert first_approval is not None, {
        key: response_data.get(key)
        for key in ("status", "reply", "plan", "error")
    }
    assert first_approval["tool_name"] == "send_email"
    assert first_approval["arguments"]["to"] == recipients[0]
    assert sent_to == []
    task_detail = client.get(
        f"/api/tasks/{response_data['task_id']}", headers=headers
    )
    assert task_detail.status_code == 200, task_detail.text
    outreach_queue = task_detail.json()["outreach_queue"]
    assert len(outreach_queue) == len(recipients)
    assert [item["position"] for item in outreach_queue] == list(
        range(1, len(recipients) + 1)
    )
    assert outreach_queue[0]["organization"] == organizations[0]
    assert outreach_queue[0]["recipient"] == recipients[0]
    assert outreach_queue[0]["status"] == "awaiting_approval"
    assert outreach_queue[0]["source_url"] == (
        f"https://{recipients[0].split('@', 1)[1]}/contact"
    )
    assert outreach_queue[0]["address_found_on_source"] is True
    assert outreach_queue[0]["organization_match_on_source"] is True
    assert outreach_queue[0]["approval_id"] == first_approval["id"]
    assert outreach_queue[0]["draft"] == {
        "to": recipients[0],
        "cc": "",
        "bcc": "",
        "subject": f"Partnership opportunity for {recipients[0]}",
        "body": f"Tailored outreach for {recipients[0]}.",
        "regards": "",
        "attachments": [],
    }
    model_calls_before_approvals = len(fake_router.tape)
    approval = first_approval
    for index, recipient in enumerate(recipients):
        assert approval["tool_name"] == "send_email"
        assert approval["arguments"]["to"] == recipient
        if index == 0:
            result = client.post(
                f"/api/approvals/{approval['id']}/skip", headers=headers
            )
            assert result.status_code == 200, result.text
            assert sent_to == []
            approval = result.json()["approval"]
            assert result.json()["status"] == "paused_approval"
            assert approval is not None
            assert approval["arguments"]["to"] == recipients[index + 1]
            continue
        if index == 1:
            edited_arguments = dict(approval["arguments"])
            edited_arguments["body"] = "Reviewed and edited second-organization message."
            edited = client.patch(
                f"/api/approvals/{approval['id']}",
                headers=headers,
                json={"arguments": edited_arguments},
            )
            assert edited.status_code == 200, edited.text
            assert sent_to == []
            updated_queue = client.get(
                f"/api/tasks/{response_data['task_id']}", headers=headers
            ).json()["outreach_queue"]
            assert updated_queue[1]["draft"]["body"] == (
                "Reviewed and edited second-organization message."
            )

        result = client.post(
            f"/api/approvals/{approval['id']}/approve", headers=headers
        )
        assert result.status_code == 200, result.text
        assert sent_to == recipients[1:index + 1], (
            f"index={index}; status={result.json().get('status')}; "
            f"reply={result.json().get('reply')}; sent_to={sent_to}; "
            f"plan={[(step.get('id'), step.get('status')) for step in result.json().get('plan', [])]}"
        )
        if index < len(recipients) - 1:
            approval = result.json()["approval"]
            assert result.json()["status"] == "paused_approval", (
                f"index={index}; approval={approval}; "
                f"reply={result.json().get('reply')}; "
                f"plan={[(step.get('id'), step.get('tool'), step.get('status'), step.get('dependencies')) for step in result.json().get('plan', [])]}"
            )
            assert approval is not None
            assert approval["tool_name"] == "send_email"
            assert approval["arguments"]["to"] == recipients[index + 1]
            assert len(opened_urls) == len(recipients)
            assert len(fake_router.tape) == model_calls_before_approvals
        else:
            assert result.json()["status"] == "completed"

    assert sent_bodies[0] == "Reviewed and edited second-organization message."
    assert len(sent_bodies) == len(recipients) - 1
    completed_queue = client.get(
        f"/api/tasks/{response_data['task_id']}", headers=headers
    ).json()["outreach_queue"]
    assert [item["status"] for item in completed_queue] == ["skipped"] + [
        "sent"
    ] * (len(recipients) - 1)
    assert all(
        item["provider"] == "email_mock" for item in completed_queue[1:]
    ), completed_queue
    assert all(item["provider_message_id"] for item in completed_queue[1:])
    assert all(item["address_found_on_source"] for item in completed_queue)
    assert [item["source_url"] for item in completed_queue] == opened_urls


def test_outreach_draft_can_be_skipped_after_backend_restart(
    client, fake_router, monkeypatch
):
    headers = _headers(client, "skip-outreach@test.com")
    integration = client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": "sender@example.com"},
    })
    assert integration.status_code == 200, integration.text

    from app.services import search_service
    from app.tools import registry

    organization = "Skip Example Organization"
    recipient = "contact@skip-example.org"
    source_url = "https://skip-example.org/contact"

    async def fake_search(query: str, max_results: int):
        return [search_service.SearchResult(
            title=f"{organization} contact",
            url=source_url,
            snippet=f"Contact {organization} at {recipient}.",
        )]

    async def fake_open_url(url: str):
        return ToolResult.ok(
            "Opened organization contact page.",
            {
                "url": url,
                "title": f"{organization} contact",
                "text": f"Contact: {recipient}",
                "public_emails": [recipient],
            },
        ).model_dump_json()

    monkeypatch.setattr(search_service, "search", fake_search)
    monkeypatch.setattr(registry.get("open_url").tool, "coroutine", fake_open_url)
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": [
            {
                "id": "public-contacts",
                "description": f"Find the official contact email for {organization}.",
                "tool": "search_public_contacts",
                "arguments": {"organizations": [organization]},
                "dependencies": [],
            },
            {
                "id": "send-outreach",
                "description": f"Send a partnership email to {recipient}.",
                "tool": "send_email",
                "arguments": {"to": recipient},
                "dependencies": [0],
            },
        ]},
        "id": "skip-outreach-plan",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "search_public_contacts",
        "args": {"organizations": [organization]},
        "id": "skip-contact-search",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "open_url",
        "args": {"url": source_url},
        "id": "skip-contact-page",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "send_email",
        "args": {
            "to": recipient,
            "subject": "Partnership proposal",
            "body": "Please review this draft.",
        },
        "id": "skip-send-call",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="This organization was skipped."))
    response = client.post("/api/chat", headers=headers, json={
        "message": (
            f"Find the contact email for {organization}, draft tailored outreach, "
            "and send it after my approval."
        ),
        "mode": "agent",
    })
    assert response.status_code == 200, response.text
    response_data = response.json()
    assert response_data["approval"] is not None, {
        key: response_data.get(key)
        for key in ("status", "reply", "plan")
    }
    approval_id = response_data["approval"]["id"]
    task_id = response_data["task_id"]

    # Simulate missing serialized run state and verify queue reconstruction from
    # persisted conversation messages and the task plan.
    from app.database.session import SessionLocal
    from app.models.agent import AgentRun

    with SessionLocal() as db:
        run = db.get(AgentRun, response_data["run_id"])
        assert run is not None and run.state_json is not None
        run.state_json = None
        db.commit()
    restarted_process = subprocess.run(
        [sys.executable, "-c", """
import json
import sys
from fastapi.testclient import TestClient
from app.main import app

request = json.load(sys.stdin)
with TestClient(app) as client:
    response = client.get(
        f"/api/tasks/{request['task_id']}",
        headers=request["headers"],
    )
    print("JALORA_RESTART_RESULT=" + json.dumps({
        "status_code": response.status_code,
        "body": response.json(),
    }))
"""],
        input=json.dumps({"task_id": task_id, "headers": headers}),
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )
    assert restarted_process.returncode == 0, restarted_process.stderr
    result_line = next(
        (
            line.removeprefix("JALORA_RESTART_RESULT=")
            for line in reversed(restarted_process.stdout.splitlines())
            if line.startswith("JALORA_RESTART_RESULT=")
        ),
        None,
    )
    assert result_line is not None, restarted_process.stdout
    restarted_response = json.loads(result_line)
    assert restarted_response["status_code"] == 200
    restarted_queue = restarted_response["body"]["outreach_queue"]
    assert restarted_queue[0]["approval_id"] == approval_id
    assert restarted_queue[0]["status"] == "awaiting_approval"
    assert restarted_queue[0]["source_url"] == source_url

    recovered_queue = client.get(
        f"/api/tasks/{task_id}", headers=headers
    ).json()["outreach_queue"]
    assert recovered_queue[0]["approval_id"] == approval_id
    assert recovered_queue[0]["source_url"] == source_url
    assert recovered_queue[0]["address_found_on_source"] is True
    assert recovered_queue[0]["organization_match_on_source"] is True
    edited_arguments = dict(response_data["approval"]["arguments"])
    edited_arguments["body"] = "Edited after losing the serialized run state."
    edited = client.patch(
        f"/api/approvals/{approval_id}",
        headers=headers,
        json={"arguments": edited_arguments},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["arguments"]["body"] == edited_arguments["body"]
    edited_queue = client.get(
        f"/api/tasks/{task_id}", headers=headers
    ).json()["outreach_queue"]
    assert edited_queue[0]["draft"]["body"] == edited_arguments["body"]
    with SessionLocal() as db:
        run = db.get(AgentRun, response_data["run_id"])
        assert run is not None and run.state_json is not None
        run.state_json = None
        db.commit()
    restarted_decision = subprocess.run(
        [sys.executable, "-c", """
import json
import os
import sys
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_router

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from conftest import FakeRouter
from langchain_core.messages import AIMessage

request = json.load(sys.stdin)
router = FakeRouter([AIMessage(content="The task is complete with no email sent.")])
app.dependency_overrides[get_router] = lambda: router
with TestClient(app) as client:
    response = client.post(
        f"/api/approvals/{request['approval_id']}/skip",
        headers=request["headers"],
    )
    print("JALORA_RESTART_RESULT=" + json.dumps({
        "status_code": response.status_code,
        "body": response.json(),
    }))
"""],
        input=json.dumps({"approval_id": approval_id, "headers": headers}),
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )
    assert restarted_decision.returncode == 0, restarted_decision.stderr
    decision_line = next(
        (
            line.removeprefix("JALORA_RESTART_RESULT=")
            for line in reversed(restarted_decision.stdout.splitlines())
            if line.startswith("JALORA_RESTART_RESULT=")
        ),
        None,
    )
    assert decision_line is not None, restarted_decision.stdout
    decision_response = json.loads(decision_line)
    assert decision_response["status_code"] == 200, decision_response
    saved_approval = next(
        item for item in client.get("/api/approvals", headers=headers).json()
        if item["id"] == approval_id
    )
    assert saved_approval["status"] == "skipped"
    queue = client.get(
        f"/api/tasks/{task_id}", headers=headers
    ).json()["outreach_queue"]
    assert [item["status"] for item in queue] == ["skipped"]
    assert queue[0]["draft"] is None


def test_public_outreach_rejects_recipient_missing_from_organization_page(
    client, fake_router, monkeypatch
):
    headers = _headers(client, "unverified-outreach@test.com")
    client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": "sender@example.com"},
    })

    from app.services import search_service

    async def fake_search(query: str, max_results: int):
        return [search_service.SearchResult(
            title="Acme Institute contact",
            url="https://acme.example.org/contact",
            snippet="Contact Acme Institute.",
        )]

    async def fake_open_url(url: str):
        return ToolResult.ok(
            "Opened the contact page.",
            {
                "url": url,
                "text": "Email support@acme.example.org",
                "public_emails": ["support@acme.example.org"],
            },
        ).model_dump_json()

    from app.tools import registry

    monkeypatch.setattr(search_service, "search", fake_search)
    monkeypatch.setattr(
        registry.get("open_url").tool,
        "coroutine",
        fake_open_url,
    )
    sent_to: list[str] = []
    original_send = email_service.send_email

    async def capture_send(**kwargs):
        sent_to.append(kwargs["recipient"])
        return await original_send(**kwargs)

    monkeypatch.setattr(email_service, "send_email", capture_send)
    organization = "Acme Institute"
    unverified_recipient = "invented@acme.example.org"
    for message in [
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [
                {
                    "id": "contacts",
                    "description": "Find the public contact page for Acme Institute.",
                    "tool": "search_public_contacts",
                    "arguments": {"organizations": [organization]},
                    "dependencies": [],
                },
                {
                    "id": "send-acme",
                    "description": f"Send outreach to {unverified_recipient}.",
                    "tool": "send_email",
                    "arguments": {"to": unverified_recipient},
                    "dependencies": [0],
                },
            ]},
            "id": "unverified_outreach_plan",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "search_public_contacts",
            "args": {"organizations": [organization]},
            "id": "unverified_contact_search",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "open_url",
            "args": {"url": "https://acme.example.org/contact"},
            "id": "unverified_contact_page",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "send_email",
            "args": {
                "to": unverified_recipient,
                "subject": "Partnership proposal",
                "body": "Hello, I would like to discuss a partnership.",
            },
            "id": "unverified_send",
            "type": "tool_call",
        }]),
        AIMessage(content=(
            "I could not confirm that recipient on the organization's contact "
            "page, so nothing was sent."
        )),
    ]:
        fake_router.script.append(message)

    response = client.post("/api/chat", headers=headers, json={
        "message": (
            "Find contact emails for Acme Institute and send an outreach email "
            "after my approval."
        ),
    })

    assert response.status_code == 200, response.text
    data = response.json()
    assert data.get("approval") is None
    assert data.get("status") != "paused_approval"
    assert sent_to == []


def test_approval_requires_authentication(client, fake_router):
    headers = _headers(client, "noauth@test.com")
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "ping_external", "args": {"message": "x"},
            "id": "call_x", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="done"))
    data = client.post("/api/chat", json={"message": "Ping"}, headers=headers).json()

    resp = client.post(f"/api/approvals/{data['approval']['id']}/approve")
    assert resp.status_code == 401


def test_resume_keeps_system_persona(client, fake_router):
    """After approval the resumed run must still get the full system prompt
    (persona + user name), not a bare conversation (spec §29)."""
    headers = _headers(client, "persona@test.com")
    fake_router.script.append(AIMessage(
        content="", tool_calls=[{
            "name": "ping_external", "args": {"message": "go"},
            "id": "call_persona", "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(content="Done after approval."))

    data = client.post("/api/chat", json={"message": "Ping"}, headers=headers).json()
    assert data["status"] == "paused_approval"

    result = client.post(
        f"/api/approvals/{data['approval']['id']}/approve", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"

    # model call #0 = the paused turn, #1 = the resumed turn
    calls = list(fake_router.tape)
    assert len(calls) >= 2
    resume_messages = calls[-1]
    assert resume_messages, "resume turn recorded no messages"
    head = resume_messages[0]
    assert isinstance(head, SystemMessage), (
        "resumed run lost its system prompt — persona/memory context dropped"
    )
    assert "The user's name is Approver" in head.content
    assert "Jalora AI" in head.content


def test_approved_email_resumes_after_backend_restart(
    client, fake_router,
):
    headers = _headers(client, "restart-approved-send@test.com")
    integration = client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": "sender@example.com"},
    })
    assert integration.status_code == 200, integration.text

    recipient = "contact@restart-example.org"
    for message in [
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [{
                "id": "send-email",
                "description": f"Send the reviewed message to {recipient}.",
                "tool": "send_email",
                "arguments": {"to": recipient},
                "dependencies": [],
            }]},
            "id": "restart-send-plan",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "send_email",
            "args": {
                "to": recipient,
                "subject": "Restart recovery",
                "body": "This mock email must be sent only after approval.",
            },
            "id": "restart-send-call",
            "type": "tool_call",
        }]),
    ]:
        fake_router.script.append(message)
    response = client.post("/api/chat", headers=headers, json={
        "message": f"Send an email to {recipient} after I approve it.",
        "mode": "agent",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "paused_approval"
    approval_id = data["approval"]["id"]
    run_id = data["run_id"]

    from app.database.session import SessionLocal
    from app.models.agent import AgentRun

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run is not None and run.state_json is not None
        run.state_json = None
        db.commit()

    restarted_process = subprocess.run(
        [sys.executable, "-c", """
import json
import os
import sys
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_router
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from conftest import FakeRouter
from langchain_core.messages import AIMessage

request = json.load(sys.stdin)
app.dependency_overrides[get_router] = lambda: FakeRouter([
    AIMessage(content="The approved email was sent and its receipt was recorded.")
])
with TestClient(app) as client:
    response = client.post(
        f"/api/approvals/{request['approval_id']}/approve",
        headers=request["headers"],
    )
    print("JALORA_RESTART_RESULT=" + json.dumps({
        "status_code": response.status_code,
        "body": response.json(),
    }))
"""],
        input=json.dumps({"approval_id": approval_id, "headers": headers}),
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )
    assert restarted_process.returncode == 0, restarted_process.stderr
    result_line = next(
        (
            line.removeprefix("JALORA_RESTART_RESULT=")
            for line in reversed(restarted_process.stdout.splitlines())
            if line.startswith("JALORA_RESTART_RESULT=")
        ),
        None,
    )
    assert result_line is not None, restarted_process.stdout
    restarted_response = json.loads(result_line)
    assert restarted_response["status_code"] == 200, restarted_response
    assert restarted_response["body"]["status"] == "completed"

    with SessionLocal() as db:
        from app.models.agent import ActivityLog, Approval

        approval = db.get(Approval, approval_id)
        assert approval is not None
        assert approval.status == "approved"
        assert approval.result["status"] == "provider_confirmed"
        assert approval.result["provider_receipt"]["provider"] == "email_mock"
        assert approval.result["provider_receipt"]["message_id"]
        call_id = approval.arguments["call_id"]
        send_logs = db.query(ActivityLog).filter_by(
            run_id=run_id, tool="send_email", success=True,
        ).filter(
            ActivityLog.meta["call_id"].as_string() == call_id
        ).all()
        assert len(send_logs) == 1
