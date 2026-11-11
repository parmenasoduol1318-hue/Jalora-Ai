"""PHASE K (§27, §70-72) tests: feedback pipeline, preference extraction,
workflow memory, and settings round-trip.

Covers:
  * Feedback -> preference promotion (rule-based, deterministic)
  * No contradictions: storing "concise" removes existing "verbose"
  * No duplicates: identical preference is not stored twice
  * Workflow pattern recording on success and failure
  * Workflow context injection into plan guidance
  * Settings round-trip (PATCH /api/settings)
  * Preference context visible in system prompt construction
"""
import asyncio
import json

import pytest
from sqlalchemy import select

from app import models as _models  # noqa: F401 - ensure all tables registered
from app.agents.prompts import build_system_prompt
from app.database.session import SessionLocal
from app.models.feedback import Feedback
from app.models.memory import Memory
from app.models.task import Task
from app.models.user import User
from app.models.workflow import WorkflowPattern
from app.services import preference_extractor, workflow_memory
from app.services.memory_service import update_memory


def _make_user(db, email="phasek@test.com", name="Phase K", prefs=None):
    user = User(email=email, name=name, password_hash="hashed", prefs=prefs or {})
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_task(db, user_id, goal="Test task"):
    task = Task(user_id=user_id, goal=goal, status="running")
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _get_prefs(db, user_id):
    return list(db.scalars(
        select(Memory).where(
            Memory.user_id == user_id, Memory.kind == "preference",
        )
    ))


# ---------------------------------------------------------------------------
# Feedback -> preference promotion (§27, §70, §72)
# ---------------------------------------------------------------------------

def test_feedback_promoted_to_preference_memory():
    """Two pieces of negative feedback on length -> a preference memory is stored."""
    with SessionLocal() as db:
        user = _make_user(db, email="promo@test.com")
        task = _make_task(db, user.id, goal="Write a report")
        # Two negative feedback items about verbosity
        for content in ["too verbose", "too verbose"]:
            fb = Feedback(
                user_id=user.id, task_id=task.id,
                sentiment="negative", content=content,
            )
            db.add(fb)
        db.commit()

        result = asyncio.run(preference_extractor.extract_preferences(
            db, user_id=user.id, task_id=task.id,
        ))
        assert len(result.promoted) == 1
        assert result.promoted[0]["scope"] == "response_length"
        # A preference memory should exist in the DB
        mems = _get_prefs(db, user.id)
        assert len(mems) == 1
        assert "concise" in mems[0].content


def test_single_feedback_not_promoted():
    """Only one feedback item -- below min_repetitions=2, no promotion."""
    with SessionLocal() as db:
        user = _make_user(db, email="single@test.com")
        task = _make_task(db, user.id, goal="Single feedback")
        fb = Feedback(user_id=user.id, task_id=task.id,
                      sentiment="negative", content="too verbose")
        db.add(fb)
        db.commit()
        result = asyncio.run(preference_extractor.extract_preferences(
            db, user_id=user.id, task_id=task.id,
        ))
        assert len(result.promoted) == 0


def test_no_promotion_on_positive_feedback():
    """Positive feedback should not trigger preference extraction."""
    with SessionLocal() as db:
        user = _make_user(db, email="pos@test.com")
        task = _make_task(db, user.id, goal="Positive feedback")
        for _ in range(3):
            fb = Feedback(user_id=user.id, task_id=task.id,
                          sentiment="positive", content="great job")
            db.add(fb)
        db.commit()
        result = asyncio.run(preference_extractor.extract_preferences(
            db, user_id=user.id, task_id=task.id,
        ))
        assert len(result.promoted) == 0


def test_no_duplicate_preference_stored():
    """Running extract twice should not create duplicate preference memories
    for the same scope."""
    with SessionLocal() as db:
        user = _make_user(db, email="dup@test.com")
        task = _make_task(db, user.id, goal="Dedup test")
        for _ in range(2):
            fb = Feedback(user_id=user.id, task_id=task.id,
                          sentiment="negative", content="too casual")
            db.add(fb)
        db.commit()
        # First extraction
        r1 = asyncio.run(preference_extractor.extract_preferences(
            db, user_id=user.id, task_id=task.id,
        ))
        assert len(r1.promoted) == 1
        # Second extraction on same task
        r2 = asyncio.run(preference_extractor.extract_preferences(
            db, user_id=user.id, task_id=task.id,
        ))
        mems = _get_prefs(db, user.id)
        tone_mems = [m for m in mems if (m.meta or {}).get("scope") == "tone"]
        assert len(tone_mems) <= 1


# ---------------------------------------------------------------------------
# Contradiction reconciliation (§72)
# ---------------------------------------------------------------------------

def test_contradiction_removes_old_preference():
    """Storing 'concise' preference should remove an existing 'verbose' one."""
    with SessionLocal() as db:
        user = _make_user(db, email="contra@test.com")
        # Store a verbose preference first
        asyncio.run(update_memory(
            db, user_id=user.id, content="verbose", kind="preference",
            source="test", scope="response_length",
        ))
        # Store a concise preference -- should remove the verbose one
        asyncio.run(update_memory(
            db, user_id=user.id, content="concise", kind="preference",
            source="test", scope="response_length",
        ))
        mems = _get_prefs(db, user.id)
        contents = [m.content for m in mems]
        assert "concise" in contents
        assert "verbose" not in contents


def test_contradiction_different_scope_kept():
    """Preferences with different scopes are not considered contradictions."""
    with SessionLocal() as db:
        user = _make_user(db, email="scope@test.com")
        asyncio.run(update_memory(
            db, user_id=user.id, content="verbose", kind="preference",
            scope="response_length",
        ))
        asyncio.run(update_memory(
            db, user_id=user.id, content="formal", kind="preference",
            scope="tone",
        ))
        mems = _get_prefs(db, user.id)
        assert len(mems) == 2
        contents = {m.content for m in mems}
        assert "verbose" in contents
        assert "formal" in contents


# ---------------------------------------------------------------------------
# Workflow memory (§71)
# ---------------------------------------------------------------------------

def test_record_success_stores_fingerprint():
    with SessionLocal() as db:
        user = _make_user(db, email="wfs@test.com")
        pattern = workflow_memory.record_success(
            db, user_id=user.id, goal="Fix failing test",
            fingerprint=["inspect_code", "edit_code", "run_tests"],
        )
        assert pattern.pattern_type == "success"
        assert "test" in pattern.label
        fp = json.loads(pattern.fingerprint)
        assert fp == ["inspect_code", "edit_code", "run_tests"]


def test_record_success_increments_hit_count():
    """Same fingerprint -> increment, not duplicate."""
    with SessionLocal() as db:
        user = _make_user(db, email="wfh@test.com")
        workflow_memory.record_success(
            db, user_id=user.id, goal="Task A",
            fingerprint=["step1", "step2"],
        )
        workflow_memory.record_success(
            db, user_id=user.id, goal="Task B",
            fingerprint=["step1", "step2"],
        )
        patterns = list(db.scalars(
            select(WorkflowPattern).where(
                WorkflowPattern.user_id == user.id,
                WorkflowPattern.pattern_type == "success",
            )
        ))
        assert len(patterns) == 1
        assert patterns[0].hit_count == 2


def test_record_failure_stores_context():
    with SessionLocal() as db:
        user = _make_user(db, email="wff@test.com")
        pattern = workflow_memory.record_failure(
            db, user_id=user.id, goal="Scrape a JS site",
            failing_tool="open_url",
            cause="site requires JavaScript",
            replacement="browse_page",
        )
        assert pattern.pattern_type == "failure"
        assert pattern.failing_tool == "open_url"
        assert "JavaScript" in pattern.cause
        assert pattern.replacement == "browse_page"


def test_workflow_failure_pattern_injected_into_context():
    """A recorded failure for a similar goal should appear in build_workflow_context."""
    with SessionLocal() as db:
        user = _make_user(db, email="wfi@test.com")
        workflow_memory.record_failure(
            db, user_id=user.id, goal="Open a JS-heavy website",
            failing_tool="open_url",
            cause="site requires JavaScript to render",
            replacement="browse_page",
        )
        context = workflow_memory.build_workflow_context(
            db, user_id=user.id, context="I need to open a JS-heavy website",
        )
        assert "open_url" in context
        assert "browse_page" in context


def test_workflow_success_pattern_injected_into_context():
    """A recorded success fingerprint should appear in build_workflow_context."""
    with SessionLocal() as db:
        user = _make_user(db, email="wfs2@test.com")
        workflow_memory.record_success(
            db, user_id=user.id, goal="Fix the broken test in my project",
            fingerprint=["inspect_code", "edit_code", "run_tests"],
        )
        context = workflow_memory.build_workflow_context(
            db, user_id=user.id, context="Fix the broken test in my project",
        )
        assert "inspect_code" in context
        assert "run_tests" in context


# ---------------------------------------------------------------------------
# Preference extraction rule patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("feedback_text,expected_scope", [
    ("This is too verbose, please be more concise", "response_length"),
    ("Too casual! Be more professional", "tone"),
    ("Please reply in Swahili", "language"),
    ("Prefer OpenAI for faster responses", "preferred_provider"),
])
def test_extract_preference_rules(feedback_text, expected_scope):
    result = preference_extractor._extract_preference(feedback_text)
    assert result is not None
    assert result[0] == expected_scope


# ---------------------------------------------------------------------------
# System prompt reflection (§27 acceptance)
# ---------------------------------------------------------------------------

def test_preference_context_visible_in_system_prompt():
    """A stored preference memory must appear in build_system_prompt when
    passed via preference_context -- the acceptance criterion: repeatedly
    correcting Jalora's tone visibly changes the system prompt context."""
    with SessionLocal() as db:
        user = _make_user(db, email="sp@test.com")
        asyncio.run(update_memory(
            db, user_id=user.id, content="concise, brief answers",
            kind="preference", source="test", scope="response_length",
        ))
        mems = _get_prefs(db, user.id)
        prefs_list = list(mems)
        ctx = preference_extractor.build_preference_context(prefs_list)
        assert "concise" in ctx
        prompt = build_system_prompt(
            memory_context="", preference_context=ctx,
            user_name=user.name, locale="en",
        )
        assert "concise" in prompt
        assert "Learned user preferences" in prompt


# ---------------------------------------------------------------------------
# Settings round-trip (PATCH /api/settings)
# ---------------------------------------------------------------------------

def _auth_headers(client, email="settingsk@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Settings User",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_settings_round_trip_preferences(client):
    """PATCH /api/settings stores preference fields; GET returns them."""
    headers = _auth_headers(client)
    # Patch new preference values
    resp = client.patch("/api/settings", headers=headers, json={
        "response_length": "concise",
        "tone": "casual",
        "timezone": "America/New_York",
        "preferred_provider": "openai",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["response_length"] == "concise"
    assert data["tone"] == "casual"
    assert data["timezone"] == "America/New_York"
    assert data["preferred_provider"] == "openai"

    # GET should return the same values
    resp2 = client.get("/api/settings", headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["response_length"] == "concise"
    assert data2["tone"] == "casual"
    assert data2["timezone"] == "America/New_York"
    assert data2["preferred_provider"] == "openai"


def test_settings_defaults(client):
    """Fresh user gets default preference values."""
    headers = _auth_headers(client, "defs@test.com")
    resp = client.get("/api/settings", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["response_length"] == "balanced"
    assert data["tone"] == "professional"
    assert data["timezone"] == "Africa/Nairobi"
    assert data["preferred_provider"] == "auto"


# ---------------------------------------------------------------------------
# Feedback API (§70)
# ---------------------------------------------------------------------------

def test_create_and_list_feedback(client):
    """POST /api/feedback then GET /api/feedback returns the entry."""
    headers = _auth_headers(client, "fb@test.com")
    # Submit feedback
    fb_payload = {
        "task_id": None,
        "sentiment": "negative",
        "content": "too verbose response",
    }
    resp = client.post("/api/feedback", json=fb_payload, headers=headers)
    assert resp.status_code == 201, resp.text
    fb_data = resp.json()
    assert fb_data["sentiment"] == "negative"
    assert "too verbose" in fb_data["content"]

    # List feedback
    resp2 = client.get("/api/feedback", headers=headers)
    assert resp2.status_code == 200
    feedbacks = resp2.json()
    assert len(feedbacks) >= 1
    assert feedbacks[0]["sentiment"] == "negative"


def test_feedback_requires_auth(client):
    """POST /api/feedback without auth -> 401."""
    resp = client.post("/api/feedback", json={
        "sentiment": "positive", "content": "nice",
    })
    assert resp.status_code == 401
