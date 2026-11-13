"""Deterministic acceptance checks for routing, lifecycle truthfulness, and attachments."""
from __future__ import annotations

import base64
from email import message_from_bytes

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.graph import (
    _activate_plan_step,
    _add_public_contact_verification_steps,
    _build_planning_messages,
    _complete_plan_step,
    _current_turn_has_tool_attempt,
    _effective_tool_call_budget,
    _has_failed_tool_result,
    _has_rejected_plan_update,
    _public_contact_email_evidence_error,
    _compact_public_contact_page_result,
    _normalize_email_action_args,
    _expand_contact_tool_bundle,
    _parse_tool_result,
    _public_contact_plan_error,
    _ready_plan_tools,
    _response_calls_tool,
    _repair_research_artifact_plan,
    _research_artifact_plan_error,
    _select_public_contact_tool_calls,
    _matching_plan_step,
)
from app.config import get_settings
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.services import email as email_service
from app.services.email import (
    _build_raw_mime,
    _resolve_email_attachments,
    _ResolvedAttachment,
)
from app.tools import registry
from app.tools.plan import PlanValidationError, normalize_steps
from app.tools.routing import tool_names_for_intent


def test_malformed_tool_output_is_failure_and_cannot_complete():
    result = _parse_tool_result("{not-json")
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "MALFORMED_TOOL_RESULT"
    assert _has_failed_tool_result([
        ToolMessage(content=result.model_dump_json(), tool_call_id="call-1")
    ]) is True


def test_tool_output_decoder_accepts_structured_models_and_mappings():
    from app.services.tool_execution import decode_tool_result

    expected = ToolResult.ok("completed", {"value": 2})

    assert decode_tool_result(expected) == expected
    assert decode_tool_result(expected.model_dump()) == expected
    assert decode_tool_result(expected.model_dump_json()) == expected
    assert decode_tool_result("unstructured output") is None


def test_shared_tool_invoker_normalizes_results_and_failures():
    import asyncio
    from types import SimpleNamespace

    from app.services.tool_execution import invoke_registered_tool

    async def valid_tool(_args):
        return ToolResult.ok("completed", {"value": 2}).model_dump()

    async def malformed_tool(_args):
        return "probably done"

    async def failing_tool(_args):
        raise ValueError("private provider response")

    async def slow_tool(_args):
        import asyncio

        await asyncio.sleep(0.05)
        return ToolResult.ok("completed")

    expected = ToolResult.ok("completed", {"value": 2})
    spec = SimpleNamespace(
        name="shared_test_tool",
        tool=SimpleNamespace(ainvoke=valid_tool),
    )
    assert asyncio.run(invoke_registered_tool(spec, {})) == expected

    spec.tool.ainvoke = malformed_tool
    malformed = asyncio.run(invoke_registered_tool(spec, {}))
    assert malformed.error is not None
    assert malformed.error.code == "MALFORMED_TOOL_RESULT"

    spec.tool.ainvoke = failing_tool
    failure = asyncio.run(invoke_registered_tool(spec, {}, label="Voice capability"))
    assert failure.error is not None
    assert failure.error.code == "TOOL_EXCEPTION"
    assert "private provider response" not in failure.message

    spec.tool.ainvoke = slow_tool
    timeout = asyncio.run(
        invoke_registered_tool(spec, {}, timeout_seconds=0.001)
    )
    assert timeout.error is not None
    assert timeout.error.code == "TIMEOUT"


def test_planner_context_excludes_old_chat_history():
    request = (
        "Search current CBK rates, calculate the net Treasury Bill return, "
        "and create a cited Word report."
    )
    instruction = "Planning pass: call update_plan only."

    messages = _build_planning_messages(request, instruction)

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == instruction
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == request
    assert all("old conversation" not in str(message.content) for message in messages)


def test_public_contact_plan_requires_one_fanout_search_not_company_search():
    request = (
        "Find and verify current public contact emails on official pages for "
        "Transparency International Kenya, Code for Africa, Katiba Institute, "
        "Open Contracting Partnership, Kituo Cha Sheria, ARTICLE 19 Eastern "
        "Africa, EACC, and Global Innovation Fund. Prepare tailored drafts."
    )
    wrong_plan = normalize_steps([
        {
            "id": "ti-kenya",
            "description": "Find contact email for Transparency International Kenya",
            "tool": "search_companies",
        },
        {
            "id": "code-for-africa",
            "description": "Find contact email for Code for Africa",
            "tool": "search_companies",
        },
    ], capability_registry=registry)
    assert wrong_plan is not None
    error = _public_contact_plan_error(request, wrong_plan)
    assert error is not None
    assert "exactly one search_public_contacts" in error
    assert _public_contact_plan_error(request, [{
        "id": "contacts",
        "description": "Find all public organization contact emails",
        "tool": "search_public_contacts",
    }]) is None


def test_public_contact_plan_rejects_multiple_fanout_calls():
    request = (
        "Search public contact emails for Transparency International Kenya "
        "and Code for Africa."
    )
    plan = normalize_steps([
        {
            "id": "ti-kenya",
            "description": "Find TI-Kenya contact",
            "tool": "search_public_contacts",
        },
        {
            "id": "code-for-africa",
            "description": "Find Code for Africa contact",
            "tool": "search_public_contacts",
        },
    ], capability_registry=registry)
    assert plan is not None
    assert _public_contact_plan_error(request, plan) is not None


def test_public_contact_workflow_reserves_calls_for_its_planned_actions():
    plan = [
        {"tool": "search_public_contacts"},
        {"tool": "open_url"},
        {"tool": "open_url"},
        {"tool": "send_email"},
        {"tool": "send_email"},
    ]

    assert _effective_tool_call_budget(plan, 3) == 5
    assert _effective_tool_call_budget([{"tool": "search_web"}], 3) == 3


def test_public_contact_send_plan_requires_individual_approval_actions():
    request = (
        "Search public contact emails for Transparency International Kenya "
        "and Code for Africa, then send one email to each."
    )
    one_send = [
        {"id": "contacts", "description": "Find all public contacts",
         "tool": "search_public_contacts"},
        {"id": "verify", "description": "Verify first address", "tool": "open_url"},
        {"id": "first_send", "description": "Send the first approved message",
         "tool": "send_email"},
    ]
    assert _public_contact_plan_error(request, one_send) is None

    missing_send = one_send[:-1]
    assert _public_contact_plan_error(request, missing_send) is not None

    multiple_sends = [
        *one_send,
        {"id": "second_send", "description": "Send the next message",
         "tool": "send_email"},
    ]
    assert _public_contact_plan_error(request, multiple_sends) is None

    batched_send = [
        *one_send[:-1],
        {"id": "send_batch", "description": "Send all messages",
         "tool": "send_email_batch"},
    ]
    assert _public_contact_plan_error(request, batched_send) is not None


def test_contact_search_adds_official_page_checks_before_any_send():
    plan = [
        {
            "id": "contacts",
            "description": "Find public contacts",
            "tool": "search_public_contacts",
            "status": "active",
            "dependencies": [],
        },
        {
            "id": "draft_first",
            "description": "Draft the first message",
            "tool": "compose_email",
            "status": "pending",
            "dependencies": [0],
        },
        {
            "id": "send_first",
            "description": "Send the first message",
            "tool": "send_email",
            "status": "pending",
            "dependencies": [0],
        },
        {
            "id": "draft_second",
            "description": "Draft the second message",
            "tool": "compose_email",
            "status": "pending",
            "dependencies": [0],
        },
        {
            "id": "send_second",
            "description": "Send the second message",
            "tool": "send_email",
            "status": "pending",
            "dependencies": [0],
        },
    ]
    result_data = {
        "results_by_organization": {
            "First Organization": [
                {"url": "https://first.example.org/contact"},
            ],
            "Second Organization": [
                {"url": "https://second.example.org/contact"},
            ],
        },
    }

    revised, changed = _add_public_contact_verification_steps(
        plan, 0, result_data
    )

    checks = [
        index for index, step in enumerate(revised)
        if step.get("tool") == "open_url"
    ]
    assert changed
    assert len(checks) == 2
    assert "First Organization" in revised[checks[0]]["description"]
    assert "https://first.example.org/contact" in revised[checks[0]]["description"]
    assert "Second Organization" in revised[checks[1]]["description"]
    assert "https://second.example.org/contact" in revised[checks[1]]["description"]
    first_verify, second_verify = checks
    step_indexes = {step["id"]: index for index, step in enumerate(revised)}
    assert revised[first_verify]["dependencies"] == [0]
    assert revised[second_verify]["dependencies"] == [0]
    assert revised[step_indexes["draft_first"]]["dependencies"] == [first_verify]
    assert revised[step_indexes["send_first"]]["dependencies"] == [
        step_indexes["draft_first"]
    ]
    assert revised[step_indexes["draft_second"]]["dependencies"] == [second_verify]
    assert revised[step_indexes["send_second"]]["dependencies"] == [
        step_indexes["draft_second"]
    ]


def test_public_contact_send_plan_skips_separate_drafting_before_approval():
    plan = [
        {
            "id": "contacts",
            "tool": "search_public_contacts",
            "status": "done",
            "dependencies": [],
        },
        {
            "id": "draft_first",
            "tool": "compose_email",
            "description": "Draft the first message",
            "status": "pending",
            "dependencies": [0],
        },
        {
            "id": "send_first",
            "tool": "send_email",
            "description": "Send first@example.org",
            "status": "pending",
            "arguments": {"to": "first@example.org"},
            "dependencies": [1],
        },
        {
            "id": "draft_second",
            "tool": "compose_email",
            "description": "Draft the second message",
            "status": "pending",
            "dependencies": [0],
        },
        {
            "id": "send_second",
            "tool": "send_email",
            "description": "Send second@example.org",
            "status": "pending",
            "arguments": {"to": "second@example.org"},
            "dependencies": [3],
        },
    ]
    result_data = {
        "results_by_organization": {
            "First Organization": [{"url": "https://first.example/contact"}],
            "Second Organization": [{"url": "https://second.example/contact"}],
        },
    }

    revised, changed = _add_public_contact_verification_steps(
        plan, 0, result_data, send_requested=True
    )

    assert changed
    indexes = {step["id"]: index for index, step in enumerate(revised)}
    first_verify = next(
        index for index, step in enumerate(revised)
        if step.get("tool") == "open_url"
        and "First Organization" in step["description"]
    )
    second_verify = next(
        index for index, step in enumerate(revised)
        if step.get("tool") == "open_url"
        and "Second Organization" in step["description"]
    )
    assert revised[indexes["draft_first"]]["status"] == "skipped"
    assert revised[indexes["draft_second"]]["status"] == "skipped"
    assert revised[indexes["send_first"]]["dependencies"] == [first_verify]
    assert revised[indexes["send_second"]]["dependencies"] == [second_verify]
    assert set(step["id"] for step in _ready_plan_tools(revised)) == {
        "verify_contact_6", "verify_contact_7"
    }


def test_public_contact_dispatch_selects_only_next_ready_action():
    plan = [
        {
            "id": "search",
            "tool": "search_public_contacts",
            "status": "done",
            "dependencies": [],
        },
        {
            "id": "verify-first",
            "tool": "open_url",
            "status": "done",
            "description": (
                "Open the official contact page for First Organization. "
                "Candidate URLs: https://first.example/contact; "
                "https://first.example/about"
            ),
            "arguments": {"url": "https://first.example/contact"},
            "dependencies": [0],
        },
        {
            "id": "draft-first",
            "tool": "compose_email",
            "status": "skipped",
            "dependencies": [1],
        },
        {
            "id": "send-first",
            "tool": "send_email",
            "status": "pending",
            "arguments": {"to": "first@example.org"},
            "description": "Send first@example.org",
            "dependencies": [1],
        },
        {
            "id": "verify-second",
            "tool": "open_url",
            "status": "done",
            "arguments": {"url": "https://second.example/contact"},
            "dependencies": [0],
        },
        {
            "id": "send-second",
            "tool": "send_email",
            "status": "pending",
            "arguments": {"to": "second@example.org"},
            "dependencies": [4],
        },
    ]
    send_calls = [
        {
            "id": f"send-call-{recipient}",
            "name": "send_email",
            "args": {"to": f"{recipient}@example.org"},
        }
        for recipient in ("first", "second")
    ]

    selected, deferred = _select_public_contact_tool_calls(
        plan, send_calls
    )

    assert selected == [send_calls[0]]
    assert deferred == [send_calls[1]]
    assert _ready_plan_tools(plan) == [plan[3], plan[5]]
    assert _matching_plan_step(
        plan, "send_email", send_calls[1]["args"]
    ) == 5
    plan_after_first_send = [dict(step) for step in plan]
    plan_after_first_send[3]["status"] = "done"
    selected_after_first_send, deferred_after_first_send = (
        _select_public_contact_tool_calls(
            plan_after_first_send,
            send_calls,
            answered_ids={send_calls[0]["id"]},
        )
    )
    assert selected_after_first_send == [send_calls[1]]
    assert deferred_after_first_send == []

    verification_plan = [{
        "id": "verify-first",
        "tool": "open_url",
        "status": "pending",
        "description": (
            "Open the official contact page for First Organization. "
            "Candidate URLs: https://first.example/contact; "
            "https://first.example/about"
        ),
        "arguments": {"url": "https://first.example/contact"},
        "dependencies": [],
    }]
    assert _matching_plan_step(
        verification_plan,
        "open_url",
        {"url": "https://first.example/about"},
        permit_read_only_argument_correction=True,
    ) == 0


def test_public_contact_dispatch_defers_calls_when_no_action_is_ready():
    plan = [
        {
            "id": "verify",
            "tool": "open_url",
            "status": "pending",
            "dependencies": [1],
        },
        {
            "id": "search",
            "tool": "search_public_contacts",
            "status": "pending",
            "dependencies": [],
        },
    ]
    calls = [{"id": "verify-call", "name": "open_url", "args": {}}]

    selected, deferred = _select_public_contact_tool_calls(plan, calls)

    assert selected == []
    assert deferred == calls


def test_public_contact_page_result_is_compacted_but_retains_email_evidence():
    raw = ToolResult.ok(
        "Page opened",
        {
            "url": "https://example.org/contact",
            "title": "Contact",
            "text": (
                "Large unrelated page content. " * 200
                + "Email support@example.org for more information."
            ),
            "public_emails": ["support@example.org"],
            "total_chars": 6200,
            "untrusted": True,
        },
    ).model_dump_json()

    compact = _parse_tool_result(_compact_public_contact_page_result(raw))

    assert compact.success
    assert compact.data["url"] == "https://example.org/contact"
    assert compact.data["public_emails"] == ["support@example.org"]
    assert "support@example.org" in compact.data["contact_evidence"]
    assert len(compact.data["contact_evidence"]) < 2000
    assert len(compact.data["contact_evidence"]) < len(
        "Large unrelated page content. " * 200
    )


def test_public_contact_email_requires_current_task_page_evidence():
    plan = [
        {
            "id": "send-org",
            "tool": "send_email",
            "arguments": {"to": "contact@example.org"},
            "dependencies": [1],
            "status": "pending",
        },
        {
            "id": "verify-org",
            "tool": "open_url",
            "arguments": {"url": "https://example.org/contact"},
            "status": "done",
            "description": (
                "Open the official contact page for Example Org. "
                "Candidate URLs: https://example.org/contact"
            ),
        },
    ]
    messages = [
        HumanMessage(content="Find the official contact email and send outreach."),
        AIMessage(content="", tool_calls=[{
            "id": "open-contact-page",
            "name": "open_url",
            "args": {"url": "https://example.org/contact"},
            "type": "tool_call",
        }]),
        ToolMessage(
            content=ToolResult.ok(
                "Page opened.",
                {
                    "url": "https://example.org/contact",
                    "title": "Example Org | Contact",
                    "public_emails": ["contact@example.org"],
                },
            ).model_dump_json(),
            tool_call_id="open-contact-page",
        ),
    ]

    assert _public_contact_email_evidence_error(
        messages,
        "CONTACT@example.org",
        plan=plan,
        arguments={"to": "contact@example.org"},
    ) is None
    assert _public_contact_email_evidence_error(
        messages,
        "other@example.org",
        plan=plan,
        arguments={"to": "other@example.org"},
    ) is not None


def test_public_contact_email_cannot_use_another_organization_page():
    plan = [
        {
            "id": "send-org",
            "tool": "send_email",
            "arguments": {"to": "contact@example.org"},
            "dependencies": [1],
            "status": "pending",
        },
        {
            "id": "verify-org",
            "tool": "open_url",
            "arguments": {"url": "https://example.org/contact"},
            "status": "done",
            "description": (
                "Open the official contact page for Example Org. "
                "Candidate URLs: https://example.org/contact"
            ),
        },
    ]
    messages = [
        HumanMessage(content="Find the official contact email and send outreach."),
        AIMessage(content="", tool_calls=[{
            "id": "open-other-page",
            "name": "open_url",
            "args": {"url": "https://other.example/contact"},
            "type": "tool_call",
        }]),
        ToolMessage(
            content=ToolResult.ok(
                "Page opened.",
                {
                    "url": "https://other.example/contact",
                    "public_emails": ["contact@example.org"],
                },
            ).model_dump_json(),
            tool_call_id="open-other-page",
        ),
    ]

    assert "not extracted" in _public_contact_email_evidence_error(
        messages,
        "contact@example.org",
        plan=plan,
        arguments={"to": "contact@example.org"},
    )


def test_public_contact_email_requires_page_identity_match():
    plan = [
        {
            "id": "send-org",
            "tool": "send_email",
            "arguments": {"to": "contact@example.org"},
            "dependencies": [1],
            "status": "pending",
        },
        {
            "id": "verify-org",
            "tool": "open_url",
            "arguments": {"url": "https://unrelated.example.net/contact"},
            "status": "done",
            "description": (
                "Open the official contact page for Example Org. "
                "Candidate URLs: https://unrelated.example.net/contact"
            ),
        },
    ]
    messages = [
        HumanMessage(content="Find the official contact email and send outreach."),
        AIMessage(content="", tool_calls=[{
            "id": "open-contact-page",
            "name": "open_url",
            "args": {"url": "https://unrelated.example.net/contact"},
            "type": "tool_call",
        }]),
        ToolMessage(
            content=ToolResult.ok(
                "Page opened.",
                {
                    "url": "https://unrelated.example.net/contact",
                    "title": "Contact",
                    "text": "Contact us at contact@example.org.",
                    "public_emails": ["contact@example.org"],
                },
            ).model_dump_json(),
            tool_call_id="open-contact-page",
        ),
    ]

    error = _public_contact_email_evidence_error(
        messages,
        "contact@example.org",
        plan=plan,
        arguments={"to": "contact@example.org"},
    )
    assert error is not None
    assert "does not sufficiently match Example Org" in error


def test_public_contact_email_accepts_page_with_organization_identity():
    plan = [
        {
            "id": "send-org",
            "tool": "send_email",
            "arguments": {"to": "contact@example.org"},
            "dependencies": [1],
            "status": "pending",
        },
        {
            "id": "verify-org",
            "tool": "open_url",
            "arguments": {"url": "https://example.org/contact"},
            "status": "done",
            "description": (
                "Open the official contact page for Example Org. "
                "Candidate URLs: https://example.org/contact"
            ),
        },
    ]
    messages = [
        HumanMessage(content="Find the official contact email and send outreach."),
        AIMessage(content="", tool_calls=[{
            "id": "open-contact-page",
            "name": "open_url",
            "args": {"url": "https://example.org/contact"},
            "type": "tool_call",
        }]),
        ToolMessage(
            content=ToolResult.ok(
                "Page opened.",
                {
                    "url": "https://example.org/contact",
                    "title": "Example Org | Contact",
                    "public_emails": ["contact@example.org"],
                },
            ).model_dump_json(),
            tool_call_id="open-contact-page",
        ),
    ]

    assert _public_contact_email_evidence_error(
        messages,
        "contact@example.org",
        plan=plan,
        arguments={"to": "contact@example.org"},
    ) is None


def test_public_contact_email_does_not_reuse_prior_turn_page_evidence():
    messages = [
        HumanMessage(content="Earlier request."),
        AIMessage(content="", tool_calls=[{
            "id": "old-open",
            "name": "open_url",
            "args": {"url": "https://example.org/contact"},
            "type": "tool_call",
        }]),
        ToolMessage(
            content=ToolResult.ok(
                "Page opened.",
                {
                    "url": "https://example.org/contact",
                    "public_emails": ["contact@example.org"],
                },
            ).model_dump_json(),
            tool_call_id="old-open",
        ),
        HumanMessage(content="Find the official email and send outreach."),
    ]

    assert _public_contact_email_evidence_error(
        messages, "contact@example.org"
    ) is not None


def test_public_contact_capability_binds_verification_and_drafting_tools_only():
    names = _expand_contact_tool_bundle(["search_public_contacts"], registry)

    assert {
        "search_public_contacts",
        "search_web",
        "open_url",
        "compose_email",
    } <= names
    assert "send_email_batch" not in names
    assert "compose_email_batch" not in names


def test_public_contact_refusal_is_not_a_search_tool_attempt():
    refusal = AIMessage(
        content=(
            "I cannot look up external email addresses. Please provide the "
            "addresses."
        )
    )
    search_call = AIMessage(
        content="",
        tool_calls=[{
            "name": "search_public_contacts",
            "args": {"organizations": ["Transparency International Kenya"]},
            "id": "public-search",
        }],
    )
    messages = [
        HumanMessage(content="Find Transparency International Kenya's email."),
        search_call,
        ToolMessage(content=ToolResult.ok("Search attempted.").model_dump_json(),
                    tool_call_id="public-search"),
    ]

    assert not _response_calls_tool(refusal, "search_public_contacts")
    assert _response_calls_tool(search_call, "search_public_contacts")
    assert _current_turn_has_tool_attempt(messages, "search_public_contacts")
    assert not _current_turn_has_tool_attempt(
        [HumanMessage(content="Find an email."), refusal],
        "search_public_contacts",
    )


def test_rejected_plan_result_is_not_treated_as_a_user_clarification():
    call = AIMessage(
        content="",
        tool_calls=[{
            "name": "update_plan",
            "args": {"steps": []},
            "id": "plan-call",
        }],
    )
    rejected = ToolMessage(
        content=ToolResult.fail("RESEARCH_PLAN_INCOMPLETE", "Open a page first.").model_dump_json(),
        tool_call_id="plan-call",
    )
    accepted = ToolMessage(
        content=ToolResult.ok("Plan updated.").model_dump_json(),
        tool_call_id="plan-call",
    )

    assert _has_rejected_plan_update([call, rejected])
    assert not _has_rejected_plan_update([call, accepted])


def test_research_routing_does_not_expose_location_search():
    names = set(tool_names_for_intent("Research current local LLM production practices.") or [])
    assert "search_web" in names
    assert {"find_place", "find_nearby", "get_directions", "calculate_distance"}.isdisjoint(names)


def test_plan_normalization_preserves_dependencies_and_tool_hints():
    plan = normalize_steps([
        {"id": "research", "description": "Research", "tool": "search_jobs"},
        {
            "id": "verify", "description": "Verify",
            "dependencies": ["research", "research"],
            "tool": "get_job_posting",
        },
    ], capability_registry=registry)
    assert plan[0]["id"] == "research"
    assert plan[1]["id"] == "verify"
    assert plan[1]["dependencies"] == [0]
    assert plan[1]["tool"] == "get_job_posting"


def test_research_artifact_plan_must_open_pages_after_search():
    missing_page = normalize_steps([
        {"id": "search", "description": "Find current sources", "tool": "search_web"},
        {
            "id": "report",
            "description": "Create a report",
            "tool": "create_pdf",
            "dependencies": ["search"],
        },
    ], capability_registry=registry)
    assert "page-reading step" in _research_artifact_plan_error(
        missing_page, registry
    )

    valid_plan = normalize_steps([
        {"id": "search", "description": "Find current sources", "tool": "search_web"},
        {
            "id": "read",
            "description": "Read a source page",
            "tool": "open_url",
            "dependencies": ["search"],
        },
        {
            "id": "report",
            "description": "Create a report from the opened source",
            "tool": "create_pdf",
            "dependencies": ["read"],
        },
    ], capability_registry=registry)
    assert _research_artifact_plan_error(valid_plan, registry) is None


def test_research_report_must_depend_on_opened_search_page():
    plan = normalize_steps([
        {"id": "search", "description": "Find current sources", "tool": "search_web"},
        {
            "id": "read",
            "description": "Read a source page",
            "tool": "open_url",
            "dependencies": ["search"],
        },
        {
            "id": "report",
            "description": "Create a report",
            "tool": "create_pdf",
            "dependencies": ["search"],
        },
    ], capability_registry=registry)
    assert "Every report step" in _research_artifact_plan_error(plan, registry)


def test_planner_repairs_common_model_argument_and_source_dependency_shapes():
    plan = normalize_steps([
        {
            "id": "market",
            "name": "search_web",
            "expectedOutput": "Current market data and source URLs",
            "arguments": {"query": "NSE 20 and Stanbic Holdings SBIC"},
        },
        {
            "id": "calculation",
            "name": "calculate",
            "expectedOutput": "Gross and net dividend income",
            "arguments": "5000 * 290 * 0.078",
            "dependencies": ["market"],
        },
        {"arguments": "unattached report content"},
        {
            "id": "report",
            "name": "create_pdf",
            "expectedOutput": "Investment report",
            "arguments": "report title and body",
            "dependencies": ["calculation"],
        },
    ], capability_registry=registry, allow_argument_hints=True)
    assert plan is not None
    assert plan[0]["description"] == "Current market data and source URLs"
    assert plan[1]["argument_hints_omitted"] is True
    assert "arguments" not in plan[1]

    repaired = _repair_research_artifact_plan(
        plan,
        registry,
        {"search_web", "open_url", "create_pdf"},
    )
    assert [step["tool"] for step in repaired] == [
        "search_web", "calculate", "open_url", "create_pdf",
    ]
    assert repaired[2]["dependencies"] == [0]
    assert repaired[3]["dependencies"] == [1, 2]
    assert _research_artifact_plan_error(repaired, registry) is None


def test_email_approval_args_are_polished_before_review():
    original = {
        "to": "parmenasoduol1318@gmail.com",
        "subject": "Email to ask him how he is doing",
        "body": "email to ask him how he is doing",
        "regards": "Regards,",
    }

    normalized = _normalize_email_action_args("send_email", original)

    assert normalized["to"] == "parmenasoduol1318@gmail.com"
    assert normalized["subject"] == "How Are You"
    assert normalized["body"] == (
        "Hi Parmenasoduol,\n\nI hope you're doing well. I wanted to check in "
        "and see how you are doing."
    )
    assert normalized["regards"] == "Regards,"
    assert original["body"] == "email to ask him how he is doing"


def test_email_approval_normalizes_copied_request_without_rewriting_prose():
    request_copy = {
        "to": "alex@example.test",
        "subject": "Please email Alex that I will be late",
        "body": "Please email Alex that I will be late",
    }

    normalized = _normalize_email_action_args("send_email", request_copy)
    polished = {
        "to": "alex@example.test",
        "subject": "I Will Be Late",
        "body": "I will be late to the meeting.",
    }
    ordinary_prose = {
        "to": "alex@example.test",
        "subject": "Meeting update",
        "body": "I will be late to the meeting.",
    }

    assert normalized["body"] == "Hi Alex,\n\nI will be late."
    assert _normalize_email_action_args("send_email", polished) == polished
    assert _normalize_email_action_args("send_email", ordinary_prose) == ordinary_prose


def test_plan_normalization_accepts_common_capability_and_dependency_aliases():
    plan = normalize_steps([
        {
            "id": "find",
            "capability": "search_jobs",
            "expected_output": "Find matching jobs",
        },
        {
            "id": "select",
            "capability": "none",
            "expected_output": "Select the best matching jobs",
            "arguments": {},
            "depends": ["find"],
        },
        {
            "id": "verify",
            "capability": "get_job_posting",
            "expected_output": "Verify a selected listing",
            "depends": ["select"],
        },
    ], capability_registry=registry)

    assert plan[0]["tool"] == "search_jobs"
    assert plan[1]["kind"] == "reasoning"
    assert plan[1]["dependencies"] == [0]
    assert plan[2]["tool"] == "get_job_posting"
    assert plan[2]["dependencies"] == [1]

    plan, first_step, error = _activate_plan_step(plan, "search_jobs", {})
    assert error is None
    assert first_step == 0
    plan, changed = _complete_plan_step(plan, "search_jobs", {}, first_step)
    assert changed
    plan, verify_step, error = _activate_plan_step(plan, "get_job_posting", {})
    assert error is None
    assert verify_step == 2
    assert plan[1]["status"] == "done"


def test_plan_can_omit_invalid_arguments_that_depend_on_prior_outputs():
    plan = normalize_steps([
        {
            "id": "research",
            "description": "Find evidence",
            "tool": "search_web",
            "arguments": {"query": "current roles"},
        },
        {
            "id": "email",
            "description": "Email generated deliverables",
            "tool": "send_email",
            "arguments": {
                "to": "candidate@example.com",
                "subject": "Deliverables",
                "body": "See the generated files.",
                "attachments": ["cv-one.pdf", "cv-two.pdf"],
            },
            "dependencies": ["research"],
        },
    ], capability_registry=registry, allow_argument_hints=True)

    assert plan[1]["argument_hints_omitted"] is True
    assert "arguments" not in plan[1]


@pytest.mark.parametrize("steps, message", [
    (
        [
            {"id": "a", "description": "A", "dependencies": ["b"]},
            {"id": "b", "description": "B", "dependencies": ["a"]},
        ],
        "dependency cycle",
    ),
    ([{"description": "A", "dependencies": [1]}], "outside this plan"),
    ([{"id": "same", "description": "A"}, {"id": "same", "description": "B"}],
     "Duplicate step id"),
])
def test_invalid_plan_graphs_are_rejected(steps, message):
    with pytest.raises(PlanValidationError, match=message):
        normalize_steps(steps)


def test_plan_rejects_unregistered_capabilities_and_invalid_arguments():
    with pytest.raises(PlanValidationError, match="unavailable capability"):
        normalize_steps(
            [{"description": "Do it", "tool": "not_registered"}],
            capability_registry=registry,
        )

    with pytest.raises(PlanValidationError, match="input schema"):
        normalize_steps(
            [{
                "description": "Search",
                "tool": "search_web",
                "arguments": {"max_results": "many"},
            }],
            capability_registry=registry,
        )


def test_dependency_plan_accepts_forward_edges_and_validates_capability_inputs():
    plan = normalize_steps([
        {
            "id": "report",
            "description": "Create report",
            "tool": "create_pdf",
            "arguments": {"title": "Research report", "content": "Verified findings"},
            "depends_on": ["research"],
            "expected_output": "A downloadable PDF",
            "completion_criteria": "The generated file exists",
        },
        {
            "id": "research",
            "description": "Research the topic",
            "tool": "search_web",
            "arguments": {"query": "verified research"},
        },
    ], capability_registry=registry)

    assert plan[0]["dependencies"] == [1]
    assert plan[0]["arguments"]["title"] == "Research report"
    assert plan[0]["expected_output"] == "A downloadable PDF"


def test_plan_normalizes_model_tool_kind_to_registered_capability():
    plan = normalize_steps(
        [{
            "id": "research",
            "description": "Search the web for a factual answer",
            "kind": "tool",
            "name": "search_web",
            "arguments": {"query": "3I/ATLAS message to Earth"},
        }],
        capability_registry=registry,
    )
    assert plan is not None
    assert plan[0]["tool"] == "search_web"
    assert plan[0]["arguments"] == {"query": "3I/ATLAS message to Earth"}
    assert "kind" not in plan[0]


def test_email_mime_contains_a_real_attachment(tmp_path):
    path = tmp_path / "candidate.txt"
    path.write_text("candidate profile", encoding="utf-8")
    raw = _build_raw_mime(
        "recipient@example.com", "Candidate", "See attached",
        [_ResolvedAttachment(path, "candidate.txt", "text/plain", path.stat().st_size)],
    )
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    attachments = [part for part in message.walk() if part.get_filename() == "candidate.txt"]
    assert len(attachments) == 1
    assert attachments[0].get_payload(decode=True) == b"candidate profile"


def test_email_mime_includes_multiple_to_cc_and_bcc_headers():
    raw = _build_raw_mime(
        "first@example.com, second@example.com",
        "Project update",
        "I am ready.",
        cc="copy@example.com",
        bcc="hidden@example.com",
    )
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert message["To"] == "first@example.com, second@example.com"
    assert message["Cc"] == "copy@example.com"
    assert message["Bcc"] == "hidden@example.com"


def test_attachment_validation_rejects_unowned_and_unsupported_files(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "outputs_dir", tmp_path / "outputs")
    owned = settings.outputs_dir / "user-1"
    owned.mkdir(parents=True)
    path = owned / "secret.exe"
    path.write_bytes(b"not an executable attachment")
    with SessionLocal() as db:
        with pytest.raises(email_service.EmailError) as exc:
            _resolve_email_attachments(
                db, user_id="user-1",
                attachments=[{"url": "/api/files/user-1/secret.exe"}],
            )
    assert exc.value.code == "INVALID_ATTACHMENT_MIME"

    with SessionLocal() as db:
        with pytest.raises(email_service.EmailError) as exc:
            _resolve_email_attachments(
                db, user_id="user-1",
                attachments=[{"url": "/api/files/other-user/secret.txt"}],
            )
    assert exc.value.code == "ATTACHMENT_NOT_FOUND"
