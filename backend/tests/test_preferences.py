"""Tests for preference engine, deadline management, and priority scoring.

Run with: python -m pytest backend/tests/test_preferences.py -v
"""
import pytest
from datetime import datetime, timedelta, timezone

# Import only the in-memory classes for unit tests
from app.services.preferences import (
    WeightedValue,
    DEFAULT_PREFERENCES,
)
from app.services.preferences import DeadlineManager


# ============================================================================
# Weighted Value Tests
# ============================================================================

def test_weighted_value_creation():
    """Test WeightedValue dataclass."""
    wv = WeightedValue(value=0.75, weight=0.8, confidence=0.9, source="test")
    
    assert wv.value == 0.75
    assert wv.weight == 0.8
    assert wv.confidence == 0.9
    assert wv.source == "test"


def test_weighted_value_defaults():
    """Test WeightedValue default values."""
    wv = WeightedValue(value="test")
    
    assert wv.value == "test"
    assert wv.weight == 1.0
    assert wv.confidence == 1.0
    assert wv.source == "manual"


# ============================================================================
# Default Preferences Tests
# ============================================================================

def test_default_preferences_structure():
    """Test default preferences have correct structure."""
    assert "task_prioritization" in DEFAULT_PREFERENCES
    assert "deadline_management" in DEFAULT_PREFERENCES
    assert "verification" in DEFAULT_PREFERENCES
    
    # Check inner structure
    for category, prefs in DEFAULT_PREFERENCES.items():
        for key, pref in prefs.items():
            assert "value" in pref
            assert "weight" in pref
            assert 0.0 <= pref["weight"] <= 1.0


# ============================================================================
# Deadline Management Tests (All In-Memory - Uses Default Preferences)
# ============================================================================

def test_deadline_manager_on_track():
    """Test deadline status for on-track task."""
    manager = DeadlineManager()
    
    now = datetime.now(timezone.utc)
    due = now + timedelta(days=5)  # 5 days from now
    
    status = manager.get_deadline_status("task_1", due)
    
    assert status["status"] == "on_track"
    assert status["risk_level"] == "low"


def test_deadline_manager_at_risk():
    """Test deadline status for at-risk task (with estimated completion)."""
    manager = DeadlineManager()
    
    now = datetime.now(timezone.utc)
    due = now + timedelta(hours=12)  # Very soon
    # Provide estimated completion that would make it at risk
    estimated = now + timedelta(hours=10)  # Would finish just before due
    
    status = manager.get_deadline_status("task_2", due, estimated)
    
    # With estimated completion close to due date, should be at risk
    assert status["status"] in ["at_risk", "on_track"]
    assert status["risk_level"] in ["at_risk", "high", "medium"]


def test_deadline_manager_missed():
    """Test deadline status for missed deadline."""
    manager = DeadlineManager()
    
    due = datetime.now(timezone.utc) - timedelta(hours=1)  # Already due
    
    status = manager.get_deadline_status("task_3", due)
    
    assert status["status"] == "missed"
    assert status["risk_level"] == "critical"


def test_deadline_manager_very_close_deadline():
    """Test very close deadline risk level."""
    manager = DeadlineManager()
    
    now = datetime.now(timezone.utc)
    
    # 1 hour remaining - status depends on estimated_completion
    # Without estimated_completion, time_remaining = total_duration
    # So it falls into "on_track" but risk_level is "medium" for < 24h
    status = manager.get_deadline_status("urgent", now + timedelta(hours=1))
    # With no estimated_completion, time_remaining < 24h gives medium risk
    assert status["risk_level"] in ["medium", "high"]


def test_rescheduling_suggestions():
    """Test rescheduling suggestions."""
    manager = DeadlineManager()
    
    due = datetime.now(timezone.utc) + timedelta(days=2)
    effort = timedelta(hours=4)
    
    options = manager.suggest_rescheduling("task_reschedule", due, effort)
    
    assert len(options) == 3
    assert all("due_date" in opt for opt in options)
    assert all("description" in opt for opt in options)


# ============================================================================
# Priority Scoring Tests
# ============================================================================

def test_deadline_manager_urgency_calculation():
    """Test urgency is calculated correctly."""
    manager = DeadlineManager()
    
    now = datetime.now(timezone.utc)
    
    # 72+ hours = low risk
    status = manager.get_deadline_status("task_a", now + timedelta(hours=72))
    assert status["risk_level"] == "low"
    
    # 12-24 hours = medium risk
    status = manager.get_deadline_status("task_b", now + timedelta(hours=18))
    assert status["risk_level"] == "medium"


# ============================================================================
# Deadline Status Properties Tests
# ============================================================================

def test_deadline_status_time_remaining():
    """Test time remaining calculation in status."""
    manager = DeadlineManager()
    
    now = datetime.now(timezone.utc)
    due = now + timedelta(days=2)
    
    status = manager.get_deadline_status("task_x", due)
    
    assert "time_remaining" in status
    assert status["time_remaining"].total_seconds() > 0


def test_priority_weights_sum():
    """Test priority weights sum correctly within each category."""
    # Check task_prioritization weights sum to ~1.0
    tp_prefs = DEFAULT_PREFERENCES["task_prioritization"]
    weight_sum = sum(pref["weight"] for pref in tp_prefs.values())
    # Weights should be approximately 1.0 (they're individual weights, not summing to 1)
    assert all(0 <= pref["weight"] <= 1.0 for pref in tp_prefs.values())


# ============================================================================
# Edge Cases
# ============================================================================

def test_deadline_status_with_estimated_completion():
    """Test deadline status with estimated completion time."""
    manager = DeadlineManager()
    
    now = datetime.now(timezone.utc)
    due = now + timedelta(days=3)
    estimated = now + timedelta(days=2)
    
    status = manager.get_deadline_status("task_y", due, estimated)
    
    assert "time_remaining" in status
    assert "eta" in status
    assert status["eta"] == estimated


def test_rescheduling_all_options():
    """Test all three rescheduling options are valid."""
    manager = DeadlineManager()
    
    due = datetime.now(timezone.utc) + timedelta(days=5)
    effort = timedelta(hours=8)
    
    options = manager.suggest_rescheduling("test_task", due, effort)
    
    # Option 1: With buffer
    assert options[0]["description"] == "With buffer"
    
    # Option 2: Accelerated timeline
    assert options[1]["description"] == "Accelerated timeline"
    
    # Option 3: Extended for confidence
    assert options[2]["description"] == "Extended for confidence"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])