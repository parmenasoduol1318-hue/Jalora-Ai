"""Feedback incorporation services (Phase 8).

Provides automatic preference tuning and learning from user feedback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ============================================================================
# Feedback Models
# ============================================================================

@dataclass
class UserFeedback:
    """User feedback on an action or decision."""
    user_id: str
    action_type: str
    outcome: str  # positive, negative, neutral
    action_id: str | None = None
    rating: float = 0.0  # 0.0-1.0 scale
    comment: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedbackSummary:
    """Summary of feedback for a user or action type."""
    action_type: str
    positive_count: int
    negative_count: int
    neutral_count: int
    avg_rating: float
    trend: str = "stable"  # improving, declining, stable


@dataclass
class PreferenceChange:
    """Proposed change to user preferences."""
    preference_key: str
    old_value: Any
    new_value: Any
    reason: str
    confidence: float = 0.0  # 0.0-1.0


@dataclass
class LearningUpdate:
    """Update resulting from feedback processing."""
    preference_changes: list[PreferenceChange]
    action_recommendations: list[str]
    insights: list[str]


@dataclass
class PreferenceDrift:
    """Detected drift in user preferences."""
    preference_key: str
    old_value: Any
    new_value: Any
    drift_magnitude: float
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# Feedback Learning Service
# ============================================================================

class FeedbackLearner:
    """Service for processing feedback and updating preferences."""
    
    def __init__(self):
        self.feedback_history: list[UserFeedback] = []
        self.drift_threshold = 0.1
    
    def process_feedback(self, feedback: UserFeedback) -> LearningUpdate:
        """Process user feedback and generate learning updates."""
        # Store feedback
        self.feedback_history.append(feedback)
        
        # Extract insights from feedback
        insights = self._extract_insights(feedback)
        
        # Determine preference changes
        preference_changes = self._calculate_preference_changes(feedback)
        
        # Generate action recommendations
        recommendations = self._generate_recommendations(feedback)
        
        return LearningUpdate(
            preference_changes=preference_changes,
            action_recommendations=recommendations,
            insights=insights
        )
    
    def _extract_insights(self, feedback: UserFeedback) -> list[str]:
        """Extract insights from feedback."""
        insights = []
        
        if feedback.outcome == "negative":
            insights.append(f"User expressed dissatisfaction with {feedback.action_type}")
        elif feedback.outcome == "positive":
            insights.append(f"User satisfied with {feedback.action_type}")
        
        if feedback.rating < 0.5:
            insights.append("Low rating indicates need for improvement")
        elif feedback.rating > 0.8:
            insights.append("High rating confirms effective approach")
        
        return insights
    
    def _calculate_preference_changes(self, feedback: UserFeedback) -> list[PreferenceChange]:
        """Calculate preference changes based on feedback."""
        changes = []
        
        # Map action types to preference keys
        action_to_preference = {
            "form_completion": "form_compliance",
            "content_creation": "creative_tone",
            "workflow_execution": "automation_aggressiveness",
            "code_review": "code_quality_threshold"
        }
        
        pref_key = action_to_preference.get(feedback.action_type, "general_preference")
        
        # Calculate new value based on feedback
        old_value = 0.5  # Default neutral
        if feedback.rating < 0.3:
            new_value = max(0.0, old_value - 0.2)
            reason = f"Negative feedback: {feedback.comment or 'No comment'}"
        elif feedback.rating > 0.7:
            new_value = min(1.0, old_value + 0.1)
            reason = f"Positive feedback: {feedback.comment or 'No comment'}"
        else:
            new_value = old_value
            reason = "Neutral feedback - no change"
        
        changes.append(PreferenceChange(
            preference_key=pref_key,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            confidence=feedback.rating
        ))
        
        return changes
    
    def _generate_recommendations(self, feedback: UserFeedback) -> list[str]:
        """Generate action recommendations based on feedback."""
        recommendations = []
        
        if feedback.outcome == "negative" and feedback.rating < 0.3:
            recommendations.append(f"Review {feedback.action_type} approach")
            recommendations.append("Consider alternative strategy")
        elif feedback.outcome == "positive" and feedback.rating > 0.7:
            recommendations.append(f"Apply similar approach to similar tasks")
        
        return recommendations
    
    def update_preferences(self, user_id: str, feedback: UserFeedback) -> list[PreferenceChange]:
        """Update user preferences based on feedback."""
        learning = self.process_feedback(feedback)
        return learning.preference_changes
    
    def detect_preference_drift(self, user_id: str, threshold: float = 0.1) -> list[PreferenceDrift]:
        """Detect if user preferences have drifted significantly."""
        drifts = []
        
        # Calculate average rating for each action type
        action_ratings: dict[str, list[float]] = {}
        for fb in self.feedback_history:
            if fb.user_id == user_id:
                if fb.action_type not in action_ratings:
                    action_ratings[fb.action_type] = []
                action_ratings[fb.action_type].append(fb.rating)
        
        # Check for drift from historical average
        for action_type, ratings in action_ratings.items():
            if len(ratings) > 3:
                avg = sum(ratings) / len(ratings)
                if abs(avg - 0.5) > threshold:
                    drifts.append(PreferenceDrift(
                        preference_key=action_type,
                        old_value=0.5,
                        new_value=avg,
                        drift_magnitude=abs(avg - 0.5)
                    ))
        
        return drifts
    
    def summarize_feedback(self, user_id: str, action_type: str | None = None) -> FeedbackSummary:
        """Summarize feedback for a user or action type."""
        relevant_feedback = [
            fb for fb in self.feedback_history
            if fb.user_id == user_id and (action_type is None or fb.action_type == action_type)
        ]
        
        if not relevant_feedback:
            return FeedbackSummary(
                action_type=action_type or "all",
                positive_count=0,
                negative_count=0,
                neutral_count=0,
                avg_rating=0.0
            )
        
        positive = sum(1 for fb in relevant_feedback if fb.outcome == "positive")
        negative = sum(1 for fb in relevant_feedback if fb.outcome == "negative")
        neutral = sum(1 for fb in relevant_feedback if fb.outcome == "neutral")
        
        ratings = [fb.rating for fb in relevant_feedback if fb.rating > 0]
        avg_rating = sum(ratings) / len(ratings) if ratings else 0.0
        
        # Determine trend
        if len(relevant_feedback) >= 3:
            recent = relevant_feedback[-3:]
            recent_avg = sum(fb.rating for fb in recent) / len(recent)
            if recent_avg > avg_rating + 0.1:
                trend = "improving"
            elif recent_avg < avg_rating - 0.1:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "stable"
        
        return FeedbackSummary(
            action_type=action_type or "all",
            positive_count=positive,
            negative_count=negative,
            neutral_count=neutral,
            avg_rating=round(avg_rating, 3),
            trend=trend
        )


# Singleton instance
_learner: FeedbackLearner | None = None

def get_feedback_learner() -> FeedbackLearner:
    """Get or create the feedback learner singleton."""
    global _learner
    if _learner is None:
        _learner = FeedbackLearner()
    return _learner


def process_user_feedback(
    user_id: str,
    action_type: str,
    outcome: str,
    rating: float = 0.0,
    comment: str = ""
) -> LearningUpdate:
    """Convenience function to process feedback."""
    learner = get_feedback_learner()
    feedback = UserFeedback(
        user_id=user_id,
        action_type=action_type,
        outcome=outcome,
        rating=rating,
        comment=comment
    )
    return learner.process_feedback(feedback)