"""Tests for the deterministic intent classifier (spec §4).

These tests guard against the original misclassification bug where job-search
requests containing tech-stack keywords ("Python", "React", "FastAPI") were
routed to the ``coding`` intent — triggering the coding workspace brief and
preventing the model from reaching research/job tools.
"""
from __future__ import annotations

import pytest

from app.agents.intent import IntentResult, classify_intent


# --- the original failure cases (BUG A + BUG B) -----------------------------

def test_job_search_in_kenya_routes_to_research():
    """This is the exact query that originally failed with a 15s search timeout
    and 'I'm sorry, but I couldn't find any active software development roles…'"""
    result = classify_intent("Find software dev jobs in Kenya")
    assert result.intent == "research"
    assert any("job" in s.lower() for s in result.signals)


def test_job_search_with_tech_stack_keywords_routes_to_research():
    """Tech nouns in a career context must NOT trigger coding intent."""
    text = (
        "I'm studying B.Sc. in Mechanical Engineering at Technical University "
        "of Mombasa. My skills: Python (FastAPI), JavaScript (React, Node.js, "
        "Express, Vite, Expo), REST API integrations, local AI setups (Ollama), "
        "cloud deployments (Vercel, Render), and Git/GitHub. "
        "Can you search for active software dev jobs in Kenya?"
    )
    result = classify_intent(text)
    assert result.intent == "research", (
        f"Expected 'research' but got '{result.intent}'. "
        f"Tech nouns in a job-search context must not trigger coding intent."
    )


def test_internship_search_routes_to_research():
    result = classify_intent("Looking for mechanical engineering internships in Kenya")
    assert result.intent == "research"
    assert any("internship" in s.lower() for s in result.signals)


def test_cv_generation_request_routes_to_research():
    result = classify_intent("Generate a tailored CV for a software engineer role")
    assert result.intent == "research"


def test_vacancy_search_routes_to_research():
    result = classify_intent("Show me entry-level IT vacancies in Nairobi")
    assert result.intent == "research"


@pytest.mark.parametrize(
    "query",
    ["search parmenas oduol", "search about parmenas oduol"],
)
def test_direct_name_search_gets_web_search_intent(query):
    result = classify_intent(query)
    assert result.intent == "web_search"
    assert result.signals == ("web_search",)


# --- coding intent still works for real coding requests ----------------------

def test_write_python_unit_tests_routes_to_coding():
    """A genuine coding request must still hit the coding intent.

    Note: 'Write Python unit tests' actually routes to 'task' because 'Write'
    matches the _TASK pattern first (task > coding in priority). We use a
    coding-only signal word instead so this genuinely exercises _CODING."""
    result = classify_intent("My unit tests are failing with a traceback")
    assert result.intent == "coding"
    assert any("test" in s.lower() or "trace" in s.lower() for s in result.signals)


def test_debug_traceback_routes_to_coding():
    result = classify_intent("Debug the traceback in my Flask app")
    assert result.intent == "coding"


def test_refactor_routes_to_coding():
    result = classify_intent("Refactor this function to use async/await")
    assert result.intent == "coding"


def test_syntax_error_routes_to_coding():
    result = classify_intent("I'm getting a syntaxerror on line 42")
    assert result.intent == "coding"


# --- other intents ----------------------------------------------------------

def test_greeting_routes_to_chat():
    result = classify_intent("Hello how are you")
    assert result.intent == "chat"


def test_schedule_reminder_routes_to_scheduling():
    result = classify_intent("remind me to write a python script comparing prices")
    assert result.intent == "scheduling"


def test_research_request_routes_to_research():
    """A non-job research request should also route to research."""
    result = classify_intent("Search the web for the latest AI research papers")
    assert result.intent == "research"


def test_current_toll_and_lending_rate_request_routes_to_live_research():
    result = classify_intent(
        "Calculate my yearly Nairobi Expressway toll charges and a KES "
        "5,000,000 loan using 2026 CBK commercial lending rates."
    )

    assert result.intent == "research"
    assert "live_data_lookup" in result.signals


def test_live_data_lookup_matches_time_marker_after_financial_term():
    result = classify_intent(
        "Compare CBK lending rates today and model the monthly repayments."
    )

    assert result.intent == "research"
    assert "live_data_lookup" in result.signals


def test_historical_lending_rate_arithmetic_does_not_force_live_research():
    result = classify_intent(
        "Calculate a loan repayment using the 2020 lending rate of 12%."
    )

    assert "live_data_lookup" not in result.signals
    assert result.intent == "math"
    assert "financial_calculation" in result.signals


def test_supplied_commercial_lending_rate_and_docx_request_routes_to_math():
    from app.agents.graph import _requires_research_evidence

    result = classify_intent(
        "Model a corporate project financing loan of KES 5,000,000 taken at "
        "the commercial bank average lending rate of 14.4% over a 3-year term "
        "(compounded monthly). Calculate the exact monthly amortized payment, "
        "total interest payable, and generate a Word document (.docx) named "
        '"Loan_Amortization_Schedule.docx" containing the complete itemized '
        "monthly table without aborting midway."
    )
    assert result.intent == "math"
    assert "financial_calculation" in result.signals
    assert "financial_calculation" in result.signals
    assert not _requires_research_evidence(result.intent, [])


def test_biomedical_hypothesis_routes_to_evidence_review():
    result = classify_intent(
        "Novel theoretical biomedical hypothesis: CRISPR-Cas13 RNA editing "
        "and LRAs for an HIV functional cure."
    )
    assert result.intent == "research"
    assert "biomedical_review" in result.signals


def test_biomedical_domain_without_a_hypothesis_keeps_existing_routing():
    result = classify_intent("What is HIV?")
    assert result.intent == "web_search"
    assert "biomedical_review" not in result.signals


def test_ambiguous_interstellar_message_request_routes_to_web_search():
    result = classify_intent("solve the 3i atlas messege to earth")
    assert result.intent == "web_search"


def test_claim_verification_routes_to_web_search():
    result = classify_intent("confirm claims about an et escaping area 51")
    assert result.intent == "web_search"


def test_public_organization_email_lookup_routes_to_research():
    result = classify_intent(
        "Retrieve current valid contact/support email addresses for organizations "
        "and draft outreach messages."
    )
    assert result.intent == "research"
    assert "public_contact_search" in result.signals


def test_public_email_lookup_with_call_to_action_still_routes_to_research():
    result = classify_intent(
        "Retrieve current contact email addresses for organizations, draft "
        "outreach, and include a clear call-to-action requesting a meeting."
    )

    assert result.intent == "research"
    assert "public_contact_search" in result.signals


def test_pasted_unfinished_public_email_searches_route_to_research():
    result = classify_intent(
        "I could not complete all planned task actions. These steps remain "
        "unfinished: Search email for Open Contracting Partnership contact email, "
        "Search email for Kituo Cha Sheria contact email, Search email for "
        "ARTICLE 19 Eastern Africa contact email, Search email for EACC Public "
        "Reporting Desk contact email, Search email for Global Innovation Fund "
        "contact email."
    )
    assert result.intent == "research"
    assert "public_contact_search" in result.signals


def test_pasted_email_or_web_contact_searches_route_to_research():
    result = classify_intent(
        "Search email or web for Open Contracting Partnership contact email, "
        "Search email or web for Kituo Cha Sheria contact email, Search email or "
        "web for ARTICLE 19 Eastern Africa contact email, Search email or web for "
        "EACC Public Reporting Desk contact email, Search email or web for "
        "Global Innovation Fund contact email."
    )
    assert result.intent == "research"
    assert "public_contact_search" in result.signals


def test_grouped_search_for_contact_emails_routes_to_research():
    result = classify_intent(
        "Search for contact emails for Transparency International Kenya, "
        "Code for Africa, Katiba Institute, Open Contracting Partnership, "
        "Kituo Cha Sheria, ARTICLE 19 Eastern Africa, EACC, and Global "
        "Innovation Fund."
    )
    assert result.intent == "research"
    assert "public_contact_search" in result.signals


def test_pasted_company_search_contact_steps_route_to_public_research():
    result = classify_intent(
        "I could not complete all planned task actions. These steps remain "
        "unfinished: Search companies for Open Contracting Partnership contact "
        "email, Search companies for Kituo Cha Sheria contact email, Search "
        "companies for ARTICLE 19 Eastern Africa contact email, Search companies "
        "for Ethics and Anti-Corruption Commission EACC contact email, Search "
        "companies for Global Innovation Fund GIF contact email."
    )
    assert result.intent == "research"
    assert "public_contact_search" in result.signals


def test_research_with_negated_email_does_not_route_to_email():
    result = classify_intent(
        "Search current official sources and create a PDF report. Do not send email."
    )
    assert result.intent == "research"


def test_create_draft_routes_to_task():
    """'write a draft' style messages route to task (not coding)."""
    result = classify_intent("write a draft proposal for the client")
    assert result.intent == "task"


@pytest.mark.parametrize(
    "prompt",
    [
        "generate a photo of a cup on a table",
        "Draw me a picture of a red bicycle",
        "Can you create a photorealistic image of a mountain lake?",
    ],
)
def test_image_generation_requests_get_a_dedicated_signal(prompt):
    result = classify_intent(prompt)
    assert result.intent == "task"
    assert "image_generation" in result.signals


def test_image_generation_signal_survives_early_job_classification():
    result = classify_intent("Generate an image illustrating an engineering job")
    assert result.intent == "research"
    assert "image_generation" in result.signals


@pytest.mark.parametrize(
    "prompt",
    [
        "How can I generate an image?",
        "Don't generate an image for this answer.",
        "Write a prompt for generating a picture of a cup.",
    ],
)
def test_image_generation_explanations_and_negations_do_not_request_generation(prompt):
    assert "image_generation" not in classify_intent(prompt).signals


# --- signal verification -----------------------------------------------------

def test_signals_are_populated_for_job_search():
    result = classify_intent("Find software dev jobs in Kenya")
    assert result.signals
    assert all(isinstance(s, str) for s in result.signals)


def test_classify_intent_returns_intent_result():
    result = classify_intent("some text")
    assert isinstance(result, IntentResult)
    assert hasattr(result, "intent")
    assert hasattr(result, "signals")


# --- edge cases --------------------------------------------------------------

def test_empty_string_routes_to_chat():
    result = classify_intent("")
    assert result.intent == "chat"


def test_whitespace_only_routes_to_chat():
    result = classify_intent("   ")
    assert result.intent == "chat"


def test_none_text_routes_to_chat():
    """The classifier should handle None gracefully, not crash."""
    result = classify_intent(None)  # type: ignore[arg-type]
    assert result.intent == "chat"
