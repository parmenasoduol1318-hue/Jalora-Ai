"""Tests for job search and CV generation tools (spec §J).

These tests verify that:
  * All job tools are registered with the shared ``ToolRegistry``.
  * The routing layer exposes the right tools for job-search intents.
  * ``search_jobs`` delegates to ``search_service.search``.
  * ``send_application`` is flagged ``CONFIRM`` (approval-gated).
"""
from __future__ import annotations

import asyncio

import pytest

from app.tools import registry
from app.tools.base import PermissionLevel, ToolRegistry
from app.tools.routing import route_request, tool_names_for_intent
from app.errors import ToolResult


# --- registration -----------------------------------------------------------

ALL_JOB_TOOLS = [
    "search_jobs",
    "get_job_posting",
    "list_user_jobs",
    "save_job_posting",
    "list_qualifications",
    "add_qualification",
    "create_cv_template",
    "generate_cv_for_job",
    "create_application_draft",
    "review_application_draft",
    "send_application",
    "list_application_drafts",
    "list_applications",
]


@pytest.mark.parametrize("tool_name", ALL_JOB_TOOLS)
def test_each_job_tool_is_registered(tool_name):
    """Every job tool must be in the shared registry."""
    spec = registry.get(tool_name)
    assert spec is not None, f"{tool_name} is not registered"


def test_job_tools_use_shared_registry_not_custom_orchestration():
    """The job tools must be registered through the same ToolRegistry used by
    email, voice, calendar, etc. — no custom orchestration per capability."""
    from app.tools import registry as reg
    assert isinstance(reg, ToolRegistry)


def test_send_application_requires_approval():
    """send_application must be permission level CONFIRM (approvals are
    mandatory before sending an email on the user's behalf)."""
    spec = registry.get("send_application")
    assert spec is not None
    assert spec.permission == PermissionLevel.CONFIRM


def test_search_and_read_tools_are_auto_or_notify():
    """Search/CV/list tools should be AUTO or NOTIFY, not CONFIRM."""
    auto_or_notify = {PermissionLevel.AUTO, PermissionLevel.NOTIFY}
    for name in ALL_JOB_TOOLS:
        if name == "send_application":
            continue
        spec = registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.permission in auto_or_notify, (
            f"{name} has unexpected permission {spec.permission}"
        )


# --- routing integration ----------------------------------------------------

def test_tool_names_for_intent_job_search_binds_only_search_tools():
    names = tool_names_for_intent("Find software developer jobs in Kenya")
    assert names is not None
    assert "search_jobs" in names
    assert "generate_cv_for_job" not in names


def test_generic_chat_does_not_expose_job_tools():
    names = tool_names_for_intent("Hello, how are you?")
    assert names is not None
    assert "search_jobs" not in names
    assert "generate_cv_for_job" not in names


def test_route_request_job_search_exposes_job_tools():
    r = route_request("Find software developer jobs in Kenya")
    assert r.intent == "research"
    assert r.tool_names is not None
    assert "search_jobs" in r.tool_names
    assert "generate_cv_for_job" not in r.tool_names
    assert "send_application" not in r.tool_names


# --- search_jobs delegates to search_service --------------------------------

def test_search_jobs_delegates_to_search_service(monkeypatch):
    """search_jobs must call search_service.search and return its results."""
    from app.tools import job_tools

    # search_jobs calls _user_id() which needs a tool context; bypass it.
    monkeypatch.setattr(job_tools, "_user_id", lambda: "test_user")

    captured: dict = {}

    class _FakeResult:
        title = "Software Engineer at Acme"
        url = "https://example.com/job/1"
        snippet = "We are hiring software engineers..."

    async def _fake_search(query, max_results=5, provider=None):
        captured["query"] = query
        captured["max_results"] = max_results
        return [_FakeResult()]

    monkeypatch.setattr(job_tools.search_service, "search", _fake_search)

    result = asyncio.run(job_tools.search_jobs(
        keywords="software engineer", location="Kenya", max_results=5
    ))

    assert result.success, result.error
    assert captured["query"] == "software engineer Kenya"
    assert captured["max_results"] == 10  # job_tools doubles max_results
    assert result.data["count"] == 1
    assert result.data["jobs"][0]["title"]  # non-empty title
    assert result.data["jobs"][0]["url"] == "https://example.com/job/1"
    assert "id" not in result.data["jobs"][0]


def test_search_jobs_requires_keywords():
    """search_jobs must reject an empty keywords argument."""
    from app.tools import job_tools

    result = asyncio.run(job_tools.search_jobs(keywords="", location="Kenya"))
    assert not result.success
    assert result.error.code == "INVALID_INPUT"


def test_generic_search_pages_are_not_returned_as_verified_jobs():
    from app.tools import job_tools

    assert job_tools._listing_status("https://www.google.com/search?q=software+engineer") == "search_page"
    assert job_tools._listing_status("https://jobs.example.org/job/123", "Software Engineer") == "candidate"


def test_search_jobs_marks_results_unverified_until_opened(monkeypatch):
    from app.tools import job_tools

    monkeypatch.setattr(job_tools, "_user_id", lambda: "test_user")

    class _Result:
        title = "Software Engineer - Acme"
        url = "https://jobs.example.org/job/123"
        snippet = "Role details"

    async def _search(query, max_results=5, provider=None):
        return [_Result()]

    monkeypatch.setattr(job_tools.search_service, "search", _search)
    result = asyncio.run(job_tools.search_jobs(keywords="software engineer", location="Kenya"))
    assert result.success
    assert result.data["jobs"][0]["verified"] is False
    assert result.data["jobs"][0]["listing_status"] == "candidate"


# --- generate_cv_for_job ----------------------------------------------------

def test_generate_cv_for_job_not_found_returns_error(monkeypatch):
    """If the job isn't saved in the DB, the tool should fail gracefully."""
    from app.tools import job_tools
    from unittest.mock import MagicMock

    monkeypatch.setattr(job_tools, "_user_id", lambda: "test_user")

    # Mock SessionLocal so the DB query returns no matching job.
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = None
    fake_ctx = MagicMock()
    fake_ctx.__enter__ = lambda self: fake_db
    fake_ctx.__exit__ = lambda self, *a: None
    monkeypatch.setattr(job_tools, "SessionLocal", lambda: fake_ctx)

    result = asyncio.run(job_tools.generate_cv_for_job(job_id="nonexistent"))
    assert not result.success
    assert result.error.code == "JOB_NOT_FOUND"
    assert "get_job_posting" in result.message
    assert "persisted job ID" in result.message


# --- ToolResult shape --------------------------------------------------------

def test_job_tools_have_correct_imports():
    """Ensure the import path for search_service is correct (no circular import)."""
    from app.tools import job_tools
    assert hasattr(job_tools, "search_service")
    assert hasattr(job_tools, "search_jobs")
    assert hasattr(job_tools, "generate_cv_for_job")
    assert hasattr(job_tools, "send_application")
