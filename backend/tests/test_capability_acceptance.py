"""Repeatable capability-level acceptance checks.

These tests are deterministic and intentionally separate from live-provider
and shared-browser evidence recorded in the audit report.
"""
from __future__ import annotations

import json
from decimal import Decimal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.graph import (
    _activate_plan_step,
    _complete_plan_step,
    _has_failed_tool_result,
    _has_unchanged_failed_step,
    _model_outage_message,
    _preserve_completed_plan_steps,
)
from app.agents.intent import classify_intent, extract_person_search_subject
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.agent import ActivityLog
from app.services.chat_service import _format_public_profile_evidence
from app.services.finance_engine import (
    build_amortization_schedule,
    calculate_loan,
    format_loan_reply,
    parse_loan_request,
)
from app.services.math_engine import classify_math, solve
from app.tools import registry
from app.tools.base import PermissionLevel
from app.tools.plan import normalize_steps
from app.tools.routing import route_request, tool_names_for_intent


def test_research_citations_and_profile_routing_are_grounded():
    person = "Check everything about Parmenas Oduol"
    result = classify_intent(person)
    assert result.intent == "research"
    assert extract_person_search_subject(person) == "Parmenas Oduol"
    names = set(tool_names_for_intent(person))
    assert "search_profiles" in names
    assert "search_flights" not in names

    reply = _format_public_profile_evidence("Parmenas Oduol", [
        {"title": "Public profile", "url": "https://example.org/profile", "snippet": "Public summary"}
    ])
    assert "[Public profile](https://example.org/profile)" in reply
    assert "not verified biographical facts" in reply


def test_company_search_and_multi_recipient_outreach_are_available():
    request = (
        "Search for companies that provide automation for my project and email "
        "them to ask for help."
    )
    routing = route_request(request)
    assert routing.tool_names is not None
    assert "search_companies" in routing.tool_names
    assert "send_email" in routing.tool_names


def test_nse_research_rejects_unselected_flight_tool(
        client, fake_router, auth_headers):
    request = (
        "Search for the current Nairobi Securities Exchange (NSE 20) index "
        "value and find 1 top-performing banking stock listed on the NSE in "
        "2026. Extract its exact stock ticker, recent share price (KES), and "
        "reported dividend yield."
    )
    routing = route_request(request)
    assert routing.intent == "research"
    assert routing.tool_names is not None
    assert "search_web" in routing.tool_names
    assert "search_flights" not in routing.tool_names

    headers = auth_headers("nse-wrong-tool@test.com")
    for message in [
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [{
                "id": "market-search",
                "description": "Find the NSE 20 value and a banking stock's price and yield",
                "tool": "search_web",
                "arguments": {"query": "NSE 20 index banking stock price dividend yield Kenya 2026"},
            }]},
            "id": "nse-plan",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "search_flights",
            "args": {"origin": "Nairobi", "destination": "London", "date": "2026-01-01"},
            "id": "wrong-flight-tool",
            "type": "tool_call",
        }]),
        AIMessage(content="The requested market data was not retrieved."),
    ]:
        fake_router.script.append(message)

    response = client.post(
        "/api/chat",
        json={"message": request},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "partial"
    with SessionLocal() as db:
        assert db.query(ActivityLog).filter_by(
            run_id=response.json()["run_id"],
            tool="search_flights",
        ).count() == 0


def test_scheduled_investment_pipeline_rejects_unselected_bus_tool(
        client, fake_router, auth_headers):
    request = (
        "Please execute the following fully interconnected task pipeline: "
        "Search the web for the current Nairobi Securities Exchange NSE 20 "
        "Index value in 2026 and Stanbic Holdings Plc SBIC share price and "
        "dividend yield. Using the exact share price of KES 290.00 and yield "
        "of 7.8%, calculate gross and net annual dividend income after 5% "
        "withholding tax for 5,000 shares. Generate an Executive Investment "
        "Report with the market context, itemized returns, and a risk "
        "assessment. Create a Google Calendar event titled 'NSE Portfolio "
        "& Dividend Strategy Briefing' for next Wednesday at 2:30 PM EAT."
    )
    routing = route_request(request)
    assert routing.intent == "scheduling"
    assert routing.tool_names is not None
    assert "search_web" in routing.tool_names
    assert "calculate" in routing.tool_names
    assert "create_event" in routing.tool_names
    assert "search_buses" not in routing.tool_names

    headers = auth_headers("investment-wrong-tool@test.com")
    for message in [
        AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [
                {
                    "id": "market-search",
                    "description": "Research the NSE 20 and SBIC market data",
                    "tool": "search_web",
                },
                {
                    "id": "source-review",
                    "description": "Read the market-data sources",
                    "tool": "open_url",
                    "dependencies": ["market-search"],
                },
                {
                    "id": "dividend-calculation",
                    "description": "Calculate the gross and net dividend",
                    "tool": "calculate",
                    "dependencies": ["market-search"],
                },
                {
                    "id": "investment-report",
                    "description": "Generate the investment report",
                    "tool": "create_pdf",
                    "dependencies": ["source-review", "dividend-calculation"],
                },
                {
                    "id": "calendar-briefing",
                    "description": "Create the portfolio strategy calendar event",
                    "tool": "create_event",
                    "dependencies": ["dividend-calculation"],
                },
            ]},
            "id": "investment-plan",
            "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "search_buses",
            "args": {},
            "id": "wrong-bus-tool",
            "type": "tool_call",
        }]),
        AIMessage(content="The requested investment research was not retrieved."),
    ]:
        fake_router.script.append(message)

    response = client.post(
        "/api/chat",
        json={"message": request},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "partial"
    with SessionLocal() as db:
        assert db.query(ActivityLog).filter_by(
            run_id=response.json()["run_id"],
            tool="search_buses",
        ).count() == 0


def test_binomial_math_is_deterministic_and_verified():
    parsed = classify_math("Expand (x+2)^3 using the Binomial Theorem")
    assert parsed.intent == "distribution"
    solved = solve("Expand (x+2)^3 using the Binomial Theorem")
    assert solved.verified
    assert solved.exact == "x**3 + 6*x**2 + 12*x + 8"


def test_finance_json_is_valid_and_independently_calculated():
    request = (
        "Calculate the monthly payment and total interest for a KES 5,000,000 loan "
        "over 15 years at 14% annual interest with monthly repayments. Return JSON only."
    )
    parsed = parse_loan_request(request)
    assert parsed == (5000000.0, 14.0, 180)
    result = calculate_loan(*parsed)
    payload = json.loads(format_loan_reply(result, json_only=True))
    monthly_rate = 0.14 / 12
    expected = 5_000_000 * monthly_rate / (1 - (1 + monthly_rate) ** -180)
    assert payload["monthly_payment"] == round(expected, 2)
    assert payload["total_interest"] == round(expected * 180 - 5_000_000, 2)
    assert payload["term_months"] == 180


def test_hyphenated_loan_term_parses_and_schedule_clears_balance():
    request = (
        "Model a corporate project financing loan of KES 5,000,000 taken at "
        "the commercial bank average lending rate of 14.4% over a 3-year term "
        "(compounded monthly)."
    )

    parsed = parse_loan_request(request)
    assert parsed == (5_000_000, 14.4, 36)

    rows = build_amortization_schedule(*parsed)
    assert len(rows) == 36
    assert rows[-1]["ending_balance"] == 0
    assert rows[0]["payment"] == Decimal("171861.14")
    assert sum(row["principal"] for row in rows) == 5_000_000


def test_intermediate_tool_failure_does_not_claim_downstream_completion(client, fake_router):
    """A failed prerequisite must remain failed and leave dependent work pending."""
    from test_agent_phase_b import _tc, _token

    headers = _token(client, "capability-failure@test.com")
    for message in [
        AIMessage(content="", tool_calls=[_tc("update_plan", {"steps": [
            {
                "description": "open source",
                "tool": "open_url",
                "arguments": {"url": "ftp://invalid.test/source"},
            },
            {"description": "summarize source", "status": "pending"},
        ]}, "cap-1")]),
        AIMessage(content="", tool_calls=[_tc("open_url", {"url": "ftp://invalid.test/source"}, "cap-2")]),
        AIMessage(content="The source could not be opened; the summary was not completed."),
    ]:
        fake_router.script.append(message)
    response = client.post("/api/chat", json={"message": "open the source and summarize it"}, headers=headers)
    assert response.status_code == 200
    task = client.get("/api/tasks", headers=headers).json()[0]
    assert task["plan"][0]["status"] == "failed"
    assert task["plan"][1]["status"] == "pending"


def test_context_retention_compacts_history_without_losing_recent_turns():
    from app.services.chat_service import compact_history

    history = [HumanMessage(content=f"turn {i}") for i in range(6)]
    compacted = compact_history(history, threshold=4, keep=2, summary="Earlier context")
    assert len(compacted) == 3
    assert "Earlier conversation summary" in str(compacted[0].content)
    assert [m.content for m in compacted[-2:]] == ["turn 4", "turn 5"]


def test_error_recovery_and_integration_routing_are_explicit():
    research_names = set(tool_names_for_intent("Research current LLM deployment practices."))
    email_names = set(tool_names_for_intent(
        "Send an email to user@example.com with the requested update."
    ))
    assert "search_web" in research_names
    assert "send_email" in email_names
    assert "translate" not in research_names


def test_career_workflow_discovers_capabilities_from_full_request():
    from app.tools.routing import route_request

    request = (
        "I am a Software Engineer with 5 years of experience in full-stack web development. "
        "Search for relevant jobs I qualify for, prepare a tailored CV, and email a report."
    )
    routing = route_request(request)
    assert routing.intent == "research"
    assert routing.tool_names is not None
    assert "multi_step" in routing.signals
    assert {"search_jobs", "generate_cv_for_job", "send_email"} <= set(routing.tool_names)
    assert "search_buses" not in routing.tool_names
    assert "translate" not in routing.tool_names


def test_unrelated_tool_errors_are_schema_checked_and_goal_agnostic():
    from app.agents.graph import _validate_tool_arguments
    from app.tools import registry
    from app.tools.routing import route_request

    routing = route_request(
        "Find software engineering jobs in Kenya, prepare CVs, and email me a report."
    )
    assert routing.tool_names is not None
    assert "search_buses" not in routing.tool_names

    _, result = _validate_tool_arguments(registry.get("search_buses"), {})
    assert result is not None
    assert not result.success
    assert result.error is not None
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"
    assert {"origin", "destination", "date"} <= set(result.data["missing_fields"])
    assert "ValidationError" not in result.message


def test_missing_calculation_expression_explains_same_tool_retry():
    from app.agents.graph import _validate_tool_arguments
    from app.tools import registry

    _, result = _validate_tool_arguments(registry.get("calculate"), {})
    assert result is not None
    assert result.error is not None
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"
    assert result.data["missing_fields"] == ["expression"]
    assert "'calculate'" in result.message
    assert "Retry this same capability" in result.message


def test_malformed_tool_json_cannot_fall_back_to_empty_email_search():
    from app.agents.graph import _validate_tool_arguments

    spec = registry.get("search_email")
    assert spec is not None
    _, result = _validate_tool_arguments(
        spec,
        {"_raw": '{"query":"EACC contact email"}{"query":"GIF contact email"}'},
    )
    assert result is not None
    assert not result.success
    assert result.error is not None
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"
    assert result.data["invalid_fields"] == ["_raw"]
    assert "Never continue with omitted or defaulted arguments" in result.message


def test_multi_step_actions_are_rejected_until_a_validated_plan_exists(client, fake_router):
    """A model tool call cannot bypass the plan-first runtime contract."""
    from langchain_core.messages import AIMessage

    from app.models.agent import Approval
    response_auth = client.post("/api/auth/register", json={
        "email": "plan-required@test.com",
        "password": "longenough1",
        "name": "Plan User",
    })
    assert response_auth.status_code == 201
    headers = {
        "Authorization": f"Bearer {response_auth.json()['access_token']}",
    }
    fake_router.script.append(AIMessage(
        content="",
        tool_calls=[{
            "name": "send_email",
            "args": {
                "to": "recipient@example.com",
                "subject": "Research report",
                "body": "The report is ready.",
            },
            "id": "unplanned-email",
            "type": "tool_call",
        }],
    ))
    fake_router.script.append(AIMessage(
        content="",
        tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [{
                "description": "Find current job listings",
                "tool": "search_jobs",
            }]},
            "id": "validated-plan",
            "type": "tool_call",
        }],
    ))

    response = client.post(
        "/api/chat",
        json={"message": (
            "Research current software engineering roles in Kenya, prepare a "
            "summary report, and email the report to recipient@example.com."
        )},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["approval"] is None
    assert response.json()["status"] != "paused_approval"
    assert "unfinished" in response.json()["reply"].lower()

    with SessionLocal() as db:
        assert db.query(Approval).count() == 0
    assert any(
        isinstance(message, SystemMessage)
        and "Planning pass" in str(message.content)
        for turn in fake_router.model.tape.items
        for message in turn
    )


def test_unselected_tool_failure_cannot_be_presented_as_task_success(client, fake_router):
    from test_agent_phase_b import _token

    headers = _token(client, "wrong-tool@test.com")
    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "search_buses",
        "args": {},
        "id": "wrong-capability",
        "type": "tool_call",
    }]))

    response = client.post(
        "/api/chat",
        json={"message": (
            "Find active software engineering roles in Kenya, prepare two tailored "
            "CVs, and email the report."
        )},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "partial"
    assert "not selected" in response.json()["reply"].lower()
    assert "search_buses" not in response.json()["reply"]


def test_rejected_capability_is_partial_only_when_no_valid_tool_succeeded():
    rejected = ToolMessage(
        content=ToolResult.fail("CAPABILITY_NOT_SELECTED", "out of scope").model_dump_json(),
        tool_call_id="rejected",
    )
    succeeded = ToolMessage(
        content=ToolResult.ok("Research completed.").model_dump_json(),
        tool_call_id="successful",
    )
    assert _has_failed_tool_result([rejected])
    assert not _has_failed_tool_result([rejected, succeeded])


def test_recovered_plan_step_error_does_not_force_partial_status():
    first_attempt = AIMessage(content="", tool_calls=[{
        "name": "create_docx",
        "args": {"content": "summary"},
        "id": "report-blocked",
        "type": "tool_call",
    }])
    blocked = ToolMessage(
        content=ToolResult.fail("DEPENDENCY_NOT_READY", "prerequisite pending").model_dump_json(),
        tool_call_id="report-blocked",
    )
    retry = AIMessage(content="", tool_calls=[{
        "name": "create_docx",
        "args": {"content": "summary"},
        "id": "report-retry",
        "type": "tool_call",
    }])
    succeeded = ToolMessage(
        content=ToolResult.ok("Report created.").model_dump_json(),
        tool_call_id="report-retry",
    )
    assert _has_failed_tool_result([first_attempt, blocked])
    assert not _has_failed_tool_result([first_attempt, blocked, retry, succeeded])


def test_tool_backed_plan_steps_wait_for_and_complete_dependencies():
    plan = normalize_steps([
        {
            "id": "research",
            "description": "Search for matching roles",
            "tool": "search_web",
            "arguments": {"query": "software jobs Kenya"},
        },
        {
            "id": "report",
            "description": "Create the summary report",
            "tool": "create_docx",
            "arguments": {"content": "Verified results"},
            "dependencies": ["research"],
        },
    ], capability_registry=registry)
    assert plan is not None

    _, blocked_step, blocked = _activate_plan_step(
        plan, "create_docx", {"content": "Verified results"}
    )
    assert blocked_step is None
    assert blocked is not None
    assert blocked.error is not None
    assert blocked.error.code == "DEPENDENCY_NOT_READY"

    active_plan, search_step, error = _activate_plan_step(
        plan, "search_web", {"query": "software jobs Kenya"}
    )
    assert error is None
    assert search_step == 0
    completed_plan, changed = _complete_plan_step(
        active_plan, "search_web", {"query": "software jobs Kenya"}, search_step
    )
    assert changed
    assert completed_plan[0]["status"] == "done"

    ready_plan, report_step, error = _activate_plan_step(
        completed_plan, "create_docx", {"content": "Verified results"}
    )
    assert error is None
    assert report_step == 1
    assert ready_plan[1]["status"] == "active"


def test_read_only_plan_step_accepts_corrected_search_query():
    planned_query = (
        "Christian conscience moral non complitiy Puritan casuistry "
        "Patristic ethics imperial structures"
    )
    corrected_query = (
        "Christian conscience moral non complicity Puritan casuistry "
        "Patristic ethics imperial structures"
    )
    plan = normalize_steps([{
        "id": "research",
        "description": "Research Christian conscience frameworks",
        "tool": "search_web",
        "arguments": {"query": planned_query},
    }], capability_registry=registry)
    assert plan is not None
    search_spec = registry.get("search_web")
    assert search_spec is not None
    assert search_spec.side_effect == "read_only"

    unchanged_plan, strict_step, _ = _activate_plan_step(
        plan,
        "search_web",
        {"query": corrected_query},
    )
    assert strict_step is None
    assert unchanged_plan[0]["status"] == "pending"

    active_plan, step, error = _activate_plan_step(
        plan,
        "search_web",
        {"query": corrected_query},
        permit_read_only_argument_correction=(
            search_spec.permission == PermissionLevel.AUTO
            and search_spec.side_effect == "read_only"
        ),
    )
    assert error is None
    assert step == 0
    assert active_plan[0]["status"] == "active"

    _, unrelated_step, _ = _activate_plan_step(
        plan,
        "search_web",
        {"query": "Find unrelated information about a different subject"},
        permit_read_only_argument_correction=True,
    )
    assert unrelated_step is None


def test_model_outage_does_not_claim_plan_update_as_completed_work():
    plan_update = AIMessage(content="", tool_calls=[{
        "name": "update_plan",
        "args": {"steps": []},
        "id": "plan-update",
        "type": "tool_call",
    }])
    rejected_search = AIMessage(content="", tool_calls=[{
        "name": "search_web",
        "args": {"query": "corrected search"},
        "id": "search-call",
        "type": "tool_call",
    }])
    messages = [
        plan_update,
        ToolMessage(
            content=ToolResult.ok("Plan saved.").model_dump_json(),
            tool_call_id="plan-update",
        ),
        rejected_search,
        ToolMessage(
            content=ToolResult.fail(
                "UNPLANNED_ACTION", "No matching planned search step."
            ).model_dump_json(),
            tool_call_id="search-call",
        ),
    ]

    assert "before any requested task action completed" in _model_outage_message(messages)
    assert "Completed results were preserved" not in _model_outage_message(messages)


def test_model_cannot_mark_unexecuted_plan_steps_complete():
    submitted = normalize_steps([{
        "id": "research",
        "description": "Search for evidence",
        "tool": "search_web",
        "status": "done",
        "arguments": {"query": "source"},
    }], capability_registry=registry)
    assert submitted is not None
    assert _preserve_completed_plan_steps(None, submitted)[0]["status"] == "pending"

    completed = [dict(submitted[0], status="done")]
    assert _preserve_completed_plan_steps(completed, submitted)[0]["status"] == "done"


def test_plan_revisions_must_change_failed_step_before_retrying():
    submitted = normalize_steps([{
        "id": "research",
        "description": "Search for evidence",
        "tool": "search_web",
        "arguments": {"query": "source"},
    }], capability_registry=registry)
    assert submitted is not None
    failed = [dict(submitted[0], status="failed")]
    assert _has_unchanged_failed_step(failed, submitted)

    alternative = normalize_steps([{
        "id": "research",
        "description": "Search an alternative source",
        "tool": "search_web",
        "arguments": {"query": "alternative source"},
    }], capability_registry=registry)
    assert alternative is not None
    assert not _has_unchanged_failed_step(failed, alternative)
    assert _preserve_completed_plan_steps(failed, alternative)[0]["status"] == "pending"


def test_translation_failure_does_not_stop_independent_research(client, fake_router):
    from test_agent_phase_b import _tc, _token

    headers = _token(client, "career-recovery@test.com")
    for message in [
        AIMessage(content="", tool_calls=[_tc("update_plan", {"steps": [
            {
                "description": "Search for software engineering roles",
                "tool": "search_web",
                "arguments": {"query": "software engineer jobs Kenya"},
            },
            {
                "description": "Translate the resulting summary",
                "tool": "translate",
                "arguments": {"text": "jobs", "target": "sw"},
            },
        ]}, "career-plan")]),
        AIMessage(content="", tool_calls=[
            _tc("translate", {"text": "jobs", "target": "sw"}, "career-t1"),
            _tc("search_web", {"query": "software engineer jobs Kenya"}, "career-s1"),
        ]),
        AIMessage(content="Research continued; translation was not required."),
    ]:
        fake_router.script.append(message)

    response = client.post(
        "/api/chat",
        json={
            "message": "Search the web for software engineer jobs in Kenya and "
                       "translate the summary into Swahili."
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["sources"]
    assert "TOOL_EXCEPTION" not in response.json()["reply"]
    assert "research" in response.json()["reply"].lower()


def test_search_automatically_falls_back_after_primary_failure(monkeypatch):
    import asyncio

    from app.services import search_service

    async def primary_fails(query, max_results):
        raise search_service.SearchError("PROVIDER_ERROR", "primary unavailable")

    async def fallback_succeeds(query, max_results):
        return [search_service.SearchResult("Fallback vacancy", "https://example.org/job", "verified result")]

    monkeypatch.setattr(search_service, "_PROVIDERS", {
        "primary": primary_fails,
        "duckduckgo": fallback_succeeds,
    })
    monkeypatch.setattr(search_service, "_resolve", lambda provider: ("primary", primary_fails))
    result = asyncio.run(search_service.search("software engineer Kenya"))
    assert result[0].title == "Fallback vacancy"
