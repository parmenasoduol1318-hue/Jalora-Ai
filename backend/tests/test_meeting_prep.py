"""Tests for meeting preparation tools.

Run with: python -m pytest backend/tests/test_meeting_prep.py -v
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.services.meeting_prep import (
    MeetingPrepAssistant,
    get_meeting_assistant,
    AgendaItem,
    DocumentInfo,
    MeetingPrepPlan,
)


# ============================================================================
# Meeting Prep Assistant Tests
# ============================================================================

def test_meeting_prep_assistant_creation():
    """Test meeting prep assistant can be created."""
    assistant = MeetingPrepAssistant()
    assert assistant is not None


def test_get_meeting_assistant():
    """Test singleton pattern."""
    assistant1 = get_meeting_assistant()
    assistant2 = get_meeting_assistant()
    assert assistant1 is assistant2


# ============================================================================
# Meeting Creation Tests
# ============================================================================

def test_create_prep_plan():
    """Test creating a meeting preparation plan."""
    assistant = get_meeting_assistant()
    
    meeting_date = datetime.now(timezone.utc) + timedelta(days=2)
    plan = assistant.create_prep_plan(
        title="Q4 Planning Meeting",
        participants=["Alice", "Bob", "charlie@example.com"],
        meeting_date=meeting_date,
        duration_minutes=90
    )
    
    assert plan.title == "Q4 Planning Meeting"
    assert plan.duration_minutes == 90
    assert len(plan.participants) == 3


def test_create_agenda():
    """Test agenda creation from topics."""
    assistant = get_meeting_assistant()
    
    topics = ["Q4 Goals", "Budget Review", "Team Expansion"]
    agenda = assistant.create_agenda(
        title="Planning Meeting",
        topics=topics,
        duration_minutes=60
    )
    
    assert len(agenda) == len(topics) + 1  # +1 for wrap-up
    assert all(isinstance(a, AgendaItem) for a in agenda)


def test_gather_documents():
    """Test document gathering."""
    assistant = get_meeting_assistant()
    
    topics = ["Budget", "Goals", "Timeline"]
    docs = assistant.gather_documents(topics)
    
    assert len(docs) <= 3  # Limited to 3 topics
    assert all(isinstance(d, DocumentInfo) for d in docs)


# ============================================================================
# Summary Template Tests
# ============================================================================

def test_generate_summary_template():
    """Test summary template generation."""
    assistant = get_meeting_assistant()
    
    template = assistant.generate_summary_template(
        attendees=["Alice", "Bob", "Charlie"],
        key_decisions=["Approve budget", "Set timeline"]
    )
    
    assert "attendees" in template
    assert "decisions_made" in template
    assert len(template["attendees"]) == 3


# ============================================================================
# Reading Suggestions Tests
# ============================================================================

def test_suggest_reading():
    """Test reading suggestions generation."""
    assistant = get_meeting_assistant()
    
    suggestions = assistant.suggest_reading("Product Launch", ["Alice", "Bob"])
    
    assert isinstance(suggestions, list)
    assert len(suggestions) >= 1


# ============================================================================
# Dataclass Tests
# ============================================================================

def test_agenda_item_defaults():
    """Test AgendaItem default values."""
    item = AgendaItem(title="Test Topic")
    
    assert item.title == "Test Topic"
    assert item.time_allocated == 5
    assert item.importance == "normal"


def test_document_info_defaults():
    """Test DocumentInfo default values."""
    doc = DocumentInfo(name="Test Document")
    
    assert doc.name == "Test Document"
    assert doc.url == ""
    assert doc.relevance_score == 0.0


def test_meeting_prep_plan_structure():
    """Test MeetingPrepPlan structure."""
    plan = MeetingPrepPlan(
        title="Test Meeting",
        meeting_date=datetime.now(timezone.utc),
        duration_minutes=60,
        participants=[],
        agenda=[],
        documents=[],
        pre_tasks=[]
    )
    
    assert plan.title == "Test Meeting"
    assert plan.duration_minutes == 60


# ============================================================================
# Edge Cases
# ============================================================================

def test_create_plan_empty_participants():
    """Test creating plan with no participants."""
    assistant = get_meeting_assistant()
    
    plan = assistant.create_prep_plan(
        title="Solo Meeting",
        participants=[],
        meeting_date=datetime.now(timezone.utc) + timedelta(days=1)
    )
    
    assert plan.title == "Solo Meeting"
    assert len(plan.participants) == 0


def test_agenda_max_topics():
    """Test agenda handles too many topics."""
    assistant = get_meeting_assistant()
    
    # Provide more topics than will fit
    topics = [f"Topic {i}" for i in range(10)]
    agenda = assistant.create_agenda("Long Meeting", topics, duration_minutes=60)
    
    # Original topics list
    assert len(agenda) <= 11  # Topics + wrap-up, limited by duration


if __name__ == "__main__":
    pytest.main([__file__, "-v"])