"""Phase 1: Intent detection tests.

Verifies that ``classify_intent`` and ``route_request`` correctly distinguish
chat, web_search, email, math, coding, science, and research intents for
10 representative user queries.

Each test also verifies that the routed tool set for the intent includes the
tools the model is expected to use (e.g. ``send_email`` for email intent,
``search_web`` for web_search intent).
"""
from __future__ import annotations

import pytest

from app.agents.intent import classify_intent, needs_outreach_style_clarification
from app.agents.prompts import (
    EMAIL_BRIEF,
    KENYAN_CURRICULUM_BRIEF,
    RESEARCH_BRIEF,
    SCIENCE_BRIEF,
    WEB_SEARCH_BRIEF,
    build_system_prompt,
)
from app.tools.routing import route_request


# ---------------------------------------------------------------------------
# 10 Phase 1 example queries and their expected intents
# ---------------------------------------------------------------------------
PHASE1_EXAMPLES = [
    ("What is the latest news about AI?",          "web_search"),
    ("Search the web for quantum computing",       "web_search"),
    ("Send an email to john@example.com about the quarterly report", "email"),
    ("Email my boss about the quarterly report",   "email"),
    ("What is 2+2?",                               "math"),
    ("Apply Ohm's law for 12 V and 4 ohm",          "science"),
    ("How do I fix a leaky faucet?",               "web_search"),
    ("Research the causes of climate change",      "research"),
    ("Write a Python function to sort a list",     "coding"),
    ("Tell me a joke",                             "chat"),
]


def test_phase1_examples_classify_correctly():
    """All 10 Phase 1 example queries must classify to the expected intent."""
    for text, expected in PHASE1_EXAMPLES:
        result = classify_intent(text)
        assert result.intent == expected, (
            f"FAIL: {text!r}\n"
            f"  expected intent={expected!r}\n"
            f"  got      intent={result.intent!r}, signals={result.signals}"
        )


def test_phase1_routing_returns_correct_tools():
    """Each Phase 1 intent must resolve to a non-empty focused tool set
    (or None for full-registry intents like task)."""
    for text, expected_intent in PHASE1_EXAMPLES:
        routing = route_request(text)
        assert routing.intent == expected_intent, (
            f"{text!r}: expected intent {expected_intent!r}, got {routing.intent!r}"
        )


def test_web_research_briefs_require_exact_value_extraction():
    for brief in (RESEARCH_BRIEF, WEB_SEARCH_BRIEF):
        assert "exact" in brief.lower()
        assert "unit" in brief.lower()
        assert "reporting period" in brief.lower() or "effective date" in brief.lower()
        assert "boilerplate" in brief.lower()
        assert (
            "rather than" in brief.lower()
            or "instead of guessing" in brief.lower()
        )


def test_transit_briefs_require_route_verification_and_plausible_durations():
    prompt = build_system_prompt() + RESEARCH_BRIEF + WEB_SEARCH_BRIEF

    assert "specific route" in prompt
    assert "Never invent operators" in prompt
    assert "geographically plausible" in prompt
    assert "historical" in prompt


def test_kenyan_curriculum_requests_use_official_source_research():
    request = (
        "Let Jalora be updated on all the Kenyan syllabus from primary to university"
    )
    result = classify_intent(request)
    routing = route_request(request)

    assert result.intent == "research"
    assert "kenyan_curriculum" in result.signals
    assert routing.intent == "research"
    assert routing.tool_names is not None
    assert "search_web" in routing.tool_names
    assert "open_url" in routing.tool_names
    assert "KICD" in KENYAN_CURRICULUM_BRIEF
    assert "KNEC" in KENYAN_CURRICULUM_BRIEF
    assert "never invent a complete syllabus" in KENYAN_CURRICULUM_BRIEF


def test_base_prompt_requires_private_reasoning_and_natural_interaction():
    prompt = build_system_prompt()

    assert "Do not expose hidden chain-of-thought" in prompt
    assert "Do not write hidden chain-of-thought" in prompt
    assert "acknowledge the user's actual concern" in prompt
    assert "without pretending to have human feelings" in prompt
    assert "reason inside <thinking> tags" not in prompt
    assert "Account identity" in prompt


def test_chat_language_follows_latest_message_not_interface_locale():
    prompt = build_system_prompt(locale="sw")

    assert "latest user message" in prompt
    assert "interface locale, profile preferences, location" in prompt
    assert "Preferred interface language code" not in prompt


@pytest.mark.parametrize("user_text", [
    "Explain photosynthesis in simple terms",
    "How does plate tectonics work?",
    "Explain epidemiology.",
    "What is stellar evolution?",
    "What is an exoplanet?",
    "Explain the basics of social science",
])
def test_science_questions_across_disciplines_use_science_intent(user_text):
    assert classify_intent(user_text).intent == "science"


def test_science_brief_covers_broad_evidence_based_safe_explanations():
    assert "Earth, space, environmental, health, computing, or social science" in SCIENCE_BRIEF
    assert "scientific consensus" in SCIENCE_BRIEF
    assert "offer a safer way" in SCIENCE_BRIEF


# ---------------------------------------------------------------------------
# Task-specific capability discovery verification
# ---------------------------------------------------------------------------
def test_web_search_binding_has_search_tools():
    names = route_request("Search the web for local news and open the article pages").tool_names
    assert names is not None
    assert "search_web" in names
    assert "open_url" in names
    assert "browse_page" in names


def test_email_binding_has_email_tools():
    names = route_request("Compose an email, read my inbox, and send a message").tool_names
    assert names is not None
    assert "send_email" in names
    assert "compose_email" in names
    assert "read_email" in names


def test_math_binding_has_calculate():
    names = route_request("Calculate the value and verify the math").tool_names
    assert names is not None
    assert "calculate" in names
    assert "verify_math" in names


def test_science_binding_has_compute_and_plot():
    names = route_request("Calculate the equation and plot the function").tool_names
    assert names is not None
    assert "calculate" in names
    assert "plot_function" in names


def test_coding_binding_has_coding_tools():
    names = route_request("Edit the code and run the tests").tool_names
    assert names is not None
    assert "edit_code" in names
    assert "run_tests" in names


def test_chat_does_not_bind_editing_only_tools():
    """A greeting should not discover unrelated editing or browsing tools."""
    names = route_request("Hello there").tool_names
    assert names is not None
    assert "browser_fill" not in names
    assert "edit_code" not in names


# ---------------------------------------------------------------------------
# Regression guards: the two originally-failing cases
# ---------------------------------------------------------------------------
def test_apply_ohms_law_routes_to_science_not_research():
    """Bare 'apply' must not short-circuit to the job/research career path."""
    result = classify_intent("apply ohm's law for 12 V and 4 ohm")
    assert result.intent == "science"


def test_send_email_is_bound_for_email_intent():
    """route_request for an email query must expose send_email."""
    routing = route_request("Send an email to john@example.com about the report")
    assert routing.intent == "email"
    assert routing.tool_names is not None
    assert "send_email" in routing.tool_names


def test_send_payment_not_in_chat_tools():
    """An email request must not discover money-movement capabilities."""
    routing = route_request("Send an email about the project report")
    assert routing.tool_names is not None
    assert "send_email" in routing.tool_names
    assert "send_payment" not in routing.tool_names


def test_system_prompt_distinguishes_email_drafts_from_sending():
    prompt = build_system_prompt()
    assert "For email drafts, call compose_email and do not send them." in prompt
    assert "Call send_email or" in prompt
    assert "when the user explicitly asks to send" in prompt
    assert "ALWAYS call send_email" not in prompt
    assert "Choosing a style is not permission to send." in prompt
    assert "recipient-specific message for each" in prompt
    assert "request_user_input to present" in prompt
    assert "handle one recipient at a time with compose_email" in prompt
    assert "never use either batch tool" in prompt
    assert "do not repeat the superseded name" in prompt
    assert "wait for approval of the complete batch before sending" in EMAIL_BRIEF
    assert "search_public_contacts" in prompt
    assert "all requested names in its organizations list" in prompt
    assert "one recipient-specific send_email" in prompt
    assert "after each approval or rejection" in prompt
    assert "preserve the" in prompt
    assert "subject verbatim" in prompt
    assert "metadata stripping" in prompt
    assert 'Sign as "Lead Developer"' in prompt
    assert "search the public web with search_public_contacts" in prompt
    assert "call compose_email" in EMAIL_BRIEF
    assert "Call send_email or send_email_batch only when the user explicitly asks" in EMAIL_BRIEF
    assert "ALWAYS call send_email" not in EMAIL_BRIEF


def test_multi_recipient_outreach_clarification_requires_a_message_style():
    assert needs_outreach_style_clarification(
        "Draft outreach emails for each target organization."
    )
    assert not needs_outreach_style_clarification(
        "Draft a different tailored email for each organization."
    )
    assert not needs_outreach_style_clarification(
        "Draft the same email for all target organizations."
    )
    assert needs_outreach_style_clarification(
        "Draft outreach emails to multiple organizations."
    )
    assert needs_outreach_style_clarification(
        "Draft outreach emails to different organizations."
    )
    assert not needs_outreach_style_clarification(
        "Draft a custom outreach email for each organization."
    )
    assert not needs_outreach_style_clarification(
        "Send one email for each organization."
    )
    assert not needs_outreach_style_clarification(
        "Send one email to each recipient."
    )


def test_public_organization_send_request_uses_contact_search_without_style_prompt():
    request = (
        "Perform an online lookup to retrieve the current, valid contact/support "
        "email addresses for the following organizations operating in Kenya and "
        "internationally. Once retrieved, draft and prepare an outreach email "
        "for each organization. TARGET ORGANIZATIONS: send the emails to these "
        "organisations: Transparency International Kenya, Code for Africa, "
        "Katiba Institute, Open Contracting Partnership, Kituo Cha Sheria, "
        "ARTICLE 19 Eastern Africa, Ethics and Anti-Corruption Commission, "
        "Global Innovation Fund. Search the emails and send them, for each "
        "organisation, only send in one email."
    )
    routing = route_request(request, intent_override="task")

    assert routing.intent == "research"
    assert "public_contact_search" in routing.signals
    assert "search_public_contacts" in (routing.tool_names or [])
    assert "search_web" in (routing.tool_names or [])
    assert needs_outreach_style_clarification(request) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "Fetch emails for the organisations listed and draft outreach.",
        "Search emails of the organisations and prepare individual drafts.",
        "Find the contact email addresses for these organizations.",
        "Find email addresses for Microsoft and Apple.",
        "Scrape public contact details for the listed companies.",
    ],
)
def test_organization_email_lookup_routes_to_public_web(utterance):
    result = classify_intent(utterance)
    assert "public_contact_search" in result.signals


def test_web_search_not_triggered_for_deep_research():
    """'Research the causes of climate change' must stay research, not web_search."""
    result = classify_intent("Research the causes of climate change")
    assert result.intent == "research"


def test_web_search_not_triggered_for_latest_news_without_question():
    """'latest physics news' must stay research (no question word = no web_search)."""
    result = classify_intent("latest physics news")
    assert result.intent == "research"


def test_search_the_web_for_research_papers_stays_research():
    """'Search the web for the latest AI research papers' has deep-research
    marker 'research' so it must NOT collapse to web_search."""
    result = classify_intent("Search the web for the latest AI research papers")
    assert result.intent == "research"


def test_write_python_function_routes_to_coding_not_task():
    """'Write a Python function' has coding signal 'function' so it must win
    over the generic 'write' task keyword."""
    result = classify_intent("Write a Python function to sort a list")
    assert result.intent == "coding"


def test_write_draft_proposal_stays_task():
    """'write a draft proposal' has no coding signals so it stays task."""
    result = classify_intent("write a draft proposal for the client")
    assert result.intent == "task"
