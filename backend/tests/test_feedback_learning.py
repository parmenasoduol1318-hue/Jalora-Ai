"""Tests for Feedback Incorporation (Phase 8).

Run with: python -m pytest backend/tests/test_feedback_learning.py -v
"""
import pytest
from datetime import datetime, timezone

from app.services.feedback_learning import (
    FeedbackLearner,
    UserFeedback,
    LearningUpdate,
    PreferenceChange,
    FeedbackSummary,
    PreferenceDrift,
    get_feedback_learner,
    process_user_feedback,
)


class TestFeedbackLearner:
    """Test the FeedbackLearner service."""
    
    def test_learner_singleton(self):
        """Test that get_feedback_learner returns singleton."""
        l1 = get_feedback_learner()
        l2 = get_feedback_learner()
        assert l1 is l2
    
    def test_learner_creation(self):
        """Test FeedbackLearner can be created."""
        learner = FeedbackLearner()
        assert learner is not None


class TestUserFeedback:
    """Test UserFeedback dataclass."""
    
    def test_feedback_creation(self):
        """Test creating user feedback."""
        feedback = UserFeedback(
            user_id="user-123",
            action_type="form_completion",
            action_id="form-456",
            outcome="positive",
            rating=0.9,
            comment="Great help!"
        )
        
        assert feedback.user_id == "user-123"
        assert feedback.action_type == "form_completion"
        assert feedback.outcome == "positive"
        assert feedback.rating == 0.9


class TestLearningUpdate:
    """Test LearningUpdate dataclass."""
    
    def test_update_creation(self):
        """Test creating a learning update."""
        update = LearningUpdate(
            preference_changes=[
                PreferenceChange(
                    preference_key="test",
                    old_value=0.5,
                    new_value=0.7,
                    reason="Test change"
                )
            ],
            action_recommendations=["Do this", "Do that"],
            insights=["Test insight"]
        )
        
        assert len(update.preference_changes) == 1
        assert len(update.action_recommendations) == 2
        assert len(update.insights) == 1


class TestProcessFeedback:
    """Test feedback processing."""
    
    def test_process_positive_feedback(self):
        """Test processing positive feedback."""
        learner = get_feedback_learner()
        
        feedback = UserFeedback(
            user_id="user-123",
            action_type="content_creation",
            outcome="positive",
            rating=0.9
        )
        
        result = learner.process_feedback(feedback)
        
        assert isinstance(result, LearningUpdate)
        assert len(result.insights) > 0
        assert any("satisfied" in i.lower() or "positive" in i.lower() for i in result.insights)
    
    def test_process_negative_feedback(self):
        """Test processing negative feedback."""
        learner = get_feedback_learner()
        
        feedback = UserFeedback(
            user_id="user-456",
            action_type="form_completion",
            outcome="negative",
            rating=0.2,
            comment="Too slow"
        )
        
        result = learner.process_feedback(feedback)
        
        assert any("dissatisfaction" in i.lower() for i in result.insights)
    
    def test_preference_changes_calculated(self):
        """Test that preference changes are calculated from feedback."""
        learner = get_feedback_learner()
        
        feedback = UserFeedback(
            user_id="user-789",
            action_type="form_completion",
            outcome="negative",
            rating=0.2
        )
        
        result = learner.process_feedback(feedback)
        
        assert len(result.preference_changes) > 0
        # Low rating should decrease preference value
        change = result.preference_changes[0]
        assert change.old_value != change.new_value
    
    def test_recommendations_generated(self):
        """Test that recommendations are generated."""
        learner = get_feedback_learner()
        
        feedback = UserFeedback(
            user_id="user-123",
            action_type="content_creation",
            outcome="negative",
            rating=0.2
        )
        
        result = learner.process_feedback(feedback)
        
        assert len(result.action_recommendations) > 0


class TestUpdatePreferences:
    """Test update_preferences method."""
    
    def test_update_preferences_returns_changes(self):
        """Test that update_preferences returns preference changes."""
        learner = get_feedback_learner()
        
        feedback = UserFeedback(
            user_id="user-123",
            action_type="code_review",
            outcome="positive",
            rating=0.8
        )
        
        changes = learner.update_preferences("user-123", feedback)
        
        assert len(changes) > 0
        assert changes[0].preference_key == "code_quality_threshold"


class TestDetectDrift:
    """Test detect_preference_drift method."""
    
    def test_detect_no_drift_initially(self):
        """Test no drift with insufficient feedback."""
        learner = get_feedback_learner()
        
        drift = learner.detect_preference_drift("user-123")
        
        assert drift == []
    
    def test_detect_drift_after_feedback(self):
        """Test drift detection after multiple feedbacks."""
        learner = get_feedback_learner()
        
        # Add multiple feedbacks
        for i in range(5):
            feedback = UserFeedback(
                user_id="user-drift",
                action_type="test_action",
                outcome="positive",
                rating=0.9 - (i * 0.1)  # Decreasing ratings
            )
            learner.process_feedback(feedback)
        
        drift = learner.detect_preference_drift("user-drift")
        
        # Should detect some drift
        assert isinstance(drift, list)


class TestSummarizeFeedback:
    """Test summarize_feedback method."""
    
    def test_summarize_empty(self):
        """Test summarization with no feedback."""
        learner = get_feedback_learner()
        
        summary = learner.summarize_feedback("nonexistent-user")
        
        assert summary.positive_count == 0
        assert summary.negative_count == 0
        assert summary.avg_rating == 0.0
    
    def test_summarize_with_feedback(self):
        """Test summarization with feedback."""
        learner = get_feedback_learner()
        
        # Add feedback
        learner.process_feedback(UserFeedback(
            user_id="user-summary",
            action_type="form_completion",
            outcome="positive",
            rating=0.9
        ))
        learner.process_feedback(UserFeedback(
            user_id="user-summary",
            action_type="form_completion",
            outcome="negative",
            rating=0.3
        ))
        
        summary = learner.summarize_feedback("user-summary")
        
        assert summary.positive_count == 1
        assert summary.negative_count == 1
        assert 0.0 <= summary.avg_rating <= 1.0
    
    def test_summarize_by_action_type(self):
        """Test summarization by action type."""
        learner = get_feedback_learner()
        
        # Add feedback for different actions
        learner.process_feedback(UserFeedback(
            user_id="user-type",
            action_type="content_creation",
            outcome="positive",
            rating=0.8
        ))
        
        summary = learner.summarize_feedback("user-type", "content_creation")
        
        assert summary.action_type == "content_creation"
        assert summary.positive_count == 1


class TestConvenienceFunction:
    """Test the process_user_feedback convenience function."""
    
    def test_process_user_feedback(self):
        """Test convenience function for processing feedback."""
        result = process_user_feedback(
            user_id="user-999",
            action_type="workflow_execution",
            outcome="positive",
            rating=0.7,
            comment="Helpful!"
        )
        
        assert isinstance(result, LearningUpdate)
        assert len(result.insights) > 0


class TestFeedbackSummary:
    """Test FeedbackSummary dataclass."""
    
    def test_summary_creation(self):
        """Test creating a feedback summary."""
        summary = FeedbackSummary(
            action_type="blog_post",
            positive_count=10,
            negative_count=2,
            neutral_count=3,
            avg_rating=0.75,
            trend="improving"
        )
        
        assert summary.action_type == "blog_post"
        assert summary.positive_count == 10
        assert summary.trend == "improving"


class TestPreferenceChange:
    """Test PreferenceChange dataclass."""
    
    def test_change_creation(self):
        """Test creating a preference change."""
        change = PreferenceChange(
            preference_key="form_compliance",
            old_value=0.5,
            new_value=0.7,
            reason="Positive feedback",
            confidence=0.9
        )
        
        assert change.preference_key == "form_compliance"
        assert change.old_value == 0.5
        assert change.new_value == 0.7
        assert change.confidence == 0.9


class TestPreferenceDrift:
    """Test PreferenceDrift dataclass."""
    
    def test_drift_creation(self):
        """Test creating a preference drift."""
        drift = PreferenceDrift(
            preference_key="content_tone",
            old_value=0.5,
            new_value=0.8,
            drift_magnitude=0.3
        )
        
        assert drift.preference_key == "content_tone"
        assert drift.drift_magnitude == 0.3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])