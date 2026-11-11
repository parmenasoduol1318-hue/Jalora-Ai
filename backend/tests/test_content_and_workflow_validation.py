"""Deterministic regression tests for generated content and workflow gates."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agents.graph import _valid_research_sources
from app.services.content_validation import (
    find_unresolved_placeholders,
    validate_generated_text,
    validate_research_report,
)
from app.services import email as email_service
from app.services.email import generate_email_draft
from app.tools.job_tools import _has_vacancy_evidence, _parse_job_details


def test_placeholder_detection_ignores_real_markdown_links():
    text = "Read [the official source](https://example.test/source), then contact [Recipient's Name]."
    assert find_unresolved_placeholders(text) == ["[Recipient's Name]"]


@pytest.mark.parametrize(
    "text",
    [
        "{{recipient}}",
        "<Your Name>",
        "TODO: add the figures",
        "[Company Name]",
        "Deputy President: (See official portal)",
        "Prime CS: Name (or current officeholder per official listings)",
    ],
)
def test_generated_content_rejects_unresolved_markers(text):
    errors = validate_generated_text(text, field="body")
    assert errors
    assert "unresolved placeholders" in errors[0]


def test_generated_content_accepts_complete_text():
    assert validate_generated_text("The verified result is 42.", field="body") == []


def test_email_draft_preserves_current_request_instead_of_a_canned_task_template():
    draft = generate_email_draft(
        "contact@example.test",
        "Send an email to contact@example.test to ask whether they are going to church",
    )
    assert draft["valid"] is True
    assert "church" in draft["subject"].lower() or "church" in draft["body"].lower()
    assert "training your ai" not in draft["body"].lower()


def test_email_draft_keeps_review_workflow_out_of_message_and_signoff_separate():
    draft = generate_email_draft(
        "junioroduol@gmail.com",
        "Send an email to junioroduol@gmail.com and tell him that im ready "
        "now for the showing of my current app capabilities",
    )
    assert draft["valid"] is True
    assert draft["to"] == "junioroduol@gmail.com"
    assert "capabilities" in draft["subject"].lower()
    assert draft["body"] == (
        "Hi Junioroduol,\n\nI'm ready now to show you my current app capabilities."
    )
    assert draft["regards"] == "Regards,"
    assert "approval form" not in draft["body"].lower()


def test_email_draft_uses_polished_language_for_check_in_request():
    draft = generate_email_draft(
        "parmenasoduol1318@gmail.com",
        "Send an email to parmenasoduol1318@gmail.com to ask him how he is doing",
    )
    assert draft["valid"] is True
    assert draft["to"] == "parmenasoduol1318@gmail.com"
    assert draft["body"] == (
        "Hi Parmenasoduol,\n\nI hope you're doing well. I wanted to check in and "
        "see how you are doing."
    )
    assert "email to ask him how he is doing" not in draft["body"].lower()
    assert draft["regards"] == "Regards,"


def test_email_task_extracts_multiple_to_cc_and_bcc_recipients():
    draft = generate_email_draft(
        "",
        "Send an email to first@example.com and second@example.com "
        "cc copy@example.com and bcc hidden@example.com and tell them I am ready.",
    )
    assert draft["to"] == "first@example.com, second@example.com"
    assert draft["cc"] == "copy@example.com"
    assert draft["bcc"] == "hidden@example.com"
    assert "ready" in draft["body"].lower()


def test_email_draft_uses_selected_model_for_subject_and_body(monkeypatch):
    class FakeModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content=(
                '{"subject":"Ready to Share the Jalora Demo",'
                '"body":"I am ready to walk you through the app and its current capabilities.",'
                '"regards":"Warm regards,"}'
            ))

    class FakeRouter:
        def route(self, **kwargs):
            assert kwargs["provider_override"] == "openai"
            return SimpleNamespace(provider=SimpleNamespace(name="openai"))

        def get_model(self, _route):
            return FakeModel()

    monkeypatch.setattr(
        "app.services.model_router.get_model_router", lambda: FakeRouter()
    )
    draft = asyncio.run(email_service.generate_email_draft_with_model(
        "first@example.com",
        "Send an email to first@example.com and tell him I'm ready for the app demo",
        prefs={"preferred_provider": "openai"},
    ))
    assert draft["generation_method"] == "model"
    assert draft["subject"] == "Ready to Share the Jalora Demo"
    assert "walk you through" in draft["body"]
    assert draft["regards"] == "Regards,"


def test_directory_text_is_not_vacancy_evidence():
    parsed = {"title": "Careers", "company": "Example", "description": "Open roles"}
    assert not _has_vacancy_evidence("Careers | Open roles | Teams", parsed)


def test_individual_vacancy_requires_multiple_job_markers():
    parsed = {"title": "Backend Engineer", "company": "Example"}
    text = "Backend Engineer\nExample\nLocation: Remote\nRequirements: Python\nHow to apply: submit CV"
    assert _has_vacancy_evidence(text, parsed)


def test_job_details_use_browser_title_to_recover_company():
    text = (
        "Senior Backend Engineer\nResponsibilities: Build reliable APIs.\n"
        "Requirements: Python and FastAPI.\nHow to apply: Submit your CV."
    )
    parsed = _parse_job_details(
        text,
        "https://jobs.example.test/role/123",
        page_title="Senior Backend Engineer at Example Inc | Careers",
    )
    assert parsed["title"] == "Senior Backend Engineer"
    assert parsed["company"] == "Example Inc"
    assert _has_vacancy_evidence(text, parsed)


def test_research_artifact_requires_a_real_tracked_source():
    assert not _valid_research_sources([])
    assert not _valid_research_sources([{"url": "not-a-url"}])
    assert _valid_research_sources([{"url": "https://example.test/source"}])


def test_research_report_must_carry_source_citation():
    source = {"url": "https://example.test/source", "source_type": "page"}
    assert validate_research_report("Verified finding. https://example.test/source", [source]) == []
    assert validate_research_report("Verified finding without evidence.", [source])


def test_research_report_requires_an_opened_page_not_a_search_snippet():
    source = {
        "url": "https://example.test/source",
        "source_type": "search_result",
    }
    errors = validate_research_report(
        "Verified finding. https://example.test/source", [source]
    )
    assert errors == ["Open a source page before creating a research report."]


def test_research_report_must_cite_an_opened_page():
    sources = [
        {"url": "https://example.test/search", "source_type": "search_result"},
        {"url": "https://example.test/page", "source_type": "page"},
    ]
    errors = validate_research_report(
        "Verified finding. https://example.test/search", sources
    )
    assert errors == [
        "The report must cite at least one source page opened during this run."
    ]


def test_agent_task_with_web_sources_requires_research_evidence_for_artifacts():
    from app.agents.graph import _requires_research_evidence

    assert _requires_research_evidence(
        "task", [{"url": "https://example.test/result", "source_type": "search_result"}]
    )
    assert _requires_research_evidence("research", [])
    assert not _requires_research_evidence("task", [])


def test_opened_source_metadata_replaces_search_snippet_for_same_url():
    from app.agents.graph import _dedupe_sources

    merged = _dedupe_sources([
        {
            "title": "Search result title",
            "url": "https://example.test/source",
            "snippet": "Search snippet",
            "source_type": "search_result",
        },
        {
            "title": "Opened page title",
            "url": "https://example.test/source",
            "snippet": "Page content",
            "source_type": "page",
        },
    ])
    assert merged == [{
        "title": "Opened page title",
        "url": "https://example.test/source",
        "snippet": "Page content",
        "source_type": "page",
    }]
