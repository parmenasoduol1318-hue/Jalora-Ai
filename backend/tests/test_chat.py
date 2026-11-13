import io
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.errors import ToolResult
from app.services.chat_service import (
    _apply_decoding_profile,
    _recover_capability_rejected_sends,
    _history_for_agent_turn,
    _routing_context_for_followup,
    _tool_names_for_resume,
)


def test_referential_outreach_followup_carries_prior_user_context():
    history = [
        HumanMessage(content=(
            "The application is Vox Kenta. Find public contact emails for "
            "Transparency International Kenya and Code for Africa."
        )),
        AIMessage(content="I found the organizations."),
        HumanMessage(content=(
            "Search official contact emails for TI-Kenya and Code for Africa, "
            "then prepare custom outreach emails."
        )),
        AIMessage(content="I found the organizations."),
        HumanMessage(content=(
            "Replace the old app name in all the drafts and write emails to all of them."
        )),
    ]

    context = _routing_context_for_followup(
        str(history[-1].content), history
    )

    assert "official contact emails" in context
    assert "Vox Kenta" in context
    assert _routing_context_for_followup("What time is it?", history) == ""


def test_fresh_agent_task_does_not_receive_unrelated_hotel_history():
    history = [
        HumanMessage(content="Find hotels in Paris and compare prices."),
        AIMessage(content="I found Hotel XYZ."),
        HumanMessage(content="open youtube"),
    ]

    assert _history_for_agent_turn(
        history, mode="agent", routing_context=""
    ) == [history[-1]]
    assert _history_for_agent_turn(
        history, mode="chat", routing_context=""
    ) == history
    assert _history_for_agent_turn(
        history, mode="agent", routing_context="Find hotels in Paris"
    ) == history


def test_complex_chat_generation_uses_configured_timeout_and_output_budget(monkeypatch):
    from app.config import Settings
    from app.services.model_router import JaloraChatModel, ProviderSpec

    settings = Settings(
        _env_file=None,
        agent_max_output_tokens=12288,
        model_request_timeout_seconds=900,
    )
    monkeypatch.setattr("app.services.chat_service.get_settings", lambda: settings)
    model = JaloraChatModel(
        spec=ProviderSpec("test", "openai", "http://localhost/v1", None, "m", "m"),
        model_name="m",
    )

    complex_model = _apply_decoding_profile(model, "task")
    ordinary_model = _apply_decoding_profile(model, "chat")

    assert complex_model.max_tokens == 12288
    assert complex_model.timeout == 900
    assert ordinary_model.max_tokens == 1536
    assert ordinary_model.timeout == 900
    assert model.max_tokens == 4096
    assert model.timeout == 300


def test_continue_from_where_left_restores_original_task_context():
    original_request = (
        "Search the web for current 2026 Nairobi Expressway toll charges and "
        "current CBK commercial lending rates, calculate fleet costs, and "
        "create a cited PDF report."
    )
    history = [
        HumanMessage(content=original_request),
        AIMessage(content="I started the research."),
        HumanMessage(content="continue from where you left"),
        AIMessage(content="I need to continue."),
        HumanMessage(content="CONTINUE AND DONT STOP UNTILL ALL THE PROCESS IS DONE"),
    ]

    context = _routing_context_for_followup(
        str(history[-1].content), history
    )

    assert original_request in context
    assert "continue from where you left" not in context


def test_bus_option_followup_reuses_recent_provider_booking_context():
    original_request = "Book a bus from Mombasa to Nairobi for tomorrow."
    provider_result = json.dumps({
        "provider": "buupass",
        "status": "provider_handoff",
        "booking_url": "https://buupass.com/Mombasa-to-Nairobi",
    })
    history = [
        HumanMessage(content=original_request),
        ToolMessage(content=provider_result, tool_call_id="bus-handoff"),
        AIMessage(content="I found the current BuuPass options. Which bus do you want?"),
        HumanMessage(content="I'll choose option 2, the MashPoa at 10 AM."),
    ]

    context = _routing_context_for_followup(
        str(history[-1].content), history
    )

    assert original_request in context


def test_plain_passenger_detail_followup_reuses_bus_booking_context():
    original_request = "Book a bus from Mombasa to Nairobi for tomorrow."
    provider_result = json.dumps({
        "provider": "buupass",
        "status": "provider_handoff",
        "booking_url": "https://buupass.com/Mombasa-to-Nairobi",
    })
    history = [
        HumanMessage(content=original_request),
        ToolMessage(content=provider_result, tool_call_id="bus-handoff"),
        AIMessage(content="Please provide the passenger's full name and phone number."),
        HumanMessage(content="Sam Example"),
    ]

    context = _routing_context_for_followup(
        str(history[-1].content), history
    )

    assert original_request in context


def test_resume_rebinds_email_send_from_validated_pending_plan():
    state = {
        "intent": "email",
        "plan": [
            {
                "id": "send_ti_kenya",
                "description": "Send email to Transparency International Kenya",
                "status": "pending",
                "tool": "send_email",
                "dependencies": [],
            },
            {
                "id": "send_cfa",
                "description": "Send email to Code for Africa",
                "status": "pending",
                "tool": "send_email",
                "dependencies": [],
            },
            {
                "id": "final_response",
                "description": "Confirm completed sends",
                "status": "pending",
                "kind": "response",
                "dependencies": ["send_ti_kenya", "send_cfa"],
            },
        ],
    }

    tools = set(_tool_names_for_resume(
        state,
        task_goal="I said separately tailored email for each recipient.",
        coding=False,
    ))

    assert {"send_email", "update_plan"} <= tools


def test_resume_rebinds_public_outreach_capabilities_from_pending_plan():
    state = {
        "intent": "chat",
        "messages": [HumanMessage(content="continue from where you left")],
        "plan": [
            {
                "id": "search",
                "description": "Search web for public contacts of the 8 organizations",
                "status": "pending",
                "tool": "search_web",
                "dependencies": [],
            },
            {
                "id": "draft",
                "description": "Compose email batch for all verified recipients",
                "status": "pending",
                "tool": "compose_email_batch",
                "dependencies": ["search"],
            },
            {
                "id": "send",
                "description": "Send emails or submit for approval",
                "status": "pending",
                "tool": "send_email",
                "dependencies": ["draft"],
            },
        ],
    }

    tools = set(_tool_names_for_resume(
        state,
        task_goal="continue from where you left",
        coding=False,
    ))

    assert {
        "search_web",
        "search_public_contacts",
        "compose_email_batch",
        "send_email",
        "update_plan",
    } <= tools


def test_resume_does_not_bind_unknown_plan_tool():
    state = {
        "intent": "email",
        "plan": [{
            "id": "bad",
            "description": "Run an unknown operation",
            "status": "pending",
            "tool": "unknown_external_action",
            "dependencies": [],
        }],
    }

    tools = set(_tool_names_for_resume(
        state,
        task_goal="Continue.",
        coding=False,
    ))

    assert "unknown_external_action" not in tools


def test_resume_keeps_single_recipient_public_outreach_as_draft_only():
    state = {
        "intent": "email",
        "messages": [HumanMessage(
            content=(
                "Find the official contact email for Katiba Institute and "
                "prepare a tailored outreach email."
            )
        )],
        "plan": [{
            "id": "send_katiba",
            "description": "Send email to Katiba Institute",
            "status": "pending",
            "tool": "send_email",
            "dependencies": [],
        }],
    }

    tools = set(_tool_names_for_resume(
        state,
        task_goal="Prepare a tailored outreach email for Katiba Institute.",
        coding=False,
    ))

    assert "send_email" not in tools


def test_resume_rebinds_single_recipient_public_email_send():
    state = {
        "intent": "email",
        "messages": [HumanMessage(content=(
            "Find the official contact email for Katiba Institute and send "
            "a tailored outreach email after I approve it."
        ))],
        "plan": [{
            "id": "send_katiba",
            "description": "Send email to Katiba Institute",
            "status": "pending",
            "tool": "send_email",
            "dependencies": [0],
        }],
    }

    tools = set(_tool_names_for_resume(
        state,
        task_goal=(
            "Find the official contact email for Katiba Institute and send "
            "a tailored outreach email after I approve it."
        ),
        coding=False,
    ))

    assert "send_email" in tools
    assert "send_email_batch" not in tools


def test_resume_recovers_saved_multi_recipient_sends_rejected_by_old_tool_scope():
    recipients = ["one@example.org", "two@example.org"]
    calls = [{
        "name": "send_email",
        "args": {
            "to": recipient,
            "subject": f"Partnership with {recipient}",
            "body": f"Tailored message for {recipient}.",
        },
        "id": f"old_call_{index}",
        "type": "tool_call",
    } for index, recipient in enumerate(recipients)]
    state = {
        "messages": [
            HumanMessage(content="Send separately tailored emails to both organizations."),
            AIMessage(content="", tool_calls=calls),
            *[
                ToolMessage(
                    content=ToolResult.fail(
                        "CAPABILITY_NOT_SELECTED", "Old tool scope omitted send_email."
                    ).model_dump_json(),
                    tool_call_id=call["id"],
                )
                for call in calls
            ],
        ],
        "plan": [{
            "id": f"send_{index}",
            "description": f"Send email to {recipient}",
            "tool": "send_email",
            "status": "pending",
        } for index, recipient in enumerate(recipients)],
    }

    assert _recover_capability_rejected_sends(state) is True
    recovered = state["messages"][-1]
    assert isinstance(recovered, AIMessage)
    assert [call["args"]["to"] for call in recovered.tool_calls] == recipients
    assert all(
        call["id"] not in {old["id"] for old in calls}
        for call in recovered.tool_calls
    )


def test_resume_does_not_recover_a_single_recipient_send():
    state = {
        "messages": [
            HumanMessage(content="Send the approved outreach email."),
            AIMessage(content="", tool_calls=[{
                "name": "send_email",
                "args": {
                    "to": "one@example.org",
                    "subject": "Partnership",
                    "body": "The reviewed message.",
                },
                "id": "single_send",
                "type": "tool_call",
            }]),
            ToolMessage(
                content=ToolResult.fail(
                    "CAPABILITY_NOT_SELECTED", "Old tool scope omitted send_email."
                ).model_dump_json(),
                tool_call_id="single_send",
            ),
        ],
        "plan": [{
            "id": "send_one",
            "description": "Send email to one@example.org",
            "tool": "send_email",
            "status": "pending",
        }],
    }

    assert _recover_capability_rejected_sends(state) is False
    assert len(state["messages"]) == 3


def _token(client, email="chat@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Chatter",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_model_outage_before_planning_saves_a_resumable_task(
    client, fake_router, monkeypatch
):
    from conftest import Box, FakeModel
    from app.database.session import SessionLocal
    from app.models.agent import AgentRun
    from app.models.task import Task, TaskStep, TaskStepAttempt
    from app.services import search_service
    from app.tools import registry

    class UnavailableModel(FakeModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError(
                "openai API error 429: You have no credits remaining"
            )
            yield

    fake_router.model = UnavailableModel(
        script=Box(),
        tape=fake_router.tape,
    )
    fake_router.route_chain = lambda **_kwargs: []
    headers = _token(client, "outreach-model-outage@test.com")
    integration = client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": "sender@example.com"},
    })
    assert integration.status_code == 200, integration.text

    response = client.post("/api/chat", headers=headers, json={
        "message": (
            "Find the official contact email for Example Partnership Network, "
            "draft a tailored partnership email, and send it after I approve."
        ),
        "mode": "agent",
    })

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "partial"
    assert result["task_id"]
    assert "resume this task" in result["reply"].casefold()

    task_detail = client.get(
        f"/api/tasks/{result['task_id']}", headers=headers
    )
    assert task_detail.status_code == 200, task_detail.text
    assert task_detail.json()["status"] == "partial"
    assert task_detail.json()["resumable"] is True
    assert task_detail.json()["outreach_queue"] is None

    with SessionLocal() as db:
        task = db.get(Task, result["task_id"])
        run = db.get(AgentRun, task.run_id)
        assert run.status == "partial"
        assert run.state_json

    organization = "Example Partnership Network"
    recipient = "partnerships@example.org"
    contact_url = "https://example.test/contact"

    async def fake_search(query: str, max_results: int):
        return [search_service.SearchResult(
            title=f"{organization} contact",
            url=contact_url,
            snippet=f"Contact {organization} at {recipient}.",
        )]

    async def fake_open_url(url: str):
        assert url == contact_url
        return ToolResult.ok(
            "Opened official contact page.",
            {
                "url": url,
                "title": f"{organization} contact",
                "text": f"{organization} partnership contact: {recipient}",
                "public_emails": [recipient],
            },
        ).model_dump_json()

    monkeypatch.setattr(search_service, "search", fake_search)
    monkeypatch.setattr(
        registry.get("open_url").tool,
        "coroutine",
        fake_open_url,
    )
    fake_router.model = FakeModel(script=Box([
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [
                {
                    "id": "public-contacts",
                    "description": f"Find the official contact for {organization}",
                    "tool": "search_public_contacts",
                    "arguments": {"organizations": [organization]},
                    "dependencies": [],
                },
                {
                    "id": "verify-contact",
                    "description": f"Open the official contact page for {organization}",
                    "tool": "open_url",
                    "arguments": {"url": contact_url},
                    "dependencies": [0],
                },
                {
                    "id": "send-outreach",
                    "description": f"Send a tailored partnership email to {recipient}",
                    "tool": "send_email",
                    "arguments": {"to": recipient},
                    "dependencies": [1],
                },
            ]},
            "id": "recovered-plan",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "search_public_contacts",
            "args": {"organizations": [organization]},
            "id": "recovered-search",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "open_url",
            "args": {"url": contact_url},
            "id": "recovered-open",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "send_email",
            "args": {
                "to": recipient,
                "subject": "Partnership opportunity",
                "body": "I would welcome a conversation about partnering.",
            },
            "id": "recovered-send",
            "type": "tool_call",
        }]),
    ]))
    sent_to: list[str] = []
    from app.services import email as email_service

    original_send = email_service.send_email

    async def capture_send(**kwargs):
        sent_to.append(kwargs["recipient"])
        return await original_send(**kwargs)

    monkeypatch.setattr(email_service, "send_email", capture_send)
    with SessionLocal() as db:
        run_id = db.get(AgentRun, result["run_id"]).id

    resumed = client.post(
        f"/api/runs/{run_id}/resume",
        headers=headers,
    )
    assert resumed.status_code == 200, resumed.text
    recovered = resumed.json()
    assert recovered["status"] == "paused_approval", {
        "reply": recovered.get("reply"),
        "plan": recovered.get("plan"),
        "error": recovered.get("error"),
    }
    assert recovered["approval"]["arguments"]["to"] == recipient
    assert sent_to == []

    queue = client.get(
        f"/api/tasks/{result['task_id']}",
        headers=headers,
    ).json()["outreach_queue"]
    assert len(queue) == 1
    assert queue[0]["recipient"] == recipient
    assert queue[0]["status"] == "awaiting_approval"

    approved = client.post(
        f"/api/approvals/{recovered['approval']['id']}/approve",
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    from sqlalchemy import select

    with SessionLocal() as db:
        task_steps = db.scalars(
            select(TaskStep).where(TaskStep.task_id == result["task_id"])
        ).all()
        attempts = db.scalars(
            select(TaskStepAttempt).where(
                TaskStepAttempt.task_id == result["task_id"]
            )
        ).all()
    assert approved.json()["status"] == "completed", {
        "reply": approved.json().get("reply"),
        "sent_to": sent_to,
        "steps": [
            (step.status, step.outcome_status, step.error_code)
            for step in task_steps
        ],
        "attempts": [
            (attempt.state, attempt.outcome_status, attempt.error_code)
            for attempt in attempts
        ],
    }
    assert sent_to == [recipient]


def test_exact_youtube_request_opens_browser_without_model(client, fake_router, monkeypatch):
    from app.tools import device_tools

    opened = []
    monkeypatch.setattr(device_tools.sys, "platform", "win32")
    monkeypatch.setattr(
        device_tools.webbrowser,
        "open",
        lambda url, new: opened.append((url, new)) or True,
    )
    headers = _token(client, "youtube-direct@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "open youtube"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "completed"
    assert data["reply"] == "Opened YouTube in the default browser."
    assert opened == [("https://www.youtube.com/", 2)]
    assert not fake_router.tape


@pytest.mark.parametrize(
    ("close_result", "expected_status", "expected_reply"),
    [
        (
            {"status": "closed", "process_id": 321},
            "completed",
            "Closed Calculator.",
        ),
        (
            {"status": "close_requested", "process_id": 321},
            "partial",
            "Asked Calculator to close gracefully, but could not verify that it exited.",
        ),
    ],
)
def test_exact_app_close_requires_approval_and_reports_verified_result(
    client, fake_router, monkeypatch, close_result, expected_status, expected_reply,
):
    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.models.device import LocalDeviceApp
    from app.models.user import User
    from app.services import local_device_control
    from app.tools import device_tools

    headers = _token(client, "local-app-close@test.com")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "local-app-close@test.com"))
        assert user is not None
        app = LocalDeviceApp(
            user_id=user.id,
            name="Calculator",
            executable_path=r"C:\Windows\System32\calc.exe",
            enabled=True,
        )
        db.add(app)
        db.commit()
        app_id = app.id

    closed = []
    monkeypatch.setattr(device_tools.sys, "platform", "win32")
    monkeypatch.setattr(
        local_device_control,
        "close_local_app",
        lambda user_id, selected_app_id: (
            closed.append((user_id, selected_app_id))
            or close_result
        ),
    )

    response = client.post(
        "/api/chat",
        json={"message": "close Calculator"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    pending = response.json()
    assert pending["status"] == "paused_approval"
    assert closed == []
    assert not fake_router.tape

    approvals = client.get("/api/approvals", headers=headers).json()
    approval = next(row for row in approvals if row["tool_name"] == "close_local_app")
    approved = client.post(
        f"/api/approvals/{approval['id']}/approve",
        headers=headers,
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == expected_status, approved.json()
    assert approved.json()["reply"] == expected_reply
    assert closed == [(user.id, app_id)]
    assert not fake_router.tape


def test_normal_conversation_persists(client, fake_router):
    fake_router.script.append(AIMessage(content="Hello! How can I help?"))
    headers = _token(client)

    r1 = client.post("/api/chat", json={"message": "Hi"}, headers=headers)
    assert r1.status_code == 200, r1.text
    data = r1.json()
    assert data["status"] == "completed"
    assert data["reply"] == "Hello! How can I help?"
    conv_id = data["conversation_id"]

    r2 = client.post("/api/chat", json={
        "message": "What did I just say?", "conversation_id": conv_id,
    }, headers=headers)
    assert r2.status_code == 200
    assert r2.json()["conversation_id"] == conv_id

    msgs = client.get(f"/api/conversations/{conv_id}/messages", headers=headers).json()
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user", "assistant"]

    convs = client.get("/api/conversations", headers=headers).json()
    assert len(convs) == 1

    # the model must have received prior turns as context
    second_call_msgs = fake_router.tape[-1]
    contents = [getattr(m, "content", "") for m in second_call_msgs]
    assert any("Hi" == c for c in contents)
    assert any("Hello! How can I help?" in str(c) for c in contents)


def test_attached_document_is_read_before_completing_user_task(
        client, fake_router):
    headers = _token(client, "chat-attached-document@test.com")
    content = (
        "Employment Contract Termination Clause. "
        "Either party may terminate this agreement with 30 days written notice. "
        "The notice must be delivered to the registered office."
    )
    upload = client.post("/api/documents", headers=headers, files={
        "file": ("contract.txt", io.BytesIO(content.encode("utf-8")), "text/plain"),
    })
    assert upload.status_code == 201, upload.text
    document = upload.json()

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "read_document",
        "args": {
            "query": "Extract the termination notice period.",
            "document_id": document["id"],
        },
        "id": "read-attached-contract",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(
        content="The contract requires 30 days' written notice."
    ))

    response = client.post("/api/chat", headers=headers, json={
        "message": "Extract the termination notice period from the attached contract.",
        "document_ids": [document["id"]],
    })

    assert response.status_code == 200, response.text
    assert "30 days" in response.json()["reply"]
    tool_messages = [
        message for call in fake_router.tape
        for message in call
        if getattr(message, "type", "") == "tool"
    ]
    assert any("30 days" in str(message.content) for message in tool_messages)
    user_messages = [
        message for call in fake_router.tape
        for message in call
        if getattr(message, "type", "") == "human"
    ]
    assert any(document["id"] in str(message.content) for message in user_messages)


def test_attached_document_must_belong_to_requesting_user(client):
    owner_headers = _token(client, "document-owner@test.com")
    other_headers = _token(client, "document-other-user@test.com")
    upload = client.post("/api/documents", headers=owner_headers, files={
        "file": ("private.txt", io.BytesIO(b"private document text"), "text/plain"),
    })
    assert upload.status_code == 201, upload.text

    response = client.post("/api/chat", headers=other_headers, json={
        "message": "Summarize this document.",
        "document_ids": [upload.json()["id"]],
    })
    assert response.status_code == 404, response.text


def test_hotel_booking_missing_details_bypasses_model_planning(client, fake_router):
    headers = _token(client, "hotel-details@test.com")
    response = client.post(
        "/api/chat",
        json={
            "message": (
                "book for me this hotel "
                "Sarova Whitesands Beach Resort & Spa Mombasa"
            )
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    outcome = response.json()
    assert outcome["status"] == "completed"
    assert "check-in and check-out dates (YYYY-MM-DD)" in outcome["reply"]
    assert "number of adults" in outcome["reply"]
    assert "number of rooms" in outcome["reply"]
    assert "Booking.com" in outcome["reply"]
    assert not fake_router.tape


def test_current_factual_question_runs_web_search_before_model_answer(
        client, fake_router, monkeypatch):
    from app.tools import web

    queries = []

    async def fake_search(query, max_results=5):
        queries.append(query)
        return ToolResult.ok("Found one current source.", {
            "results": [{
                "title": "Israel Prime Minister",
                "url": "https://news.example.test/israel-pm",
                "snippet": "Benjamin Netanyahu is the current prime minister.",
            }],
        })

    async def fake_open(url):
        assert url == "https://news.example.test/israel-pm"
        return ToolResult.ok("Opened source.", {
            "title": "Israel Prime Minister",
            "url": url,
            "text": "Benjamin Netanyahu is the current prime minister.",
        })

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", fake_open)
    fake_router.script.append(AIMessage(
        content=(
            "I do not have access to a web search tool, so I cannot verify "
            "the current prime minister."
        )
    ))
    headers = _token(client, "live-search-preflight@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who is the current prime minister of Israel?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert queries == ["Who is the current prime minister of Israel?"]
    assert "Benjamin Netanyahu" in data["reply"]
    assert "do not have access to a web search tool" not in data["reply"]
    assert "https://news.example.test/israel-pm" in data["reply"]
    assert data["sources"][0]["url"] == "https://news.example.test/israel-pm"
    assert data["sources"][0]["snippet"] == (
        "Benjamin Netanyahu is the current prime minister."
    )


def test_preflight_retains_exact_numeric_value_beyond_short_page_prefix(
        client, fake_router, monkeypatch):
    from app.tools import web
    from langchain_core.messages import SystemMessage

    url = "https://rates.example.test/current"
    exact_value = "The current lending rate is 15.75% effective 2026-10-01."

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one source.", {
            "results": [{
                "title": "Official lending rates",
                "url": url,
                "snippet": "Official lending rates.",
            }],
        })

    async def fake_open(opened_url):
        return ToolResult.ok("Opened source.", {
            "title": "Official lending rates",
            "url": opened_url,
            "text": "Policy information. " * 400 + exact_value,
        })

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", fake_open)
    fake_router.script.append(AIMessage(content=exact_value))
    headers = _token(client, "exact-value-preflight@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "What is the current lending rate?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert exact_value in response.json()["reply"]
    system_messages = [
        message.content
        for messages in fake_router.tape
        for message in messages
        if isinstance(message, SystemMessage)
    ]
    assert any(exact_value in message for message in system_messages)


def test_current_affairs_does_not_return_model_claim_contradicting_search_evidence(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one current source.", {
            "results": [{
                "title": "Kenya State House",
                "url": (
                    "https://www.president.go.ke/administration/"
                    "office-of-the-president/"
                ),
                "snippet": "William Ruto is the President of Kenya.",
            }],
        })

    async def fake_open(url):
        return ToolResult.ok("Opened source.", {
            "title": "Kenya State House",
            "url": url,
            "text": "William Ruto is the President of Kenya.",
        })

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", fake_open)
    fake_router.script.append(AIMessage(
        content="The current Kenyan president is Daniel Mwai Kipkoele."
    ))
    headers = _token(client, "kenya-current-president@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who is the current president of Kenya?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert "Daniel Mwai Kipkoele" not in reply
    assert "William Ruto is the President of Kenya." in reply
    assert "retrieved " in reply
    assert "https://www.president.go.ke/administration/" in reply


def test_current_affairs_does_not_expose_datetime_tool_as_the_answer(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one current source.", {
            "results": [{
                "title": "The Official Website of the President of Kenya",
                "url": "https://www.president.go.ke/",
                "snippet": "His Excellency William Samoei Ruto, President of Kenya.",
            }],
        })

    async def fake_open(url):
        return ToolResult.fail("HTTP_ERROR", "HTTP 403")

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", fake_open)
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "current_datetime",
        "args": {},
        "id": "current-time-for-query",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(
        content=(
            "The current UTC time is 2026-10-27 20:39:49 (Tuesday). "
            "It's 10:39 in the morning."
        )
    ))
    headers = _token(client, "kenya-current-time-tool@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who is the current Kenyan president?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert "William Samoei Ruto" in reply
    assert "current UTC time" not in reply.lower()
    assert "https://www.president.go.ke/" in reply


def test_historical_factual_question_uses_retrieved_evidence(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one historical source.", {
            "results": [{
                "title": "Kenya 2002 presidential election",
                "url": "https://news.example.test/kenya-election-2002",
                "snippet": "Mwai Kibaki became President of Kenya in December 2002.",
            }],
        })

    async def failed_open(url):
        return ToolResult.fail("HTTP_ERROR", "HTTP 403")

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", failed_open)
    fake_router.script.append(AIMessage(
        content="Daniel Mwai Kipkoele was the Kenyan president in 2002."
    ))
    headers = _token(client, "kenya-historical-president@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who was President of Kenya in 2002?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert "Daniel Mwai Kipkoele" not in reply
    assert "Mwai Kibaki became President of Kenya in December 2002." in reply
    assert "search-result snippet" in reply
    assert "https://news.example.test/kenya-election-2002" in reply


def test_model_claim_is_suppressed_when_search_returns_no_usable_sources(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def empty_search(query, max_results=5):
        return ToolResult.ok("Found no usable sources.", {"results": []})

    monkeypatch.setattr(web, "search_web", empty_search)
    fake_router.script.append(AIMessage(
        content="The current Kenyan president is Daniel Mwai Kipkoele."
    ))
    headers = _token(client, "kenya-empty-search@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who is the current president of Kenya?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert "Daniel Mwai Kipkoele" not in reply
    assert "returned no usable sources" in reply


def test_current_factual_question_hides_pseudo_tool_output_and_returns_live_evidence(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one current source.", {
            "results": [{
                "title": "Israel Prime Minister",
                "url": "https://news.example.test/israel-pm",
                "snippet": "Benjamin Netanyahu is the current prime minister.",
            }],
        })

    async def fake_open(url):
        return ToolResult.ok("Opened source.", {
            "title": "Israel Prime Minister",
            "url": url,
            "text": "Benjamin Netanyahu is the current prime minister.",
        })

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", fake_open)
    fake_router.script.append(AIMessage(
        content=(
            "<thinking>I'll search for the current officeholder.</thinking>"
            "<syntax_call><default_api:search_web "
            'query="current prime minister of Israel" /></syntax_call>'
        )
    ))
    headers = _token(client, "pseudo-tool-output@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who is the current prime minister of Israel?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert "Benjamin Netanyahu is the current prime minister." in data["reply"]
    assert "thinking" not in data["reply"].lower()
    assert "syntax_call" not in data["reply"]
    assert "default_api" not in data["reply"]
    assert data["sources"][0]["url"] == "https://news.example.test/israel-pm"


def test_current_factual_question_replaces_agent_guard_failure_with_search_result(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one current source.", {
            "results": [{
                "title": "Israel Prime Minister",
                "url": "https://news.example.test/israel-pm",
                "snippet": "Benjamin Netanyahu is the current prime minister.",
            }],
        })

    async def fake_open(url):
        return ToolResult.ok("Opened source.", {
            "title": "Israel Prime Minister",
            "url": url,
            "text": "Benjamin Netanyahu is the current prime minister.",
        })

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", fake_open)
    fake_router.script.append(AIMessage(
        content=(
            "I did not execute the requested actions because one or more "
            "tool calls did not meet the task's capability, argument, or plan "
            "requirements. No requested actions were carried out."
        )
    ))
    headers = _token(client, "search-guard-failure@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who is the current prime minister of Israel?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert "Live web results" in reply
    assert "Benjamin Netanyahu is the current prime minister." in reply
    assert "No requested actions were carried out" not in reply


def test_search_stream_never_emits_private_markup_and_finishes_with_live_evidence(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one current source.", {
            "results": [{
                "title": "Israel Prime Minister",
                "url": "https://news.example.test/israel-pm",
                "snippet": "Benjamin Netanyahu is the current prime minister.",
            }],
        })

    async def fake_open(url):
        return ToolResult.ok("Opened source.", {
            "title": "Israel Prime Minister",
            "url": url,
            "text": "Benjamin Netanyahu is the current prime minister.",
        })

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", fake_open)
    fake_router.script.append(AIMessage(
        content=(
            "<thinking>Internal reasoning.</thinking>"
            "<syntax_call><default_api:search_web "
            'query="current prime minister of Israel" /></syntax_call>'
        )
    ))
    headers = _token(client, "search-stream-output@test.com")

    with client.stream(
        "POST",
        "/api/chat",
        json={
            "message": "Who is the current prime minister of Israel?",
            "stream": True,
        },
        headers=headers,
    ) as response:
        assert response.status_code == 200
        text = "\n".join(response.iter_lines())

    done = _done_payload(text)
    assert done is not None
    assert "Benjamin Netanyahu is the current prime minister." in done["reply"]
    assert "thinking" not in text.lower()
    assert "syntax_call" not in text
    assert "default_api" not in text


def test_current_factual_question_labels_unopened_search_snippets(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one current source.", {
            "results": [{
                "title": "Israel election report",
                "url": "https://news.example.test/israel-election",
                "snippet": "The election will determine whether Benjamin Netanyahu remains prime minister.",
            }],
        })

    async def failed_open(url):
        return ToolResult.fail(
            "HTTP_ERROR", "HTTP 403",
            message="The source page could not be opened.",
        )

    fake_router.script.append(AIMessage(
        content="Search snippets mention the election, but I cannot verify its outcome."
    ))
    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", failed_open)
    headers = _token(client, "unopened-search-source@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who is the current prime minister of Israel?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    prompt = "\n".join(
        str(getattr(message, "content", ""))
        for message in fake_router.tape[-1]
    )
    assert "No result pages could be opened" in prompt
    assert "answer only if a snippet directly supports" in prompt
    assert "do not treat a related headline or contextual mention as verification" in prompt
    assert response.json()["sources"][0]["url"] == (
        "https://news.example.test/israel-election"
    )


def test_current_factual_question_reports_live_search_failure(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def failed_search(query, max_results=5):
        return ToolResult.fail(
            "SEARCH_FAILED", "provider unavailable",
            message="The live search provider is unavailable.",
        )

    monkeypatch.setattr(web, "search_web", failed_search)
    fake_router.script.append(AIMessage(
        content="As of my training data, the officeholder may be X."
    ))
    headers = _token(client, "live-search-failure@test.com")

    response = client.post(
        "/api/chat",
        json={"message": "Who is the current prime minister of Israel?"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert "live web search" in reply
    assert "provider is unavailable" in reply
    assert "officeholder may be X" not in reply


def test_current_cbr_is_partial_without_recent_official_evidence(
        client, fake_router, monkeypatch):
    from app.tools import web

    async def fake_search(query, max_results=5):
        return ToolResult.ok("Found one result.", {
            "results": [{
                "title": "CBR history",
                "url": "https://finance.example.test/cbr",
                "snippet": "The CBR was 8.75 percent.",
            }],
        })

    async def fake_open(url):
        return ToolResult.ok("Opened result.", {
            "title": "CBR history",
            "url": url,
            "text": "The CBR was 8.75 percent.",
        })

    monkeypatch.setattr(web, "search_web", fake_search)
    monkeypatch.setattr(web, "open_url", fake_open)
    fake_router.script.append(AIMessage(content="The current CBR is 8.75%."))

    response = client.post(
        "/api/chat",
        json={"message": (
            "What is the current CBR rate announced by the Central Bank of "
            "Kenya Monetary Policy Committee? Give the exact percentage."
        )},
        headers=_token(client, "unverified-cbr@test.com"),
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "partial"
    assert "could not verify the current CBR" in data["reply"]
    assert "8.75%" not in data["reply"]


def test_search_fallback_prefers_relevant_opened_source():
    from app.services.chat_service import _format_web_search_evidence

    reply = _format_web_search_evidence(
        "Who is the current prime minister of Israel?",
        [
            {
                "title": "Israel political system",
                "url": "https://news.example.test/system",
                "snippet": "The president is head of state.",
                "source_type": "search_result",
            },
            {
                "title": "Benjamin Netanyahu current role",
                "url": "https://news.example.test/pm",
                "snippet": "Benjamin Netanyahu currently serves as Prime Minister of Israel.",
                "source_type": "page",
            },
        ],
    )

    assert "Benjamin Netanyahu currently serves as Prime Minister of Israel." in reply
    assert "https://news.example.test/pm" in reply
    assert "https://news.example.test/system" not in reply
    assert "could not open" not in reply


def test_kenya_president_fallback_only_uses_official_kenyan_presidency_source():
    from app.services.chat_service import _format_web_search_evidence

    reply = _format_web_search_evidence(
        "Who is the president of Kenya?",
        [
            {
                "title": "President Donald J. Trump",
                "url": "https://www.whitehouse.gov/administration/donald-j-trump/",
                "snippet": "President Donald J. Trump leads the United States.",
                "source_type": "page",
            },
            {
                "title": "Office of the President",
                "url": (
                    "https://www.president.go.ke/administration/"
                    "office-of-the-president/"
                ),
                "snippet": (
                    "William Samoei Ruto is the President of the Republic "
                    "of Kenya."
                ),
                "source_type": "page",
            },
        ],
    )

    assert "William Samoei Ruto is the President of the Republic of Kenya." in reply
    assert "president.go.ke" in reply
    assert "whitehouse.gov" not in reply


def test_kenya_president_fallback_rejects_unrelated_results():
    from app.services.chat_service import _format_web_search_evidence

    reply = _format_web_search_evidence(
        "Who is the president of Kenya?",
        [{
            "title": "President Donald J. Trump",
            "url": "https://www.whitehouse.gov/",
            "snippet": "President Donald J. Trump leads the United States.",
            "source_type": "page",
        }],
    )

    assert "did not contain enough evidence from Kenya's official Presidency site" in reply
    assert "whitehouse.gov" not in reply


def test_current_cbr_fallback_extracts_rate_from_latest_official_release():
    from app.services.chat_service import _format_web_search_evidence

    reply = _format_web_search_evidence(
        "What is the current Central Bank Rate (CBR) announced by the Central "
        "Bank of Kenya Monetary Policy Committee? Give the exact percentage "
        "and cite the official CBK announcement.",
        [
            {
                "title": "MPC lowers the CBR to 9.50 percent",
                "url": "https://www.centralbank.go.ke/2025/08/13/11519/",
                "snippet": (
                    "The Monetary Policy Committee lowered the CBR to "
                    "9.50 percent."
                ),
                "source_type": "page",
            },
            {
                "title": "MPC retains the CBR at 8.75 percent",
                "url": (
                    "https://www.centralbank.go.ke/"
                    "2026/10/07/mpc-retains-the-cbr-at-8-75-percent-3/"
                ),
                "snippet": (
                    "The Monetary Policy Committee retained the Central Bank "
                    "Rate (CBR) at 8.75 percent at its October 7, 2026 meeting."
                ),
                "source_type": "page",
            },
        ],
    )

    assert "Central Bank Rate at **8.75%**" in reply
    assert "October 7, 2026" in reply
    assert "https://www.centralbank.go.ke/2026/10/07/" in reply
    assert "Live web results for" not in reply


def test_current_cbr_fallback_will_not_claim_a_rate_without_recent_official_evidence():
    from app.services.chat_service import _format_web_search_evidence

    reply = _format_web_search_evidence(
        "What is the current CBR rate announced by the MPC?",
        [{
            "title": "CBR history",
            "url": "https://finance.example.test/cbr",
            "snippet": "The CBR was 8.75 percent.",
            "source_type": "page",
        }],
    )

    assert "CBR at **" not in reply
    assert "8.75%" not in reply


def test_numeric_web_fallback_quotes_relevant_retrieved_evidence():
    from app.services.chat_service import _format_web_search_evidence

    reply = _format_web_search_evidence(
        "What is the current USD to KES exchange rate?",
        [{
            "title": "USD to KES exchange rate",
            "url": "https://rates.example.test/usd-kes",
            "snippet": "The USD to KES exchange rate is 129.45.",
            "source_type": "page",
        }],
    )

    assert '"The USD to KES exchange rate is 129.45."' in reply
    assert "https://rates.example.test/usd-kes" in reply


def test_numeric_answer_requires_cited_source_to_support_its_numbers():
    from app.services.chat_service import _reply_numeric_claims_grounded

    sources = [{
        "title": "Current lending rate",
        "url": "https://rates.example.test/current",
        "snippet": "The current lending rate is 15.75% effective 2026-10-01.",
    }]

    assert _reply_numeric_claims_grounded(
        "The lending rate is 15.75% effective 2026-10-01. "
        "[Source](https://rates.example.test/current)",
        sources,
    )
    assert not _reply_numeric_claims_grounded(
        "The lending rate is 16.25%. "
        "[Source](https://rates.example.test/current)",
        sources,
    )


def test_chat_uses_persisted_preferred_provider(client, fake_router, monkeypatch):
    from app.services import chat_service

    fake_router.script.append(AIMessage(content="Routed through the preference."))
    headers = _token(client, "preferred-provider-chat@test.com")
    settings = client.patch(
        "/api/settings",
        json={"preferred_provider": "lmstudio"},
        headers=headers,
    )
    assert settings.status_code == 200

    routed_overrides: list[str | None] = []
    fallback_policy: list[tuple[bool, str | None]] = []
    original_route = fake_router.route
    original_fallbacks = chat_service._fallback_models_for_turn

    def capture_route(**kwargs):
        routed_overrides.append(kwargs.get("provider_override"))
        return original_route(**kwargs)

    def capture_fallbacks(*args, **kwargs):
        fallback_policy.append(
            (kwargs["provider_locked"], kwargs["selected_model"])
        )
        return original_fallbacks(*args, **kwargs)

    fake_router.route = capture_route
    monkeypatch.setattr(
        chat_service, "_fallback_models_for_turn", capture_fallbacks,
    )
    response = client.post(
        "/api/chat", json={"message": "Hello"}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["reply"] == "Routed through the preference."
    assert routed_overrides == ["lmstudio"]
    assert fallback_policy == [(False, None)]


def test_conversation_management_is_owned_searchable_and_archivable(client):
    headers = _token(client, "conversation-management@test.com")
    first = client.post("/api/conversations", json={"title": "Quarterly planning"}, headers=headers).json()
    second = client.post("/api/conversations", json={"title": "Personal notes"}, headers=headers).json()

    renamed = client.patch(
        f"/api/conversations/{first['id']}",
        json={"title": "Quarterly delivery planning"}, headers=headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Quarterly delivery planning"

    search = client.get("/api/conversations?q=delivery", headers=headers)
    assert [row["id"] for row in search.json()] == [first["id"]]

    archived = client.patch(
        f"/api/conversations/{first['id']}",
        json={"archived": True}, headers=headers,
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert all(row["id"] != first["id"] for row in client.get("/api/conversations", headers=headers).json())
    assert any(row["id"] == first["id"] for row in client.get(
        "/api/conversations?include_archived=true", headers=headers
    ).json())

    other = _token(client, "conversation-management-other@test.com")
    forbidden = client.patch(
        f"/api/conversations/{second['id']}", json={"title": "leak"}, headers=other,
    )
    assert forbidden.status_code == 404


def test_timezone_preference_defaults_to_nairobi_and_accepts_iana_names(client):
    headers = _token(client, "timezone@test.com")
    initial = client.get("/api/settings", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["timezone"] == "Africa/Nairobi"

    updated = client.patch(
        "/api/settings", json={"timezone": "America/New_York"}, headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["timezone"] == "America/New_York"

    invalid = client.patch(
        "/api/settings", json={"timezone": "Not/A_Timezone"}, headers=headers,
    )
    assert invalid.status_code == 422


def test_chat_streams_sse(client, fake_router):
    from langchain_core.messages import AIMessage

    fake_router.script.append(AIMessage(content="Streamed reply"))
    headers = _token(client, "stream@test.com")

    with client.stream("POST", "/api/chat",
                       json={"message": "hello", "stream": True}, headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "\n".join(resp.iter_lines())

    assert "event: meta" in text
    assert "event: token" in text
    assert "run_metrics" in text
    assert "Streamed reply" in text
    assert "event: done" in text
    done_line = [l for l in text.splitlines() if l.startswith("data:") and "Streamed reply" in l]
    assert done_line, "done event must carry the final reply"


def test_chat_streams_ollama_route_event(client, fake_router, monkeypatch):
    """An Ollama-routed turn surfaces an `ollama_route` monitoring event."""
    from app.services import chat_service, ollama_memory
    from app.services import model_router as mr

    ollama_spec = mr.ProviderSpec(
        "ollama", "openai", "http://localhost:11434/v1", None,
        "qwen2.5:7b", "qwen2.5:7b", requires_key=False,
    )
    fake_router.route = lambda **k: mr.TaskRoute(
        provider=ollama_spec, model="qwen2.5:7b", reason="low-memory")
    monkeypatch.setattr(ollama_memory, "model_tiers",
                        lambda: ["qwen2.5:7b", "llama3.2:latest", "qwen2.5:1.5b", "qwen2.5:0.5b"])
    monkeypatch.setattr(ollama_memory, "tier_requirement_gb", lambda m: 4.0)
    monkeypatch.setattr(ollama_memory, "available_memory_gb", lambda: 5.0)
    monkeypatch.setattr(ollama_memory, "keep_alive_seconds_for", lambda m: 900)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(chat_service, "_maybe_warm_ollama", _noop)
    monkeypatch.setattr(chat_service, "_maybe_keep_ollama_alive", _noop)

    fake_router.script.append(AIMessage(content="local reply"))
    headers = _token(client, "ollama-route@test.com")
    with client.stream("POST", "/api/chat",
                       json={"message": "hi", "stream": True}, headers=headers) as resp:
        assert resp.status_code == 200
        text = "\n".join(resp.iter_lines())

    assert "event: ollama_route" in text
    assert '"tier": "heavy"' in text


def test_model_not_configured_returns_structured_503(client, app_module_overrides=None):
    from app.api.deps import get_router
    from app.errors import ModelNotConfiguredError
    from app.main import app

    class BrokenRouter:
        def route(self, **kwargs):
            raise ModelNotConfiguredError()

        def get_model(self, route):
            raise AssertionError("unreachable")

    headers = _token(client, "nokey@test.com")
    app.dependency_overrides[get_router] = lambda: BrokenRouter()
    try:
        resp = client.post("/api/chat", json={"message": "hi"}, headers=headers)
    finally:
        app.dependency_overrides.pop(get_router, None)

    assert resp.status_code == 503
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "MODEL_NOT_CONFIGURED"
    assert "api key" in body["error"]["details"].lower()


def test_conversation_isolation_between_users(client, fake_router):
    from langchain_core.messages import AIMessage

    fake_router.script.append(AIMessage(content="hi one"))
    fake_router.script.append(AIMessage(content="hi two"))
    h1 = _token(client, "one@test.com")
    h2 = _token(client, "two@test.com")

    r1 = client.post("/api/chat", json={"message": "hello"}, headers=h1).json()
    assert client.get(f"/api/conversations/{r1['conversation_id']}/messages",
                      headers=h2).status_code == 404


def test_activity_logged_for_chat(client, fake_router):
    from langchain_core.messages import AIMessage

    fake_router.script.append(AIMessage(content="done"))
    headers = _token(client, "act@test.com")
    client.post("/api/chat", json={"message": "hello"}, headers=headers)

    activity = client.get("/api/activity", headers=headers).json()
    assert len(activity) >= 1
    entry = next(a for a in activity if a["tool"] == "chat_turn")
    assert entry["success"] is True
    assert entry["duration_ms"] >= 0


def test_stop_all_agents_endpoint(client):
    headers = _token(client, "stop@test.com")
    resp = client.post("/api/agents/stop", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["stopped"] == 0


def test_stop_single_run_endpoint(client, fake_router):
    from app.services import run_registry

    fake_router.script.append(AIMessage(content="done"))
    headers = _token(client, "stoprun@test.com")
    data = client.post("/api/chat", json={"message": "hi"}, headers=headers).json()
    run_id = data["run_id"]
    assert run_id

    resp = client.post(f"/api/runs/{run_id}/stop", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "success": True,
        "run_id": run_id,
        "stopped": True,
        "outcome_reconciliation_required": False,
    }

    me = client.get("/api/auth/me", headers=headers).json()
    assert run_registry.is_stopped(me["id"], run_id) is True


def test_stop_single_run_requires_ownership(client, fake_router):
    fake_router.script.append(AIMessage(content="done"))
    owner = _token(client, "owner@test.com")
    data = client.post("/api/chat", json={"message": "hi"}, headers=owner).json()
    run_id = data["run_id"]

    intruder = _token(client, "intruder@test.com")
    resp = client.post(f"/api/runs/{run_id}/stop", headers=intruder)
    assert resp.status_code == 404

    unknown = client.post("/api/runs/does-not-exist/stop", headers=owner)
    assert unknown.status_code == 404

    assert client.post(f"/api/runs/{run_id}/stop").status_code == 401


def test_describe_model_error_is_friendly():
    """Failed tasks must surface user-friendly notifications, never raw
    exception text (spec §45)."""
    from app.errors import ModelEmptyResponseError
    from app.services.chat_service import _describe_model_error

    empty = _describe_model_error(ModelEmptyResponseError("groq"))
    assert empty["code"] == "MODEL_EMPTY_RESPONSE"
    assert "try again" in empty["details"].lower()

    raw = _describe_model_error(ValueError("boom-secret-traceback"))
    assert raw["code"] == "MODEL_ERROR"
    assert "boom-secret-traceback" not in raw["details"]
    assert "ValueError" not in raw["details"]


def test_describe_model_error_ollama_specific(monkeypatch):
    """Local Ollama failures get actionable, non-leaky messages (stage 6)."""
    import httpx

    from app.services.chat_service import _describe_model_error

    def _status_error(body: str, code: int = 500) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
        response = httpx.Response(code, request=request, text=body)
        return httpx.HTTPStatusError("err", request=request, response=response)

    offline = _describe_model_error(
        httpx.ConnectError("Connection refused"),
        provider="ollama", model="qwen2.5:7b")
    assert offline["code"] == "OLLAMA_OFFLINE"
    assert "ollama" in offline["details"].lower()

    missing = _describe_model_error(
        _status_error('{"error":"model \'qwen2.5:7b\' not found"}'),
        provider="ollama", model="qwen2.5:7b")
    assert missing["code"] == "OLLAMA_MODEL_MISSING"
    assert "ollama pull qwen2.5:7b" in missing["details"]

    oom = _describe_model_error(
        _status_error("model requires more system memory (5.0 GiB) than is available"),
        provider="ollama", model="qwen2.5:7b")
    assert oom["code"] == "OLLAMA_OUT_OF_MEMORY"
    assert "memory" in oom["details"].lower()

    # A bare RuntimeError from the streaming path is still classified by body.
    stream = _describe_model_error(
        RuntimeError("ollama API error 500: model 'nope' not found"),
        provider="ollama", model="nope")
    assert stream["code"] == "OLLAMA_MODEL_MISSING"

    # Non-Ollama provider HTTP errors keep their generic mapping.
    cloud = _describe_model_error(
        _status_error("nope", code=429), provider="groq", model="llama-3.1-8b")
    assert cloud["code"] == "MODEL_RATE_LIMITED"


# --------------------------------------------------------------------------- #
# Monitoring: run_metrics + ollama_route + activity meta (stage 8)             #
# --------------------------------------------------------------------------- #
def test_ollama_tier_and_model_meta_helpers(monkeypatch):
    from app.services import chat_service, ollama_memory
    from app.services import model_router as mr

    monkeypatch.setattr(ollama_memory, "model_tiers",
                        lambda: ["a:7b", "b:latest", "c:1.5b", "d:0.5b"])
    assert chat_service._ollama_tier("a:7b") == "heavy"
    assert chat_service._ollama_tier("b:latest") == "mid"
    assert chat_service._ollama_tier("c:1.5b") == "light"
    assert chat_service._ollama_tier("d:0.5b") == "survival"
    assert chat_service._ollama_tier("not-a-tier") == "unknown"

    ollama_route = mr.TaskRoute(
        provider=mr.ProviderSpec("ollama", "openai", "x", None, "a:7b", "a:7b",
                                 requires_key=False),
        model="a:7b", reason="mem-ok")
    assert chat_service._model_meta(ollama_route) == {
        "provider": "ollama", "ollama_tier": "heavy"}

    cloud = mr.TaskRoute(
        provider=mr.ProviderSpec("groq", "openai", "x", "k", "m", "m"),
        model="m", reason="cloud")
    assert chat_service._model_meta(cloud) == {"provider": "groq"}


def test_graph_emits_run_metrics(fake_router):
    """Every streamed model reply records first-token latency for monitoring."""
    import asyncio

    from langchain_core.messages import HumanMessage

    from app.agents.graph import build_graph
    from app.events import ListEmitter, set_emitter
    from app.tools import registry

    fake_router.script.append(AIMessage(content="hello world"))
    emitter = ListEmitter()
    set_emitter(emitter)

    async def _go():
        graph = build_graph(
            model=fake_router.model, registry=registry, max_steps=4,
            tool_timeout=5.0, max_tool_calls=4, max_retries=1, system_prompt="sys")
        await graph.ainvoke({
            "messages": [HumanMessage(content="hi", id="u1")],
            "steps": 0, "sources": [], "pending_approval": None,
            "approved_call_ids": [], "rejected_call_ids": [],
            "status": "running", "run_id": "", "user_id": "",
            "task_id": "", "plan": None, "tool_calls": 0, "policy": {},
        })

    try:
        asyncio.run(_go())
    finally:
        set_emitter(None)

    events = emitter.of_type("run_metrics")
    assert events
    assert events[0]["first_token_ms"] is not None
    assert events[0]["first_token_ms"] >= 0
    assert events[0]["stream_ms"] >= 0


# --------------------------------------------------------------------------- #
# Multi-purpose visual output: math/diagram/citations wiring (spec §visual)     #
# --------------------------------------------------------------------------- #
def _done_payload(text: str) -> dict | None:
    """Pull the JSON body of the SSE `done` event out of a streamed response."""
    import json as _json
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                obj = _json.loads(line[len("data:"):].strip())
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "done":
                return obj
    return None


def test_sse_done_event_carries_visual_keys(client, fake_router):
    """Any finished turn must surface citations/diagrams keys in the `done` frame,
    and a ```svg fence must produce a non-empty diagrams list end-to-end."""
    fake_router.script.append(AIMessage(
        content="```svg\n<svg><rect width=\"10\"/></svg>\n```"
    ))
    headers = _token(client, "vis-sse@test.com")

    with client.stream("POST", "/api/chat",
                       json={"message": "draw a box", "stream": True},
                       headers=headers) as resp:
        assert resp.status_code == 200
        text = "\n".join(resp.iter_lines())

    assert "event: done" in text
    done = _done_payload(text)
    assert done is not None
    # Both visual keys MUST ride along on the SSE `done` payload.
    assert "citations" in done and "diagrams" in done
    assert done["diagrams"], "diagrams must be non-empty for an svg-fenced reply"
    assert done["diagrams"][0]["kind"] == "svg"
    assert "<rect" in done["diagrams"][0]["content"]


def test_sse_done_event_streams_research_citations(client, fake_router):
    """A research turn that calls search_web must renumber a tracked source link
    to [1] and surface the structured citation on the SSE `done` event."""
    from langchain_core.messages import AIMessage

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "search_web",
        "args": {"query": "laptops", "max_results": 2},
        "id": "call_sse_cite_1",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(
        content="See [Best Laptops](https://example.invalid/laptops) for specs."
    ))
    headers = _token(client, "cite-sse@test.com")

    with client.stream("POST", "/api/chat",
                       json={"message": "Research laptops", "stream": True},
                       headers=headers) as resp:
        assert resp.status_code == 200
        text = "\n".join(resp.iter_lines())

    done = _done_payload(text)
    assert done is not None, "done event must be present"
    assert done["citations"], "research turn must surface numbered citations"
    c0 = done["citations"][0]
    assert c0["index"] == 1
    assert c0["url"] == "https://example.invalid/laptops"
    assert c0["title"] == "Mock result about laptops"
    assert "[1](https://example.invalid/laptops)" in done["reply"]
    assert "Best Laptops" not in done["reply"]  # title rewritten away


def test_non_stream_chat_response_roundtrips_citations_and_diagrams(
    client, fake_router
):
    """Non-stream replies must round-trip both citations (renumbered) and
    diagrams through ChatResponse.from_outcome — the shared path the UI relies on."""
    from langchain_core.messages import AIMessage

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "search_web",
        "args": {"query": "laptops", "max_results": 2},
        "id": "call_ns_cite_1",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(
        content=("See [Best Laptops](https://example.invalid/laptops) for specs. "
                 "```svg\n<svg><rect/></svg>\n```")
    ))
    headers = _token(client, "vis-ns@test.com")

    data = client.post(
        "/api/chat",
        json={"message": "Research laptops"},
        headers=headers,
    ).json()
    assert data["status"] == "completed"

    # citations renumbered + structured
    assert data["citations"], "citations must round-trip on non-stream response"
    c0 = data["citations"][0]
    assert c0["index"] == 1
    assert c0["url"] == "https://example.invalid/laptops"
    assert c0["title"] == "Mock result about laptops"
    assert "[1](https://example.invalid/laptops)" in data["reply"]
    assert "Best Laptops" not in data["reply"]

    # diagrams extracted + structured
    assert data["diagrams"], "diagrams must round-trip on non-stream response"
    d0 = data["diagrams"][0]
    assert d0["kind"] == "svg"
    assert "<rect" in d0["content"]


def test_chat_response_from_outcome_roundtrips_visual_fields():
    """Direct round-trip of a fabricated ChatOutcome through from_outcome —
    exercises the schema mapping independent of the chat graph."""
    from app.schemas import ChatResponse
    from app.services.chat_service import ChatOutcome

    outcome = ChatOutcome(
        status="completed",
        conversation_id="conv-1",
        run_id="run-1",
        task_id="task-1",
        reply="See [1](https://example.invalid/a).\n",
        sources=[{"url": "https://example.invalid/a", "title": "A",
                  "snippet": "snipped"}],
        intent="research",
        citations=[{"index": 1, "title": "A",
                    "url": "https://example.invalid/a", "snippet": "snipped"}],
        diagrams=[{"id": "diag-0", "kind": "svg", "title": "",
                   "content": "<svg/>"}],
    )
    resp = ChatResponse.from_outcome(outcome)
    assert resp.citations and resp.citations[0].url == "https://example.invalid/a"
    assert resp.citations[0].index == 1
    assert resp.diagrams and resp.diagrams[0].kind == "svg"
    assert resp.diagrams[0].id == "diag-0"
    assert resp.diagrams[0].content == "<svg/>"


def test_load_history_truncates_long_tool_results(fresh_db):
    """Stale tool results are trimmed when reloaded so small local models never
    see a wall of old output (the marker keeps the truncation honest)."""
    import uuid

    from langchain_core.messages import ToolMessage

    from app.database.session import SessionLocal
    from app.models.conversation import Conversation, Message
    from app.models.user import User
    from app.services.chat_service import _load_history

    with SessionLocal() as db:
        user = User(email=f"trunc-{uuid.uuid4().hex}@test.com",
                    password_hash="x", name="Trunc")
        db.add(user)
        db.commit()
        db.refresh(user)
        conv = Conversation(user_id=user.id, title="Big tool")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(Message(
            conversation_id=conv.id, role="tool",
            content="x" * 10_000, meta={"call_id": "call-1"},
        ))
        db.add(Message(
            conversation_id=conv.id, role="user", content="hi",
        ))
        db.commit()

        loaded = _load_history(db, conv.id)
        assert len(loaded) == 2
        tool = next(m for m in loaded if isinstance(m, ToolMessage))
        assert len(tool.content) <= 4000 + 100
        assert "truncated" in tool.content
        assert tool.tool_call_id == "call-1"


def test_load_history_keeps_small_tool_results(fresh_db):
    import uuid

    from langchain_core.messages import ToolMessage

    from app.database.session import SessionLocal
    from app.models.conversation import Conversation, Message
    from app.models.user import User
    from app.services.chat_service import _load_history

    with SessionLocal() as db:
        user = User(email=f"keep-{uuid.uuid4().hex}@test.com",
                    password_hash="x", name="Keep")
        db.add(user)
        db.commit()
        db.refresh(user)
        conv = Conversation(user_id=user.id, title="Small tool")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        db.add(Message(
            conversation_id=conv.id, role="tool",
            content="short result", meta={"call_id": "call-2"},
        ))
        db.commit()

        loaded = _load_history(db, conv.id)
        tool = next(m for m in loaded if isinstance(m, ToolMessage))
        assert tool.content == "short result"


def test_public_profile_evidence_renderer_is_grounded_and_clickable():
    from app.services.chat_service import _format_public_profile_evidence

    reply = _format_public_profile_evidence(
        "Example Person",
        [{
            "title": "Public profile",
            "url": "https://example.com/profile",
            "snippet": "Public result excerpt.",
        }],
    )
    assert "[Public profile](https://example.com/profile)" in reply
    assert "namesakes" in reply
    assert "private" not in reply.lower()
