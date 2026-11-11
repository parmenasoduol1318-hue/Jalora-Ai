"""Tests for Outcome model serialization and validation.

Tests cover:
- Serialization with/without sensitive data
- Privacy controls (user_id redaction)
- Timestamp handling
- Float precision
- Success/failure distinction
- UserAction serialization
- TaskOutcome dataclass conversion
"""
from datetime import datetime, timezone

import pytest

from app.models.outcome import Outcome, TaskOutcome


class TestOutcomeSerialization:
    """Test outcome serialization methods."""

    def test_to_dict_excludes_sensitive_by_default(self):
        """Ensure sensitive fields are excluded for public responses."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            error_message="Should be hidden",
            preference_updates=[{"key": "value"}],
        )
        data = outcome.to_dict()

        assert "error_message" not in data
        assert "preference_updates" not in data
        assert data["success"] is True

    def test_to_dict_includes_sensitive_when_requested(self):
        """Ensure sensitive fields are included when requested."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            error_message="Visible when requested",
        )
        data = outcome.to_dict(include_sensitive=True)

        assert data["error_message"] == "Visible when requested"

    def test_to_dict_redacts_user_id_when_requested(self):
        """Ensure user_id is redacted for privacy in logs."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="secret_user_12345",
            tool_name="test_tool",
            success=True,
        )
        data = outcome.to_dict(redact_user_id=True)

        assert data["user_id"] == "***"
        assert data["id"] == "test123"

    def test_to_dict_handles_null_timestamps(self):
        """Ensure timestamps are handled safely."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            started_at=None,
            completed_at=None,
        )
        data = outcome.to_dict()

        assert data["started_at"] is None
        assert data["completed_at"] is None

    def test_to_dict_rounds_floats(self):
        """Ensure float values are rounded for consistency."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            quality_score=0.123456789,
            result_confidence=0.987654321,
        )
        data = outcome.to_dict()

        # Python uses banker's rounding (round half to even)
        assert data["quality_score"] == 0.1235
        assert data["result_confidence"] == 0.9877

    def test_success_outcome_has_correct_status(self):
        """Verify that successful outcomes are correctly marked."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            quality_score=0.9,
        )
        assert outcome.success is True
        data = outcome.to_dict()
        assert data["success"] is True

    def test_failed_outcome_includes_error(self):
        """Verify that failed outcomes properly record errors."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=False,
            error_message="Tool execution failed",
            error_type="execution_error",
        )
        assert outcome.success is False
        data = outcome.to_dict(include_sensitive=True)
        assert data["error_message"] == "Tool execution failed"

    def test_to_dict_returns_iso_format_timestamps(self):
        """Verify timestamps are properly converted to ISO format."""
        now = datetime(2026, 10, 22, 10, 30, 0, tzinfo=timezone.utc)
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            started_at=now,
            completed_at=now,
        )
        data = outcome.to_dict()

        assert data["started_at"] == "2026-10-22T10:30:00+00:00"
        assert data["completed_at"] == "2026-10-22T10:30:00+00:00"


class TestUserActionSerialization:
    """Test UserAction serialization methods."""

    def test_to_summary_excludes_sensitive_data(self):
        """Ensure to_summary doesn't expose sensitive args."""
        from app.models.actions import UserAction

        action = UserAction(
            id="test123",
            user_id="user123",
            tool_name="api_call",
            args={"api_key": "secret123", "endpoint": "/public"},
            status="completed",
        )
        summary = action.to_summary()

        # Summary should not include args
        assert "args" not in summary
        assert summary["id"] == "test123"
        assert summary["tool"] == "api_call"


class TestTaskOutcomeDataclass:
    """Test TaskOutcome dataclass conversion."""

    def test_outcome_to_dataclass_conversion(self):
        """Verify Outcome can be converted to TaskOutcome dataclass."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            quality_score=0.8,
        )
        task_outcome = outcome.to_dataclass()

        assert isinstance(task_outcome, TaskOutcome)
        assert task_outcome.task_id == "task123"
        assert task_outcome.success is True

    def test_from_dataclass_creates_valid_outcome(self):
        """Verify TaskOutcome can be converted back to Outcome."""
        dataclass = TaskOutcome(
            task_id="task456",
            user_id="user456",
            tool_name="another_tool",
            success=False,
            quality_score=0.3,
        )

        outcome = Outcome.from_dataclass(dataclass)

        assert isinstance(outcome, Outcome)
        assert outcome.task_id == "task456"
        assert outcome.user_id == "user456"
        assert outcome.tool_name == "another_tool"
        assert outcome.success is False
        assert outcome.quality_score == 0.3


class TestOutcomeEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_output_summary(self):
        """Verify empty output summary is handled correctly."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            output_summary="",
        )
        data = outcome.to_dict()

        assert data["output_summary"] == ""

    def test_long_output_summary_truncated(self):
        """Verify long output summary is truncated for safety."""
        long_text = "A" * 2000
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            output_summary=long_text,
        )
        data = outcome.to_dict()

        assert len(data["output_summary"]) == 1000
        # Output is truncated, not suffixed with ellipsis

    def test_null_user_rating(self):
        """Verify null user_rating is handled correctly."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            user_rating=None,
        )
        data = outcome.to_dict()

        assert data["user_rating"] is None

    def test_zero_durations(self):
        """Verify zero durations are handled correctly."""
        outcome = Outcome(
            id="test123",
            task_id="task123",
            user_id="user123",
            tool_name="test_tool",
            success=True,
            duration_seconds=0.0,
        )
        data = outcome.to_dict()

        assert data["duration_seconds"] == 0.0