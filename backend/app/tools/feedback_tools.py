"""Feedback tools (Phase 8).

Tools for collecting, processing, and acting on user feedback.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services.feedback_learning import (
    get_feedback_learner,
    UserFeedback,
    LearningUpdate,
)
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Feedback Tools
# ============================================================================

async def submit_feedback(
    action_type: str,
    outcome: str,
    rating: float = 0.0,
    comment: str = "",
    action_id: str | None = None,
    context: dict | None = None
) -> ToolResult:
    """Submit user feedback on an action or decision.
    
    Args:
        action_type: Type of action being feedbacked on
        outcome: Outcome rating (positive, negative, neutral)
        rating: Numerical rating (0.0-1.0)
        comment: Optional comment
        action_id: Optional ID of the specific action
        context: Optional context dictionary
        
    Returns:
        ToolResult with processing result and insights
    """
    learner = get_feedback_learner()
    
    feedback = UserFeedback(
        user_id=_user_id(),
        action_type=action_type,
        outcome=outcome,
        rating=rating,
        comment=comment,
        action_id=action_id,
        context=context or {},
    )
    
    result = learner.process_feedback(feedback)
    
    return ToolResult.ok(
        f"Feedback processed for {action_type}",
        {
            "insights": result.insights,
            "preference_changes": [
                {
                    "key": c.preference_key,
                    "new_value": c.new_value,
                    "confidence": c.confidence,
                }
                for c in result.preference_changes
            ],
        }
    )


async def get_feedback_summary(
    action_type: str | None = None,
    limit: int = 10
) -> ToolResult:
    """Get summary of feedback for an action type or user.
    
    Args:
        action_type: Optional action type filter
        limit: Maximum feedback items to include
        
    Returns:
        ToolResult with feedback summary
    """
    learner = get_feedback_learner()
    summary = learner.summarize_feedback(_user_id(), action_type)
    
    return ToolResult.ok(
        f"Feedback summary for {summary.action_type}",
        {
            "action_type": summary.action_type,
            "positive_count": summary.positive_count,
            "negative_count": summary.negative_count,
            "neutral_count": summary.neutral_count,
            "avg_rating": summary.avg_rating,
            "trend": summary.trend,
        }
    )


async def detect_preference_drift(threshold: float = 0.1) -> ToolResult:
    """Detect preference drift from user feedback patterns.
    
    Args:
        threshold: Drift detection threshold (0.0-1.0)
        
    Returns:
        ToolResult with potential drifts
    """
    learner = get_feedback_learner()
    drifts = learner.detect_preference_drift(_user_id(), threshold)
    
    return ToolResult.ok(
        f"Detected {len(drifts)} potential preference drifts",
        {
            "drifts": [
                {
                    "preference_key": d.preference_key,
                    "new_value": d.new_value,
                    "drift_magnitude": d.drift_magnitude,
                    "detected_at": d.detected_at.isoformat(),
                }
                for d in drifts
            ],
        }
    )


async def get_recommendations_from_feedback() -> ToolResult:
    """Get action recommendations derived from feedback.
    
    Returns:
        ToolResult with recommendations
    """
    learner = get_feedback_learner()
    summary = learner.summarize_feedback(_user_id())
    
    recommendations = []
    
    if summary.trend == "declining":
        recommendations.append("Review recent approaches - feedback indicates declining satisfaction")
    
    if summary.avg_rating < 0.5:
        recommendations.append("Low satisfaction rating - consider major adjustments")
    elif summary.avg_rating > 0.8:
        recommendations.append("High satisfaction - continue current approach")
    
    # Add recommendations from recent feedback processing
    recent_feedback = learner.feedback_history[-5:]
    for fb in recent_feedback:
        if fb.outcome == "negative":
            recommendations.append(f"Investigate issues with {fb.action_type}")
    
    return ToolResult.ok(
        f"Generated {len(recommendations)} recommendations from feedback analysis",
        {"recommendations": recommendations}
    )


async def log_feedback_event(
    event_type: str,
    description: str,
    severity: str = "info"
) -> ToolResult:
    """Log a feedback-related event for analysis.
    
    Args:
        event_type: Type of feedback event
        description: Event description
        severity: Event severity (info, warning, error)
        
    Returns:
        ToolResult with confirmation
    """
    # In a real implementation, this would log to a database
    events = getattr(get_feedback_learner(), "_events", [])
    events.append({
        "event_type": event_type,
        "description": description,
        "severity": severity,
        "timestamp": __import__("datetime").datetime.now(timezone.utc).isoformat(),
    })
    setattr(get_feedback_learner(), "_events", events)
    
    return ToolResult.ok(
        f"Event logged: {event_type}",
        {"event_type": event_type, "severity": severity}
    )


# ============================================================================
# Tool Registration
# ============================================================================

import datetime
from datetime import timezone

registry.register(
    submit_feedback,
    permission=PermissionLevel.NOTIFY,  # Feedback collection, non-autonomous
    category="feedback",
    tags=["feedback", "submit", "rating"]
)

registry.register(
    get_feedback_summary,
    permission=PermissionLevel.AUTO,
    category="feedback",
    tags=["feedback", "summary", "analysis"]
)

registry.register(
    detect_preference_drift,
    permission=PermissionLevel.AUTO,
    category="feedback",
    tags=["drift", "preferences", "learning"]
)

registry.register(
    get_recommendations_from_feedback,
    permission=PermissionLevel.AUTO,
    category="feedback",
    tags=["recommendations", "feedback", "insights"]
)

registry.register(
    log_feedback_event,
    permission=PermissionLevel.NOTIFY,
    category="feedback",
    tags=["event", "log", "feedback"]
)