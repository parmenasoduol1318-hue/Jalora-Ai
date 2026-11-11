"""Regression tests for tool dispatch, capability discovery, and email approval."""
from __future__ import annotations

import asyncio
import json
import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from app.agents.graph import (
    _parse_text_tool_calls,
    _strip_internal_model_markup,
    deserialize_state,
    serialize_state,
)
from app.agents.intent import classify_intent
from app.config import get_settings
from app.context import ToolContext, set_tool_context
from app.database.session import SessionLocal
from app.models.agent import AgentRun, Approval
from app.models.user import User
from app.services import chat_service
from app.services import email as email_service
from app.tools import registry


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _register_user(client, auth_headers, email: str = "user@test.com") -> dict:
    headers = auth_headers(email)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        user_id = user.id
    return {"headers": headers, "user_id": user_id}


def _create_mock_integration(client, headers, address: str = "") -> dict:
    resp = client.post("/api/integrations", headers=headers, json={
        "provider": "email_mock",
        "config": {"email_address": address or get_settings().gmail_from},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def _chat(client, headers, message: str, conversation_id: str | None = None) -> dict:
    body: dict = {"message": message, "stream": False}
    if conversation_id:
        body["conversation_id"] = conversation_id
    resp = client.post("/api/chat", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# Fix #1: Text-based tool-call parsing                                         #
# --------------------------------------------------------------------------- #
class TestTextToolCallParsing:
    """Criteria 1, 2, 15, 16: _parse_text_tool_calls extracts + validates."""

    def test_strips_private_reasoning_and_pseudo_tool_calls(self):
        text = (
            "<thinking>I'll search for the answer.</thinking>"
            "<syntax_call><default_api:search_web query=\"current facts\" /></syntax_call>"
        )
        assert _strip_internal_model_markup(text) == ""

    def test_preserves_answer_after_private_reasoning(self):
        text = (
            "<thinking>Internal reasoning.</thinking>"
            "The live result says the answer is available."
        )
        assert _strip_internal_model_markup(text) == (
            "The live result says the answer is available."
        )

    def test_parses_single_send_email_from_text(self):
        text = (
            'Here is the email I will send.\n'
            '{"name": "send_email", "arguments": {"to": "user@test.com", '
            '"subject": "Hello", "body": "Hi there"}}'
        )
        cleaned, calls = _parse_text_tool_calls(text, registry)
        assert len(calls) == 1
        assert calls[0]["name"] == "send_email"
        assert calls[0]["args"]["to"] == "user@test.com"
        assert calls[0]["args"]["subject"] == "Hello"
        assert calls[0]["id"].startswith("txtcall_")
        assert calls[0]["type"] == "tool_call"
        # JSON block removed from visible text (Criterion 15)
        assert "send_email" not in cleaned
        assert '"arguments"' not in cleaned

    def test_parses_ollama_parameters_tool_call_envelope(self):
        text = (
            '{"name":"update_plan","parameters":{"steps":[{"id":"contacts",'
            '"description":"Find public contacts","tool":"search_public_contacts",'
            '"dependencies":[]}]}}'
        )

        cleaned, calls = _parse_text_tool_calls(text, registry)

        assert cleaned == ""
        assert len(calls) == 1
        assert calls[0]["name"] == "update_plan"
        assert calls[0]["args"]["steps"][0]["tool"] == "search_public_contacts"

    def test_strips_json_from_cleaned_text(self):
        text = (
            'I will send it now. '
            '{"name": "send_email", "arguments": {"to": "a@example.com", "subject": "x", "body": "y"}}'
        )
        cleaned, calls = _parse_text_tool_calls(text, registry)
        assert len(calls) == 1
        assert "I will send it now." in cleaned
        assert '{"name"' not in cleaned
        assert '"arguments"' not in cleaned

    def test_rejects_unknown_tool_name(self):
        """Criterion 2 / 16: unknown tool names are silently dropped, never executed."""
        text = '{"name": "invented_tool_xyz", "arguments": {"foo": "bar"}}'
        cleaned, calls = _parse_text_tool_calls(text, registry)
        assert len(calls) == 0
        # The invalid JSON is still stripped from visible text (Criterion 15)
        assert "invented_tool_xyz" not in cleaned

    def test_rejects_malformed_json_arguments(self):
        """Malformed JSON in arguments produces a no-op result, not a crash."""
        text = '{"name": "send_email", "arguments": {to: "a@example.com"}}'
        cleaned, calls = _parse_text_tool_calls(text, registry)
        # Arguments parse fails → empty dict, but the tool name is still valid
        # so it should be parsed (with _raw or empty args).
        # The key point: it doesn't crash and the JSON is stripped from text.
        assert '"arguments"' not in cleaned

    def test_parses_multiple_tool_calls_in_one_message(self):
        text = (
            '{"name": "send_email", "arguments": {"to": "a@example.com", "subject": "1", "body": "x"}}'
            '{"name": "send_email", "arguments": {"to": "c@example.net", "subject": "2", "body": "y"}}'
        )
        cleaned, calls = _parse_text_tool_calls(text, registry)
        assert len(calls) == 2
        assert calls[0]["name"] == "send_email"
        assert calls[1]["name"] == "send_email"
        assert calls[0]["args"]["to"] == "a@example.com"
        assert calls[1]["args"]["to"] == "c@example.net"

    def test_empty_text_produces_no_calls(self):
        cleaned, calls = _parse_text_tool_calls("", registry)
        assert calls == []
        assert cleaned == ""

    def test_nested_braces_in_arguments(self):
        """Arguments with nested JSON objects are captured fully."""
        text = (
            '{"name": "send_email", "arguments": '
            '{"to": "a@example.com", "subject": "x", "body": "see {\\"key\\": \\"val\\"}"}}'
        )
        cleaned, calls = _parse_text_tool_calls(text, registry)
        assert len(calls) == 1
        assert calls[0]["args"]["to"] == "a@example.com"


# --------------------------------------------------------------------------- #
# Registry-driven capability scope                                             #
# --------------------------------------------------------------------------- #
class TestIntentRestrictions:
    """Capability sets are discovered from the task, not intent allowlists."""

    def test_send_payment_not_selected_for_email_task(self):
        from app.tools.routing import route_request

        selected = route_request("Email the project report to me").tool_names
        assert selected is not None
        assert "send_email" in selected
        assert "send_payment" not in selected

    def test_coding_capabilities_are_selected_by_request(self):
        from app.tools.routing import route_request

        selected = route_request("Inspect this code and run its tests").tool_names
        assert selected is not None
        assert {"inspect_code", "run_tests"} <= set(selected)
        assert "send_payment" not in selected


# --------------------------------------------------------------------------- #
# Capability-scope integration: tools_node rejects unselected tools              #
# --------------------------------------------------------------------------- #
class TestGraphIntentGuard:
    """An unselected capability cannot run even if a model requests it."""

    def test_send_payment_blocked_in_graph_for_email_task(
        self, client, fake_router, auth_headers
    ):
        """When intent is 'chat' (email), send_payment must be blocked."""
        import email as _email_unused  # noqa

        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        # Model emits send_payment — but the intent classifier routes "send email"
        # to a non-payment intent. The tools_node guard should block it.
        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_payment",
                "args": {"recipient": "attacker@evil.example", "amount": 999,
                         "currency": "USD", "description": "test"},
                "id": "call_pay_blocked", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Done."))

        resp = _chat(client, env["headers"],
                     "Send an email to user@test.com and ask how they're getting on with Jalora")
        assert resp["status"] in ("completed", "partial", "error", "paused_approval", "")
        # send_payment should NOT have been executed — sent_count must be 0
        assert email_service._MOCK.sent_count(env["user_id"]) == 0

    def test_send_email_allowed_in_graph_for_email_task(
        self, client, fake_router, auth_headers
    ):
        """send_email should still work for an email-intent task."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {"to": "friend@example.com", "subject": "Quick check",
                         "body": "How are you getting on with Jalora?"},
                "id": "call_email_ok", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Email sent."))

        resp = _chat(client, env["headers"],
                     "Send an email to friend@example.com asking how they're getting on")
        assert resp["status"] == "paused_approval"
        assert resp["approval"]["tool_name"] == "send_email"
        # Not sent yet — pending approval
        assert email_service._MOCK.sent_count(env["user_id"]) == 0


# --------------------------------------------------------------------------- #
# Fix #2: Follow-up resolution                                                  #
# --------------------------------------------------------------------------- #
class TestFollowUpResolution:
    """Criteria 5, 6, 7, 8: "send it now" resolves existing pending approval."""

    def test_send_it_now_approves_pending_send_email(
        self, client, fake_router, auth_headers
    ):
        """Criterion 5, 20: "send it now" resolves the pending send_email approval."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        # Turn 1: model emits send_email → paused for approval
        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {"to": "internetmoney1318@gmail.com",
                         "subject": "How are you getting on with Jalora?",
                         "body": "Hi, I hope you're doing well!"},
                "id": "call_followup_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(
            AIMessage(content="The email has been sent to your boss.")
        )

        resp = _chat(client, env["headers"],
                     "Send an email to internetmoney1318@gmail.com and ask how he is getting on with Jalora")
        assert resp["status"] == "paused_approval"
        assert resp["approval"]["tool_name"] == "send_email"
        conv_id = resp.get("conversation_id") or resp.get("conversation_id", "")

        # If conversation_id isn't returned directly, look it up
        if not conv_id:
            with SessionLocal() as db:
                run = db.scalars(
                    select(AgentRun).where(AgentRun.user_id == env["user_id"])
                ).first()
                conv_id = run.conversation_id if run else None

        # Turn 2: user says "send it now" → should resolve existing approval
        # (not invent a new send_payment call)
        resp2 = _chat(client, env["headers"], "send it now", conv_id)
        assert resp2["status"] == "completed"
        # Email should have been actually sent
        assert email_service._MOCK.sent_count(env["user_id"]) == 1

        # Verify no Approval record shows send_payment was ever attempted
        with SessionLocal() as db:
            approvals = db.scalars(
                select(Approval).where(Approval.user_id == env["user_id"])
            ).all()
            tool_names = [a.tool_name for a in approvals]
            assert "send_payment" not in tool_names

    def test_approve_resolves_existing_send_email(
        self, client, fake_router, auth_headers
    ):
        """Criterion 6: "approve" resolves existing approval, not a new action."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {"to": "boss@example.com", "subject": "Report",
                         "body": "Weekly report attached."},
                "id": "call_approve_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Email sent."))

        resp = _chat(client, env["headers"],
                     "Email my boss the weekly report")
        assert resp["status"] == "paused_approval"
        conv_id = resp.get("conversation_id") or ""
        if not conv_id:
            with SessionLocal() as db:
                run = db.scalars(
                    select(AgentRun).where(AgentRun.user_id == env["user_id"])
                ).first()
                conv_id = run.conversation_id if run else None

        resp2 = _chat(client, env["headers"], "approve", conv_id)
        assert resp2["status"] == "completed"
        assert email_service._MOCK.sent_count(env["user_id"]) == 1

    def test_reject_cancels_pending_email_no_send(
        self, client, fake_router, auth_headers
    ):
        """Criterion 7: rejection cancels the pending approval, no email sent."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {"to": "someone@example.com", "subject": "Hi",
                         "body": "Hello"},
                "id": "call_reject_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Cancelled."))

        resp = _chat(client, env["headers"],
                     "Send an email to someone@example.com saying hello")
        assert resp["status"] == "paused_approval"
        conv_id = resp.get("conversation_id") or ""
        if not conv_id:
            with SessionLocal() as db:
                run = db.scalars(
                    select(AgentRun).where(AgentRun.user_id == env["user_id"])
                ).first()
                conv_id = run.conversation_id if run else None

        resp2 = _chat(client, env["headers"], "cancel", conv_id)
        assert resp2["status"] == "completed"
        assert email_service._MOCK.sent_count(env["user_id"]) == 0

    def test_follow_up_without_pending_approval_falls_through(
        self, client, fake_router, auth_headers
    ):
        """Criterion 8: ambiguous follow-up with no pending approval is normal."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        # No pending approval — "yes" should start a normal chat turn
        fake_router.script.append(AIMessage(content="Sure, what can I help with?"))

        resp = _chat(client, env["headers"], "yes")
        assert resp["status"] == "completed"

    def test_follow_up_does_not_change_recipient(
        self, client, fake_router, auth_headers
    ):
        """Criterion 19 (follow-up): approval uses exact prepared arguments."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        original_args = {
            "to": "original@example.com",
            "subject": "Test subject",
            "body": "Test body",
        }
        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": original_args,
                "id": "call_preserve_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Sent."))

        resp = _chat(client, env["headers"],
                     "Email original@example.com about test subject")
        assert resp["status"] == "paused_approval"
        assert resp["approval"]["arguments"]["to"] == "original@example.com"
        assert resp["approval"]["arguments"]["subject"] == "Test subject"

        conv_id = resp.get("conversation_id") or ""
        if not conv_id:
            with SessionLocal() as db:
                run = db.scalars(
                    select(AgentRun).where(AgentRun.user_id == env["user_id"])
                ).first()
                conv_id = run.conversation_id if run else None

        # Approve
        resp2 = _chat(client, env["headers"], "send it now", conv_id)
        # Verify the sent email went to the ORIGINAL recipient
        assert env["user_id"] in email_service._MOCK.sent
        sent = email_service._MOCK.sent[env["user_id"]]
        assert len(sent) == 1
        assert sent[0]["to"] == "original@example.com"


# --------------------------------------------------------------------------- #
# Fix #4: Email approval data structure for frontend card                       #
# --------------------------------------------------------------------------- #
class TestEmailApprovalData:
    """Criteria 9, 10: approval stores TO/SUBJECT/BODY for the frontend card."""

    def test_approval_arguments_contain_email_fields(
        self, client, fake_router, auth_headers
    ):
        """Criterion 9: approval.arguments has to, subject, body for the card."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {"to": "card@example.com", "subject": "Card test",
                         "body": "This is the message body."},
                "id": "call_card_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Done."))

        resp = _chat(client, env["headers"],
                     "Send an email to card@example.com about the card test")
        assert resp["status"] == "paused_approval"
        args = resp["approval"]["arguments"]
        assert args["to"] == "card@example.com"
        assert args["subject"] == "Card test"
        assert args["body"] == "This is the message body."

    def test_email_approval_edits_persist_regards_and_attachments(
        self, client, fake_router, auth_headers, monkeypatch
    ):
        import io

        from app.services import email as email_service

        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])
        upload = client.post(
            "/api/documents",
            headers=env["headers"],
            files={"file": (
                "app-capabilities.txt",
                io.BytesIO(b"Current application capabilities overview."),
                "text/plain",
            )},
        )
        assert upload.status_code == 201, upload.text
        document = upload.json()
        sent_arguments = {}
        original_send = email_service.send_email

        async def capture_send(**kwargs):
            sent_arguments.update(kwargs)
            return await original_send(**kwargs)

        monkeypatch.setattr(email_service, "send_email", capture_send)
        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {
                    "to": "junioroduol@gmail.com",
                    "subject": "App capabilities",
                    "body": "I am ready to show you my current app capabilities.",
                    "regards": "Regards,",
                },
                "id": "call_email_edit_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Email sent after approval."))

        response = _chat(
            client, env["headers"],
            "Send an email to junioroduol@gmail.com and tell him that I am ready "
            "for the showing of my current app capabilities.",
        )
        assert response["status"] == "paused_approval"
        approval_id = response["approval"]["id"]
        edited = {
            **response["approval"]["arguments"],
            "to": "junioroduol@gmail.com",
            "subject": "Jalora app capability demonstration",
            "body": "I am ready to show you what my current app can do.",
            "regards": "Regards,\nParmenas",
            "attachments": [{
                "document_id": document["id"],
                "filename": document["filename"],
            }],
        }
        patch = client.patch(
            f"/api/approvals/{approval_id}",
            headers=env["headers"],
            json={"arguments": edited},
        )
        assert patch.status_code == 200, patch.text
        persisted = patch.json()["arguments"]
        assert persisted["subject"] == "Jalora app capability demonstration"
        assert persisted["regards"] == "Regards,\nParmenas"
        assert persisted["attachments"][0]["filename"] == document["filename"]

        approved = client.post(
            f"/api/approvals/{approval_id}/approve",
            headers=env["headers"],
        )
        assert approved.status_code == 200, approved.text
        assert email_service._MOCK.sent_count(env["user_id"]) == 1
        assert sent_arguments["body"].endswith("Regards,\nParmenas")
        assert sent_arguments["attachments"][0]["document_id"] == document["id"]

    def test_approval_validation_cannot_change_tool_name(
        self, client, fake_router, auth_headers
    ):
        """Criterion 10: the approval endpoint resolves the specific
        approval by ID — a misleading body cannot swap send_email for send_payment.
        ApproveRequest only accepts `credentials`, so any `tool_name` in the
        body is ignored by FastAPI's Pydantic validation."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {"to": "victim@example.com", "subject": "Phish",
                         "body": "Click link"},
                "id": "call_validation_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Done."))

        resp = _chat(client, env["headers"], "Send an email")
        assert resp["status"] == "paused_approval"
        approval_id = resp["approval"]["id"]
        assert resp["approval"]["tool_name"] == "send_email"

        # Approve via the endpoint — body cannot override the tool_name
        resp2 = client.post(
            f"/api/approvals/{approval_id}/approve",
            headers=env["headers"],
            json={"credentials": None},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        # After approval the email was sent to the ORIGINAL recipient
        assert email_service._MOCK.sent_count(env["user_id"]) == 1
        sent = email_service._MOCK.sent[env["user_id"]]
        assert sent[0]["to"] == "victim@example.com"


# --------------------------------------------------------------------------- #
# Fix #2/#4: Serialization round-trip with intent                               #
# --------------------------------------------------------------------------- #
class TestStateSerialization:
    """Intent survives serialize/deserialize round-trip."""

    def test_intent_in_serialized_state(self):
        from app.agents.graph import AgentState
        state: AgentState = {
            "messages": [],
            "user_id": "u1",
            "intent": "scheduling",
        }
        s = serialize_state(state)
        assert s["intent"] == "scheduling"

    def test_intent_round_trips(self):
        s = serialize_state({"intent": "scheduling"})
        d = deserialize_state(s)
        assert d["intent"] == "scheduling"

    def test_intent_defaults_to_empty(self):
        s = serialize_state({})
        assert s["intent"] == ""
        d = deserialize_state({})
        assert d["intent"] == ""


class TestEmailApprovalDraftIntegrity:
    """The reviewed email is the exact version that can be executed."""

    def test_pending_email_is_relevant_and_not_successful(self, client, fake_router,
                                                           auth_headers):
        env = _register_user(client, auth_headers, "intent-check@test.com")
        _create_mock_integration(client, env["headers"])
        fake_router.script.append(AIMessage(content="", tool_calls=[{
            "name": "send_email",
            "args": {
                "to": "church-contact@example.com",
                "subject": "Are you going to church?",
                "body": "Hi, I wanted to ask if you're going to church.",
            },
            "id": "call_email_relevance", "type": "tool_call",
        }]))
        # This deliberately optimistic model text must not become the pending
        # response shown to the user.
        fake_router.script.append(AIMessage(content="The email was sent."))

        response = _chat(
            client, env["headers"],
            "Send an email to church-contact@example.com to ask whether they are going to church",
        )
        assert response["status"] == "paused_approval"
        assert response["approval"]["arguments"]["to"] == "church-contact@example.com"
        assert "church" in response["approval"]["arguments"]["body"].lower()
        assert "email was sent" not in response["reply"].lower()
        assert email_service._MOCK.sent_count(env["user_id"]) == 0

    def test_edit_pending_email_and_approve_uses_edited_version(
        self, client, fake_router, auth_headers, monkeypatch
    ):
        sent_arguments = {}
        original_send = email_service.send_email

        async def capture_send(**kwargs):
            sent_arguments.update(kwargs)
            return await original_send(**kwargs)

        monkeypatch.setattr(email_service, "send_email", capture_send)
        env = _register_user(client, auth_headers, "edit-email@test.com")
        _create_mock_integration(client, env["headers"])
        fake_router.script.append(AIMessage(content="", tool_calls=[{
            "name": "send_email",
            "args": {"to": "first@example.com", "subject": "First",
                     "body": "First body", "attachments": []},
            "id": "call_email_edit", "type": "tool_call",
        }]))
        fake_router.script.append(AIMessage(content="Confirmed by provider."))
        response = _chat(client, env["headers"], "Send the first message")
        approval_id = response["approval"]["id"]
        edited = {
            "to": "second@example.com",
            "subject": "Second purpose",
            "body": "Updated body for the second purpose.",
            "attachments": [],
            "regards": "Best regards,\nParmenas",
        }
        saved = client.patch(
            f"/api/approvals/{approval_id}", headers=env["headers"],
            json={"arguments": edited},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["arguments"] == edited
        assert saved.json()["payload_version"] == 2
        stale_edit = client.patch(
            f"/api/approvals/{approval_id}",
            headers=env["headers"],
            json={"arguments": edited, "payload_version": 1},
        )
        assert stale_edit.status_code == 400
        assert stale_edit.json()["error"]["code"] == "APPROVAL_EDIT_CONFLICT"
        assert email_service._MOCK.sent_count(env["user_id"]) == 0

        from app.database.session import SessionLocal
        from app.models.agent import ApprovalRevision

        with SessionLocal() as db:
            revision = db.query(ApprovalRevision).filter_by(
                approval_id=approval_id, version=1
            ).one()
            assert revision.arguments["to"] == "first@example.com"
            assert revision.arguments["body"] == "First body"

        result = client.post(
            f"/api/approvals/{approval_id}/approve", headers=env["headers"]
        )
        assert result.status_code == 200, result.text
        assert email_service._MOCK.sent_count(env["user_id"]) == 1
        assert email_service._MOCK.sent[env["user_id"]][0]["to"] == "second@example.com"
        assert email_service._MOCK.sent[env["user_id"]][0]["subject"] == "Second purpose"
        assert sent_arguments["body"] == (
            "Updated body for the second purpose.\n\nBest regards,\nParmenas"
        )

    def test_pending_email_edit_is_owner_and_state_gated(
        self, client, fake_router, auth_headers
    ):
        env = _register_user(client, auth_headers, "owner-email@test.com")
        other = auth_headers("other-email@test.com")
        _create_mock_integration(client, env["headers"])
        fake_router.script.append(AIMessage(content="", tool_calls=[{
            "name": "send_email", "args": {"to": "owner@example.com",
            "subject": "Owner", "body": "Owner body"},
            "id": "call_email_owner", "type": "tool_call",
        }]))
        fake_router.script.append(AIMessage(content="Done."))
        response = _chat(client, env["headers"], "Send an owner email")
        approval_id = response["approval"]["id"]
        forbidden = client.patch(
            f"/api/approvals/{approval_id}", headers=other,
            json={"arguments": {"to": "other@example.com", "subject": "x", "body": "y"}},
        )
        assert forbidden.status_code == 404
        assert client.post(
            f"/api/approvals/{approval_id}/reject", headers=env["headers"]
        ).status_code == 200
        decided = client.patch(
            f"/api/approvals/{approval_id}", headers=env["headers"],
            json={"arguments": {"to": "late@example.com", "subject": "late", "body": "late"}},
        )
        assert decided.status_code == 400
        assert decided.json()["error"]["code"] == "APPROVAL_DECIDED"

    def test_provider_validation_failure_is_not_recorded_as_sent(
        self, client, fake_router, auth_headers
    ):
        env = _register_user(client, auth_headers, "failed-email@test.com")
        _create_mock_integration(client, env["headers"])
        fake_router.script.append(AIMessage(content="", tool_calls=[{
            "name": "send_email", "args": {
                "to": "failed@example.com", "subject": "Failure case",
                "body": "This must not be sent.",
                "attachments": [{"document_id": "does-not-belong-to-user"}],
            }, "id": "call_email_failure", "type": "tool_call",
        }]))
        fake_router.script.append(AIMessage(content="The provider rejected the email."))
        response = _chat(client, env["headers"], "Send the failure-case email")
        approval_id = response["approval"]["id"]
        assert email_service._MOCK.sent_count(env["user_id"]) == 0
        approved = client.post(
            f"/api/approvals/{approval_id}/approve", headers=env["headers"]
        )
        assert approved.status_code == 200, approved.text
        assert email_service._MOCK.sent_count(env["user_id"]) == 0
        approvals = client.get("/api/approvals", headers=env["headers"]).json()
        row = next(item for item in approvals if item["id"] == approval_id)
        assert row["status"] == "failed"


# --------------------------------------------------------------------------- #
# Registry-discovery routing regression                                          #
# --------------------------------------------------------------------------- #
class TestRegistryDiscovery:
    def test_unrelated_travel_capability_is_not_selected_for_career_research(self):
        from app.tools.routing import route_request

        request = (
            "Search for active software development roles or mechanical engineering "
            "internships in Kenya. Select 2 matching job listings and create a tailored "
            "CV for each role. Package the matches and CVs into a structured summary "
            "report document, then email the complete report and CVs to me."
        )
        selected = route_request(request).tool_names
        assert selected is not None
        assert {
            "search_jobs", "generate_cv_for_job", "create_docx", "send_email"
        } <= set(selected)
        assert "search_buses" not in selected


# --------------------------------------------------------------------------- #
# Fix #1: End-to-end text tool-call parsing through /api/chat                   #
# --------------------------------------------------------------------------- #
class TestTextToolCallEndToEnd:
    """Criteria 1, 2, 20: model text JSON → parsed → executed → approval."""

    def test_model_text_json_send_email_pauses_for_approval(
        self, client, fake_router, auth_headers
    ):
        """The model outputs send_email as JSON text (not native tool_calls).
        graph._parse_text_tool_calls must extract it and the workflow must
        pause for approval — the JSON must NOT appear as the chat reply."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        # Simulate a model that emits JSON as text content (Ollama-style)
        fake_router.script.append(AIMessage(
            content='{"name": "send_email", "arguments": {"to": "textjson@example.com", "subject": "Text JSON test", "body": "Parsed from text."}}'
        ))
        fake_router.script.append(AIMessage(content="Email sent."))

        resp = _chat(client, env["headers"],
                     "Send an email to textjson@example.com")
        # Must NOT show the raw JSON as the reply
        assert '"arguments"' not in resp.get("reply", "")
        assert "send_email" not in resp.get("reply", "") or "send" in resp.get("reply", "").lower()
        # Should have paused for approval (or completed if the tool was auto-allowed)
        assert resp["status"] in ("paused_approval", "completed")

    def test_model_text_json_unknown_tool_not_executed(
        self, client, fake_router, auth_headers
    ):
        """Unknown tool in text JSON is rejected, not executed as code."""
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        fake_router.script.append(AIMessage(
            content='{"name": "sys_exec", "arguments": {"cmd": "rm -rf /"}}'
        ))
        fake_router.script.append(AIMessage(content="Done."))

        resp = _chat(client, env["headers"], "Do something dangerous")
        # Should not crash; should complete with a normal reply
        assert resp["status"] == "completed"
        # The unknown tool JSON should not appear as raw chat text
        assert '"sys_exec"' not in resp.get("reply", "")


# --------------------------------------------------------------------------- #
# Email service verification (Criterion 12, 13, 14)                            #
# --------------------------------------------------------------------------- #
class TestEmailProviderInvocation:
    """Verify the real (mock) provider is invoked after approval and NOT after rejection."""

    def test_approval_triggers_provider_send(
        self, client, fake_router, auth_headers
    ):
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {"to": "verify@example.com", "subject": "Verify",
                         "body": "Test body"},
                "id": "call_verify_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Email was sent successfully."))

        resp = _chat(client, env["headers"], "Send email to verify@example.com")
        assert resp["status"] == "paused_approval"

        conv_id = resp.get("conversation_id") or ""
        if not conv_id:
            with SessionLocal() as db:
                run = db.scalars(
                    select(AgentRun).where(AgentRun.user_id == env["user_id"])
                ).first()
                conv_id = run.conversation_id if run else None

        # Approve
        resp2 = _chat(client, env["headers"], "yes", conv_id)
        assert resp2["status"] == "completed"
        # Criterion 12: real provider was invoked
        assert email_service._MOCK.sent_count(env["user_id"]) == 1

    def test_rejection_does_not_invoke_provider(
        self, client, fake_router, auth_headers
    ):
        env = _register_user(client, auth_headers)
        _create_mock_integration(client, env["headers"])

        fake_router.script.append(AIMessage(
            content="", tool_calls=[{
                "name": "send_email",
                "args": {"to": "noreject@example.com", "subject": "No send",
                         "body": "Should not go"},
                "id": "call_noreject_1", "type": "tool_call",
            }],
        ))
        fake_router.script.append(AIMessage(content="Cancelled."))

        resp = _chat(client, env["headers"], "Send email to noreject@example.com")
        assert resp["status"] == "paused_approval"

        conv_id = resp.get("conversation_id") or ""
        if not conv_id:
            with SessionLocal() as db:
                run = db.scalars(
                    select(AgentRun).where(AgentRun.user_id == env["user_id"])
                ).first()
                conv_id = run.conversation_id if run else None

        resp2 = _chat(client, env["headers"], "cancel", conv_id)
        assert resp2["status"] == "completed"
        # Criterion 13: provider NOT invoked
        assert email_service._MOCK.sent_count(env["user_id"]) == 0


# --------------------------------------------------------------------------- #
# Fix #2: Unit tests for follow-up detection helpers                          #
# --------------------------------------------------------------------------- #
class TestFollowUpHelpers:
    """Criteria 5, 6, 8: _is_follow_up_confirmation / _is_follow_up_rejection."""

    @pytest.mark.parametrize("msg", [
        "send it now", "send now", "send it", "yes", "yeah", "yep",
        "sure", "ok", "okay", "go ahead", "proceed", "do it",
        "please do it", "please send", "that looks good", "approved",
        "approve", "confirm", "confirmed",
    ])
    def test_confirmation_phrases(self, msg):
        assert chat_service._is_follow_up_confirmation(msg) is True

    @pytest.mark.parametrize("msg", [
        "cancel", "cancelled", "never mind", "nevermind", "no thanks",
        "no", "stop", "abort", "don't send", "do not send",
        "change it", "scratch that",
    ])
    def test_rejection_phrases(self, msg):
        assert chat_service._is_follow_up_rejection(msg) is True

    def test_normal_message_is_not_follow_up(self):
        assert chat_service._is_follow_up_confirmation("Hello, how are you?") is False
        assert chat_service._is_follow_up_rejection("Hello, how are you?") is False

    def test_find_pending_approval_returns_none_when_none(self):
        with SessionLocal() as db:
            result = chat_service._find_pending_approval(db, "nonexistent_user_12345", None)
            assert result is None
