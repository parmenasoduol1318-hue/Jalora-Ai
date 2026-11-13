"""Registry-driven capability discovery and graph binding."""
from __future__ import annotations

import asyncio

import pytest

from app.agents.graph import build_graph
from app.agents.intent import classify_intent, extract_person_search_subject
from app.services.model_router import JaloraChatModel, ProviderSpec
from app.tools import registry as real_registry
from app.tools.base import PermissionLevel, ToolRegistry
from app.tools.routing import (
    is_multi_step_request,
    local_app_command,
    local_app_open_target,
    resolve_intent_hint,
    route_request,
    tool_names_for_intent,
)


def _make_tool(name: str):
    async def _run() -> str:
        return "ok"

    _run.__name__ = name
    _run.__doc__ = "test tool"
    return _run


def _model() -> JaloraChatModel:
    spec = ProviderSpec("routing", "openai", "http://x/v1", "k", "m", "m")
    return JaloraChatModel(spec=spec, model_name="m")


# --- registry-based capability discovery -----------------------------------


def test_discovered_tool_names_exist_in_registry():
    known = {spec.name for spec in real_registry.all()}
    names = route_request(
        "Find software jobs, prepare tailored CVs, write a report, and email it"
    ).tool_names
    assert names
    assert set(names) <= known
    assert {"update_plan", "current_datetime"} <= set(names)


def test_job_search_includes_its_registered_verification_capability():
    names = set(route_request(
        "Search current software engineering jobs in Kenya and create tailored CVs."
    ).tool_names or [])
    assert {"search_jobs", "get_job_posting", "generate_cv_for_job"} <= names
    assert "search_buses" not in names


def test_email_search_includes_its_registered_reader_capability():
    names = set(route_request(
        "Find emails from my bank about my application and summarize them."
    ).tool_names or [])
    assert {"search_email", "read_email"} <= names


def test_public_outreach_with_call_to_action_routes_to_web_and_approved_send():
    request = (
        "Retrieve current valid contact/support email addresses for the following "
        "organizations and draft outreach emails. Include a clear call-to-action "
        "requesting a meeting. Search the emails and send them for each organization."
    )
    routing = route_request(request)
    names = set(routing.tool_names or [])

    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert {"search_public_contacts", "open_url", "send_email"} <= names
    assert not {
        "search_email", "read_email", "make_call", "send_email_batch",
    } & names


def test_agent_mode_override_preserves_public_contact_research_route():
    request = (
        "Retrieve current contact email addresses for organizations and include "
        "a clear call-to-action. Send the outreach emails."
    )
    routing = route_request(request, intent_override="task")

    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert {"search_public_contacts", "open_url", "send_email"} <= set(
        routing.tool_names or []
    )


def test_order_requests_discover_vendors_search_and_page_readers():
    names = set(route_request(
        "Order a birthday cake for 20 people in Nairobi and compare "
        "bakery ingredients, quantity, and prices."
    ).tool_names or [])
    assert {"search_companies", "search_web"} <= names
    assert {"open_url", "browse_page"} & names


def test_biomedical_hypothesis_uses_web_sources_without_ordering_tools():
    request = (
        "Novel theoretical biomedical hypothesis: CRISPR-Cas13 RNA editing "
        "and LRAs for an HIV functional cure. The proposal combines targeted "
        "latency reversal with RNA degradation, proposes a mechanism, and "
        "describes nanoparticle delivery and off-target safety strategies."
    )
    routing = route_request(request)
    names = set(routing.tool_names or [])

    assert routing.intent == "research"
    assert "biomedical_review" in routing.signals
    assert {"search_web", "open_url"} <= names
    assert not {"search_companies", "search_hotels"} & names
    assert "multi_step" not in routing.signals


def test_explicit_biomedical_report_workflow_still_requires_a_plan():
    routing = route_request(
        "Review this HIV CRISPR hypothesis and generate a cited PDF report."
    )
    assert "multi_step" in routing.signals
    assert {"search_web", "open_url", "create_pdf"} <= set(
        routing.tool_names or []
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "generate a photo of a cup on a table",
        "Draw me a picture of a red bicycle",
        "Can you create a photorealistic image of a mountain lake?",
    ],
)
def test_image_generation_requests_discover_generate_image(prompt):
    routing = route_request(prompt)
    assert "image_generation" in routing.signals
    assert "generate_image" in (routing.tool_names or [])


@pytest.mark.parametrize(
    "prompt",
    [
        "Generate a PDF document about renewable energy.",
        "Create a PDF report about renewable energy.",
        "Turn this summary into a downloadable PDF file.",
        "Convert these meeting notes into a PDF.",
    ],
)
def test_pdf_document_requests_discover_pdf_generation(prompt):
    assert "create_pdf" in (route_request(prompt).tool_names or [])


@pytest.mark.parametrize(
    "prompt",
    [
        "Generate a Word document about renewable energy.",
        "Create a DOCX report about renewable energy.",
        "Turn this summary into a Word document.",
        "Convert these meeting notes into a docx file.",
        "Write a report in Microsoft Word.",
    ],
)
def test_word_document_requests_discover_docx_generation(prompt):
    assert "create_docx" in (route_request(prompt).tool_names or [])


def test_task_query_discovers_only_relevant_capabilities():
    names = set(tool_names_for_intent("Inspect code and run its tests"))
    assert {"inspect_code", "run_tests"} <= names
    assert "search_buses" not in names
    assert "send_payment" not in names


@pytest.mark.parametrize("prompt", ["open youtube", "Please open YouTube.", "launch youtube.com"])
def test_exact_youtube_launch_uses_deterministic_browser_tool(prompt):
    routing = route_request(prompt)

    assert routing.intent == "task"
    assert "local_app_control" in routing.signals
    assert "open_youtube" in (routing.tool_names or [])
    assert "open_local_app" not in (routing.tool_names or [])
    assert local_app_open_target(prompt) is not None


def test_local_app_launch_requires_an_exact_single_app_request():
    assert local_app_open_target("open calculator") == "calculator"
    assert local_app_open_target("open any app") is None
    assert local_app_open_target("open YouTube and play a song") is None


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("close calculator", ("close", "calculator")),
        ("Please quit the WhatsApp app.", ("close", "WhatsApp")),
        ("open WhatsApp", ("open", "WhatsApp")),
    ],
)
def test_exact_local_app_commands_route_to_allowlisted_app_tools(prompt, expected):
    assert local_app_command(prompt) == expected


@pytest.mark.parametrize(
    "prompt",
    ["close the app", "close any app", "close calculator and save my work"],
)
def test_local_app_command_requires_one_named_action(prompt):
    assert local_app_command(prompt) is None


def test_local_app_command_does_not_capture_a_translation_workflow():
    assert local_app_command(
        "Open the source and translate its contents into Swahili."
    ) is None


def test_exact_local_app_close_routes_to_confirmed_close_tool():
    routing = route_request("close Calculator")

    assert routing.intent == "task"
    assert "local_app_control" in routing.signals
    assert "close_local_app" in (routing.tool_names or [])
    assert "open_local_app" not in (routing.tool_names or [])


def test_attached_document_turn_always_discovers_document_reader():
    names = set(route_request(
        "Extract the key dates from the attached contract.",
        extra_signals=("attached_document",),
    ).tool_names or [])
    assert "read_document" in names


def test_registry_selection_includes_registered_verifiers():
    reg = ToolRegistry()

    async def find_widget():
        """Find a widget."""
        return "candidate"

    async def verify_widget():
        """Open and verify a widget listing."""
        return "verified"

    reg.register(find_widget, category="widgets", tags=["widget", "search"],
                 verification_tool="verify_widget")
    reg.register(verify_widget, category="widgets", tags=["widget", "verify"])
    assert set(reg.select_for_task("find widget")) >= {"find_widget", "verify_widget"}


# --- graph binding -----------------------------------------------------------

def _bind_spy(monkeypatch, captured: list) -> None:
    original = JaloraChatModel.bind_tools

    def spy(self, tools, **kwargs):
        captured.append([t.name for t in tools])
        return original(self, tools, **kwargs)

    monkeypatch.setattr(JaloraChatModel, "bind_tools", spy)


def test_build_graph_binds_only_requested_names(monkeypatch):
    reg = ToolRegistry()
    for name in ("alpha", "beta", "gamma"):
        reg.register(_make_tool(name), permission=PermissionLevel.AUTO)
    captured: list = []
    _bind_spy(monkeypatch, captured)
    build_graph(model=_model(), registry=reg, tool_names=["alpha", "gamma"])
    assert captured == [["alpha", "gamma"]]


def test_build_graph_default_binds_all(monkeypatch):
    reg = ToolRegistry()
    for name in ("alpha", "beta"):
        reg.register(_make_tool(name), permission=PermissionLevel.AUTO)
    captured: list = []
    _bind_spy(monkeypatch, captured)
    build_graph(model=_model(), registry=reg)
    assert captured == [["alpha", "beta"]]


def test_direct_tool_entry_executes_without_invoking_model(monkeypatch):
    from conftest import Box, FakeModel
    from langchain_core.messages import AIMessage

    calls = []

    async def open_youtube():
        calls.append("opened")
        from app.errors import ToolResult
        return ToolResult.ok("Opened YouTube.")

    reg = ToolRegistry()
    reg.register(open_youtube, permission=PermissionLevel.AUTO)
    model = FakeModel(script=Box())

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agents.graph._audit_tool", no_audit)

    async def run():
        graph = build_graph(
            model=model,
            registry=reg,
            tool_names=["open_youtube"],
            entry="tools",
            max_steps=2,
            max_tool_calls=2,
        )
        return await graph.ainvoke({
            "messages": [AIMessage(content="", tool_calls=[{
                "name": "open_youtube", "args": {}, "id": "open-call",
            }])],
            "steps": 0,
            "sources": [],
            "artifacts": [],
            "pending_approval": None,
            "approved_call_ids": [],
            "rejected_call_ids": [],
            "status": "running",
            "run_id": "direct-open-run",
            "user_id": "direct-open-user",
            "task_id": "",
            "plan": None,
            "tool_calls": 0,
            "policy": {"autonomy": 2, "tool_policies": {}},
            "intent": "task",
            "request": "open YouTube",
            "direct_action": {
                "call_id": "open-call",
                "tool_name": "open_youtube",
                "display_name": "YouTube",
            },
        })

    result = asyncio.run(run())
    assert calls == ["opened"]
    assert not model.tape
    assert result["status"] == "completed"
    assert result["messages"][-1].content == (
        "Opened YouTube in the default browser."
    )


# --- route_request: analyze → route entry point (stage 4) ----------------------


def test_route_request_scheduling_maps_to_schedule_and_reminder():
    r = route_request("remind me to write a python script comparing prices")
    assert r.intent == "scheduling"
    assert r.tool_names is not None
    assert {"schedule_task", "create_reminder"} & set(r.tool_names)
    assert r.signals  # classification signals are populated


def test_route_request_phone_number_is_not_math():
    r = route_request("Text +254700000001 saying hi there")
    assert r.intent == "chat"
    assert "send_sms" in (r.tool_names or [])


def test_route_request_task_uses_bounded_capability_discovery():
    r = route_request("write a draft proposal for the client")
    assert r.intent == "task"
    assert r.tool_names is not None
    assert len(r.tool_names) < len(real_registry.all())


def test_shortcut_hint_routes_generic_message_to_selected_capability():
    hint = resolve_intent_hint("Nairobi landmarks", "web_search")
    routing = route_request("Nairobi landmarks", intent_override=hint)

    assert hint == "web_search"
    assert routing.intent == "web_search"
    assert "search_web" in (routing.tool_names or [])


def test_specific_message_intent_overrides_mismatched_shortcut_hint():
    message = "Send an email to test@example.com about the meeting"

    assert resolve_intent_hint(message, "web_search") is None
    assert route_request(message).intent == "email"


def test_email_body_instructions_are_not_mistaken_for_a_second_task():
    message = (
        "Send an email to junioroduol@gmail.com and tell him that I am ready "
        "for the showing of my current app capabilities."
    )

    assert not is_multi_step_request(message)


def test_saving_an_email_draft_is_one_action():
    assert not is_multi_step_request(
        "Save the email draft to a@b.example with subject API draft."
    )


def test_email_then_calendar_action_requires_a_plan():
    assert is_multi_step_request(
        "Send an email to test@example.com and then schedule a meeting."
    )


def test_image_generation_signal_is_not_shadowed_by_generic_shortcut_hint():
    message = "Create a picture of a red bicycle"

    assert resolve_intent_hint(message, "math") is None
    assert "generate_image" in (route_request(message).tool_names or [])


@pytest.mark.parametrize(
    ("intent", "category", "expected_tool"),
    [
        ("web_search", "research", "search_web"),
        ("email", "email", "compose_email"),
        ("math", "math", "calculate"),
        ("task", "internal", "update_plan"),
        ("coding", "coding", "edit_code"),
        ("research", "jobs", "search_jobs"),
        ("task", "comms", "make_call"),
        ("task", "translate", "translate"),
        ("task", "documents", "read_document"),
    ],
)
def test_shortcut_capability_category_binds_registered_tools(
    intent: str,
    category: str,
    expected_tool: str,
):
    routing = route_request(
        "Explain this request in plain language",
        intent_override=intent,
        capability_hint=category,
    )

    assert expected_tool in (routing.tool_names or [])


def test_unknown_shortcut_capability_category_is_rejected():
    with pytest.raises(ValueError, match="Unknown capability category"):
        route_request(
            "Explain this request",
            intent_override="task",
            capability_hint="not-registered",
        )


def test_route_request_chat_default():
    r = route_request("hello there, how are you?")
    assert r.intent == "chat"
    assert r.tool_names is not None
    assert len(r.tool_names) > 0


def test_multi_step_request_discovers_cross_capability_tools_without_full_registry():
    r = route_request(
        "Research solar power in Kenya, compare its cost with wind, "
        "and prepare a report."
    )
    assert r.intent == "research"
    assert r.tool_names is not None
    assert "search_buses" not in r.tool_names
    assert "multi_step" in r.signals


def test_steel_procurement_pipeline_routes_to_agent_planning():
    r = route_request(
        "Please execute this fully dependent pipeline step by step: "
        "Step 1 search the web for the current steel price in Kenya; "
        "Step 2 calculate the cost with VAT and logistics; "
        "Step 3 generate a PDF procurement report; "
        "Step 4 create a calendar event and send the report by email to "
        "parmenas@example.com."
    )
    assert "multi_step" in r.signals
    assert r.tool_names is not None
    assert {
        "update_plan", "search_web", "calculate", "create_pdf",
        "create_event", "send_email",
    } <= set(r.tool_names)


def test_investment_report_pipeline_routes_research_math_file_and_calendar():
    request = (
        "Search the web for the current NSE 20 index and Stanbic Holdings "
        "share price and dividend yield. Calculate gross and net dividend "
        "income for 5,000 shares using the supplied price and yield. Generate "
        "an Executive Investment Report with a cost table and risk assessment. "
        "Create a Google Calendar event for next Wednesday at 2:30 PM."
    )
    routing = route_request(request)
    assert "multi_step" in routing.signals
    names = set(routing.tool_names or [])
    assert {
        "update_plan", "search_web", "calculate", "create_event",
    } <= names
    assert {"create_pdf", "create_docx"} & names
    assert "search_flights" not in names
    assert "send_email" not in names


def test_single_step_research_keeps_focused_tool_routing():
    r = route_request("Research solar power in Kenya")
    assert r.intent == "research"
    assert r.tool_names is not None
    assert "multi_step" not in r.signals


def test_tool_routing_cannot_be_disabled_into_full_registry():
    r = route_request("remind me to write a python script comparing prices")
    assert r.intent == "scheduling"
    assert r.tool_names is not None
    assert len(r.tool_names) < len(real_registry.all())


def test_route_request_empty_text_falls_back_to_chat():
    r = route_request("")
    assert r.intent == "chat"
    assert r.tool_names is not None
    assert set(r.tool_names) == {
        spec.name for spec in real_registry.all()
        if spec.always_available and spec.permission == PermissionLevel.AUTO
    }


def test_route_request_does_not_infer_event_creation_from_attendance():
    """Mentioning attendance in an email must not authorize calendar writes."""
    r = route_request("send email to internetmoney1318@gmail.com and tell him to "
                      "attend meeting on friday at 8 am")
    assert r.intent == "scheduling"
    assert r.tool_names is not None
    assert "send_email" in r.tool_names
    assert "create_event" not in r.tool_names


def test_route_request_job_search_routes_to_research():
    """The original failure: 'Find software dev jobs in Kenya' must route to
    research intent (not coding) and expose job tools."""
    r = route_request("Find software dev jobs in Kenya")
    assert r.intent == "research"
    assert r.tool_names is not None
    assert "search_jobs" in r.tool_names
    assert "generate_cv_for_job" not in r.tool_names
    assert "search_buses" not in r.tool_names
    assert r.signals


def test_treasury_yield_workflow_routes_to_requested_capabilities():
    request = (
        "Search current CBK Central Bank Rate, USD to KES rate, and Kenya "
        "91-day Treasury Bill yield. Calculate gross and net annual interest "
        "for KES 2,500,000 and convert the total to USD. Create a cited Word "
        "report. After I confirm, schedule a calendar review and email the report."
    )

    routing = route_request(request)

    assert routing.tool_names is not None
    assert {
        "search_web",
        "calculate",
        "create_docx",
        "create_event",
        "send_email",
        "update_plan",
    } <= set(routing.tool_names)
    assert "search_buses" not in routing.tool_names


def test_research_intent_query_matches_research_capabilities():
    names = tool_names_for_intent("Research current local LLM production practices.")
    assert "search_web" in names
    assert "search_buses" not in names


def test_current_factual_question_selects_web_search_not_memory_listing():
    routing = route_request("Who is the current prime minister of Israel?")
    names = set(routing.tool_names or [])

    assert routing.intent == "web_search"
    assert {"search_web", "open_url"} <= names
    assert {"list_memories", "search_memory", "search_buses"}.isdisjoint(names)


def test_current_toll_and_cbk_rate_analysis_binds_live_search_tools():
    request = (
        "Calculate annual Nairobi Expressway toll charges and amortize a "
        "KES 5,000,000 corporate loan using 2026 CBK commercial lending rates."
    )
    routing = route_request(request)
    agent_routing = route_request(request, intent_override="task")
    names = set(routing.tool_names or [])

    assert routing.intent == "research"
    assert "live_data_lookup" in routing.signals
    assert {"search_web", "open_url", "calculate"} <= names
    assert {"search_web", "open_url"} <= set(agent_routing.tool_names or [])


def test_task_continuation_inherits_live_search_and_calculation_capabilities():
    context = (
        "Search the web for current 2026 Nairobi Expressway toll charges and "
        "current CBK commercial lending rates, calculate fleet costs, and "
        "create a cited PDF report."
    )

    routing = route_request(
        "Continue from where you left.",
        context_text=context,
    )

    assert routing.intent == "research"
    assert "live_data_lookup" in routing.signals
    assert "multi_step" in routing.signals
    assert {
        "search_web", "open_url", "calculate", "create_pdf", "update_plan",
    } <= set(routing.tool_names or [])


def test_ambiguous_interstellar_message_request_selects_web_research():
    routing = route_request("solve the 3i atlas messege to earth")
    names = set(routing.tool_names or [])
    assert routing.intent == "web_search"
    assert {"search_web", "open_url"} <= names
    assert "search_email" not in names


def test_claim_verification_selects_web_research_capabilities():
    routing = route_request("confirm claims about an et escaping area 51")
    names = set(routing.tool_names or [])
    assert routing.intent == "web_search"
    assert {"search_web", "open_url"} <= names
    assert "search_email" not in names


def test_public_email_research_selects_internet_search_not_user_mailbox():
    request = (
        "Retrieve current valid contact/support email addresses for "
        "Transparency International Kenya and Global Innovation Fund, then "
        "draft an outreach email to each."
    )
    routing = route_request(request)
    names = set(routing.tool_names or [])
    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert {"search_web", "open_url", "compose_email"} <= names
    assert "search_email" not in names


def test_pasted_unfinished_public_email_searches_select_internet_tools():
    request = (
        "These steps remain unfinished: Search email for Open Contracting "
        "Partnership contact email, Search email for Kituo Cha Sheria contact "
        "email, Search email for ARTICLE 19 Eastern Africa contact email, "
        "Search email for EACC Public Reporting Desk contact email, Search "
        "email for Global Innovation Fund contact email."
    )
    routing = route_request(request)
    names = set(routing.tool_names or [])
    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert {"search_web", "open_url"} <= names
    assert "search_email" not in names


def test_email_or_web_contact_search_excludes_private_mailbox_tool():
    routing = route_request(
        "Search email or web for Open Contracting Partnership contact email."
    )
    names = set(routing.tool_names or [])
    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert {"search_web", "open_url", "compose_email"} <= names
    assert "search_email" not in names


def test_agent_mode_keeps_public_contact_lookup_on_web_tools():
    routing = route_request(
        "Search email or web for Open Contracting Partnership contact email.",
        intent_override="task",
    )
    names = set(routing.tool_names or [])
    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert {
        "search_web", "search_public_contacts", "open_url", "compose_email",
    } <= names
    assert "search_email" not in names
    assert "compose_email_batch" not in names


def test_referential_outreach_followup_inherits_public_contact_capability():
    routing = route_request(
        "Replace the old app name in all the drafts and write emails to all of them.",
        intent_override="task",
        context_text=(
            "Find current public contact emails for Transparency International "
            "Kenya and Code for Africa, then draft custom outreach emails."
        ),
    )
    names = set(routing.tool_names or [])
    assert "public_contact_search" in routing.signals
    assert {
        "search_public_contacts", "compose_email",
    } <= names
    assert "search_email" not in names
    assert "compose_email_batch" not in names


def test_public_contact_outreach_binds_approval_gated_send_tool():
    routing = route_request(
        "Find official contact emails for two organizations, draft tailored "
        "outreach messages, and send them only after my approval."
    )
    names = set(routing.tool_names or [])
    assert "public_contact_search" in routing.signals
    assert {
        "search_public_contacts", "compose_email", "send_email",
    } <= names
    assert "search_email" not in names
    assert "send_email_batch" not in names


def test_public_contact_send_is_not_bound_for_single_recipient():
    routing = route_request(
        "Find the official email for Katiba Institute, draft a tailored "
        "outreach email, and send it after my approval."
    )
    names = set(routing.tool_names or [])
    assert {"search_public_contacts", "compose_email"} <= names
    assert "send_email" in names


def test_public_contact_followup_can_send_only_for_multiple_recipients():
    routing = route_request(
        "Send the approved messages now.",
        intent_override="task",
        context_text=(
            "Find contact emails for Transparency International Kenya and "
            "Code for Africa, draft tailored emails, and send them after approval."
        ),
    )

    names = set(routing.tool_names or [])
    assert "send_email" in names
    assert "send_email_batch" not in names


def test_public_contact_research_does_not_bind_send_without_request():
    routing = route_request(
        "Find official contact emails for two organizations and draft outreach."
    )
    assert "send_email" not in (routing.tool_names or [])


def test_organization_email_phrasing_binds_public_contact_tools():
    routing = route_request(
        "Fetch emails for the organisations listed and draft separate outreach."
    )
    names = set(routing.tool_names or [])
    assert "public_contact_search" in routing.signals
    assert {"search_public_contacts", "search_web", "open_url", "compose_email"} <= names
    assert "search_email" not in names


def test_organization_outreach_with_per_email_approval_binds_sequential_send():
    routing = route_request(
        "Find emails for two organizations, draft tailored outreach, and show "
        "each email for my approval before sending."
    )
    names = set(routing.tool_names or [])
    assert "public_contact_search" in routing.signals
    assert "send_email" in names
    assert "send_email_batch" not in names


def test_grouped_public_contact_request_has_fanout_tool_in_agent_mode():
    routing = route_request(
        "Search for contact emails for Transparency International Kenya, "
        "Code for Africa, Katiba Institute, Open Contracting Partnership, "
        "Kituo Cha Sheria, ARTICLE 19 Eastern Africa, EACC, and Global "
        "Innovation Fund.",
        intent_override="task",
    )
    names = set(routing.tool_names or [])
    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert "search_public_contacts" in names
    assert "search_email" not in names


def test_company_wording_for_public_contacts_uses_contact_fanout_not_vendors():
    request = (
        "Search companies for Open Contracting Partnership contact email, "
        "Kituo Cha Sheria contact email, ARTICLE 19 Eastern Africa contact email, "
        "EACC contact email, and Global Innovation Fund contact email."
    )
    routing = route_request(request, intent_override="task")
    names = set(routing.tool_names or [])
    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert "search_public_contacts" in names
    assert not {"search_companies", "search_email"} & names


@pytest.mark.parametrize(
    "query",
    [
        "search Israel",
        "search parmenas oduol",
        "search about parmenas oduol",
    ],
)
def test_direct_name_search_routes_to_web_not_bus_search(query):
    routing = route_request(query)
    names = set(routing.tool_names or [])
    assert routing.intent == "web_search"
    assert "search_web" in names
    assert "search_buses" not in names


def test_explicit_research_always_selects_web_research_tool():
    routing = route_request("Research how Kenya's Treasury Bill yields work.")
    names = set(routing.tool_names or [])

    assert routing.intent == "research"
    assert {"search_web", "open_url"} <= names


@pytest.mark.parametrize(
    "query",
    [
        "How long does the bus take from Mombasa to Nairobi and which operators run it?",
        "Find bus schedules from Nairobi to Mombasa next Friday.",
        "Which coach operators serve the Nairobi to Mombasa route?",
    ],
)
def test_regional_transit_questions_route_to_web_research(query):
    routing = route_request(query)
    names = set(routing.tool_names or [])

    assert routing.intent == "research"
    assert "regional_transit" in routing.signals
    assert {"search_web", "open_url"} <= names
    assert "search_buses" not in names
    assert "schedule_task" not in names


def test_bus_booking_uses_provider_capability_without_generic_search_preflight():
    routing = route_request(
        "Book a bus from Mombasa to Nairobi for tomorrow."
    )
    names = set(routing.tool_names or [])

    assert routing.intent == "task"
    assert "bus_booking" in routing.signals
    assert {
        "search_buses", "browser_open", "browser_wait", "browser_extract",
        "browser_click", "browser_select", "browser_fill",
    } <= names
    assert not {"search_web", "open_url"} & names


def test_generic_search_defaults_to_web_without_overriding_mailbox_search():
    web_search = set(route_request(
        "Search for durable information about Jalora."
    ).tool_names or [])
    mailbox_search = set(route_request(
        "Search my email for the latest project update."
    ).tool_names or [])

    assert "search_web" in web_search
    assert {"search_email", "search_buses"}.isdisjoint(web_search)
    assert "search_email" in mailbox_search
    assert "search_web" not in mailbox_search


def test_web_discovery_includes_registered_page_readers_for_research_workflows():
    names = set(route_request(
        "Search current official sources and create a downloadable PDF with citations."
    ).tool_names or [])

    assert {"search_web", "open_url", "browse_page", "create_pdf"} <= names


def test_negated_email_action_does_not_select_email_capabilities():
    names = set(route_request(
        "Search current official sources and create a PDF report. Do not send email."
    ).tool_names or [])

    assert {"search_web", "open_url", "create_pdf"} <= names
    assert {"send_email", "search_email", "read_email", "summarize_unread_emails"}.isdisjoint(names)


def test_intent_helper_retrieves_capabilities_from_metadata():
    names = tool_names_for_intent("Find a job and prepare a CV")
    assert {"search_jobs", "generate_cv_for_job"} <= set(names)


def test_job_request_routes_to_research_not_coding():
    """Tech-stack keywords in a career context must not trigger coding tools."""
    r = route_request(
        "My skills: Python (FastAPI), JavaScript (React, Node.js, Express, "
        "REST APIs, cloud deployment (Vercel, Render). Search for software dev "
        "jobs in Kenya."
    )
    assert r.intent == "research"
    assert r.tool_names is not None
    assert "edit_code" not in r.tool_names
    assert "search_jobs" in r.tool_names


def test_explicit_call_about_application_routes_to_action_task():
    """Communication actions must not be swallowed by job research routing."""
    result = classify_intent("Call +254700000101 and ask about my application")
    assert result.intent == "task"


def test_public_profile_request_routes_to_research_not_generic_task():
    result = classify_intent("Check everything about Parmenas Oduol")
    assert result.intent == "research"
    assert "person_search" in result.signals
    assert extract_person_search_subject("Check everything about Parmenas Oduol") == "Parmenas Oduol"


def test_send_application_is_confirm_level():
    """send_application must require user approval (PermissionLevel.CONFIRM)."""
    spec = real_registry.get("send_application")
    assert spec is not None, "send_application not registered"
    assert spec.permission == PermissionLevel.CONFIRM


def test_report_and_cv_workflow_discovers_required_tools_without_application_send():
    request = (
        "My name is Parmenas Otieno Oduol. I am studying BSc Mechanical "
        "Engineering in Mombasa, Kenya. My skills include Python, FastAPI, "
        "JavaScript, React, Node.js, Ollama, GitHub, Vercel, and Render. "
        "Search the web for active software development roles or mechanical "
        "engineering internships in Kenya. Select 2 matching job listings and "
        "create a tailored CV for each role. Package the results into a "
        "structured summary report document. Send the report and CVs by email "
        'with the subject "Matched Job Applications & Tailored CVs".'
    )
    names = set(route_request(request).tool_names or [])
    assert {
        "search_web", "generate_cv_for_job", "create_docx", "send_email",
    } <= names
    assert {
        "send_application", "search_buses", "search_email", "search_code",
        "edit_code", "read_email",
    }.isdisjoint(names)


def test_report_email_does_not_bind_mailbox_search_or_calendar_actions():
    names = set(route_request(
        "Create a technical report about local LLM production and email it."
    ).tool_names or [])
    assert {"create_docx", "send_email"} <= names
    assert {
        "search_email", "read_email", "summarize_unread_emails", "create_event",
        "send_application",
    }.isdisjoint(names)


def test_emailing_report_does_not_select_mailbox_search_tools():
    names = set(route_request(
        "Search for relevant jobs, prepare a tailored CV, and email a report."
    ).tool_names or [])
    assert {"search_jobs", "generate_cv_for_job", "send_email"} <= names
    assert {
        "search_email", "read_email", "summarize_unread_emails",
    }.isdisjoint(names)


def test_direct_email_binds_send_not_unrequested_mailbox_operations():
    names = set(route_request(
        "Send an email to person@example.com and ask how they are doing."
    ).tool_names or [])
    assert "send_email" in names
    assert {"search_email", "read_email", "summarize_unread_emails"}.isdisjoint(names)


@pytest.mark.parametrize("app_request", ["open youtube", "Please launch the Calculator app."])
def test_local_app_open_isolated_from_unrelated_hotel_context(app_request):
    routing = route_request(
        app_request,
        context_text=(
            "The user asked for a hotel search. Search hotels and book a hotel "
            "in Paris using search_hotels and book_hotel."
        ),
    )

    assert routing.intent == "task"
    assert "local_app_control" in routing.signals
    expected_tool = "open_youtube" if app_request == "open youtube" else "open_local_app"
    assert expected_tool in (routing.tool_names or [])
    if expected_tool == "open_youtube":
        assert "open_local_app" not in (routing.tool_names or [])
    assert {"search_hotels", "book_hotel", "open_url", "browse_page"}.isdisjoint(
        routing.tool_names or []
    )


def test_generic_open_app_without_name_is_not_treated_as_a_launch_command():
    assert "open_local_app" not in (
        route_request("open any app").tool_names or []
    )
