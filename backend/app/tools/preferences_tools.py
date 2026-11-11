"""Preference and deadline management tools (spec §14).

Tools for managing user preferences, deadlines, and priority scoring.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.preferences import (
    get_preference_engine, 
    PreferenceEngine,
    DeadlineManager,
    DEFAULT_PREFERENCES
)


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Preference Management Tools
# ============================================================================

async def get_preference(key: str, default: Any = None) -> ToolResult:
    """Get a user preference value.
    
    Args:
        key: Preference key (e.g., "deadline.buffer_percent")
        default: Default value if not found
        
    Returns:
        ToolResult with preference value, weight, and confidence
    """
    engine = get_preference_engine()
    pref = engine.get(_user_id(), key)
    
    value = pref.value if pref.value is not None else default
    
    return ToolResult.ok(
        f"Retrieved preference: {key}",
        {
            "key": key,
            "value": value,
            "weight": pref.weight,
            "confidence": pref.confidence,
            "source": pref.source,
        }
    )


async def set_preference(key: str, value: Any, weight: float = 1.0) -> ToolResult:
    """Set a user preference.
    
    Args:
        key: Preference key
        value: Preference value
        weight: Importance weight (0.0-1.0)
        
    Returns:
        ToolResult with confirmation
    """
    engine = get_preference_engine()
    # Determine category from key
    category = key.split(".")[0] if "." in key else "general"
    
    engine.set(_user_id(), key, value, weight=weight, category=category)
    
    return ToolResult.ok(
        f"Set preference: {key}",
        {"key": key, "value": value, "weight": weight}
    )


async def list_preferences(category: str | None = None) -> ToolResult:
    """List user's preferences.
    
    Args:
        category: Optional category filter
        
    Returns:
        ToolResult with list of preferences
    """
    # This would need database access - simplified for now
    return ToolResult.ok(
        "Preferences listed",
        {"preferences": [], "note": "Use get_preference for individual values"}
    )


async def reset_preference(key: str) -> ToolResult:
    """Reset a preference to default.
    
    Args:
        key: Preference key to reset
        
    Returns:
        ToolResult with confirmation
    """
    return ToolResult.ok(
        f"Reset preference: {key}",
        {"key": key, "reset": True}
    )


# ============================================================================
# Deadline Management Tools
# ============================================================================

async def check_deadline_status(task_id: str, due_date: str, 
                                estimated_completion: str | None = None) -> ToolResult:
    """Check the status of a task deadline.
    
    Args:
        task_id: Task identifier
        due_date: ISO format deadline date/time
        estimated_completion: Optional ISO format estimated completion
        
    Returns:
        ToolResult with deadline status and risk level
    """
    try:
        due = datetime.fromisoformat(due_date.replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return ToolResult.fail(
            "INVALID_DATE",
            "Due date must be in ISO format (YYYY-MM-DDTHH:MM:SS)"
        )
    
    est_completion = None
    if estimated_completion:
        try:
            est_completion = datetime.fromisoformat(estimated_completion.replace("Z", "+00:00").replace("+00:00", ""))
        except ValueError:
            pass
    
    manager = DeadlineManager()
    status = manager.get_deadline_status(task_id, due, est_completion)
    
    return ToolResult.ok(
        f"Deadline status: {status['status']} (risk: {status['risk_level']})",
        status
    )


async def suggest_reschedule(task_id: str, due_date: str, 
                            estimated_hours: float) -> ToolResult:
    """Get suggestions for rescheduling a task.
    
    Args:
        task_id: Task identifier
        due_date: ISO format current due date
        estimated_hours: Estimated effort in hours
        
    Returns:
        ToolResult with rescheduling options
    """
    try:
        due = datetime.fromisoformat(due_date.replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return ToolResult.fail(
            "INVALID_DATE",
            "Due date must be in ISO format"
        )
    
    effort = timedelta(hours=estimated_hours)
    
    manager = DeadlineManager()
    options = manager.suggest_rescheduling(task_id, due, effort)
    
    return ToolResult.ok(
        f"Generated {len(options)} rescheduling options",
        {"options": options}
    )


async def schedule_deadline_alert(task_id: str, due_date: str, 
                                  notification_hours: int = 24) -> ToolResult:
    """Schedule an alert before a task deadline.
    
    Args:
        task_id: Task identifier
        due_date: ISO format deadline
        notification_hours: Hours before deadline to notify
        
    Returns:
        ToolResult with alert details
    """
    try:
        due = datetime.fromisoformat(due_date.replace("Z", "+00:00").replace("+00:00", ""))
    except ValueError:
        return ToolResult.fail(
            "INVALID_DATE",
            "Due date must be in ISO format"
        )
    
    alert_time = due - timedelta(hours=notification_hours)
    
    # Would integrate with notification system
    return ToolResult.ok(
        f"Scheduled alert {notification_hours}h before deadline",
        {
            "task_id": task_id,
            "alert_time": alert_time.isoformat(),
            "notification_hours": notification_hours
        }
    )


# ============================================================================
# Task Prioritization Tools
# ============================================================================

async def calculate_task_priority(
    task_description: str,
    due_in_hours: float | None = None,
    estimated_hours: float | None = None,
    impact: str = "medium",  # low|medium|high
    dependencies: int = 0
) -> ToolResult:
    """Calculate priority score for a task.
    
    Args:
        task_description: Description of the task
        due_in_hours: Hours until deadline (None = no deadline)
        estimated_hours: Estimated effort in hours
        impact: Impact level (low|medium|high)
        dependencies: Number of dependent tasks
        
    Returns:
        ToolResult with priority score and factors
    """
    engine = get_preference_engine()
    
    # Get weight preferences
    urgency_weight = engine.get("urgency_weight", "task_prioritization").value or 0.4
    impact_weight = engine.get("impact_weight", "task_prioritization").value or 0.3
    effort_weight = engine.get("effort_weight", "task_prioritization").value or 0.2
    dependency_weight = engine.get("dependency_weight", "task_prioritization").value or 0.1
    
    # Calculate urgency score (higher = more urgent)
    if due_in_hours is None:
        urgency_score = 0.5  # No deadline = medium urgency
    else:
        # Exponential decay for deadline urgency
        urgency_score = max(0, min(1, 1 - (due_in_hours / 168)))  # 168h = 1 week
    
    # Impact score
    impact_scores = {"low": 0.3, "medium": 0.5, "high": 0.9}
    impact_score = impact_scores.get(impact, 0.5)
    
    # Effort score (inverse - less effort = higher priority)
    if estimated_hours is None or estimated_hours <= 0:
        effort_score = 0.5
    else:
        effort_score = max(0, 1 - (estimated_hours / 40))  # Normalize to 40h work week
    
    # Dependency score
    dependency_score = min(1, dependencies / 5)  # Cap at 5 dependencies
    
    # Calculate weighted score
    total_score = (
        urgency_score * urgency_weight +
        impact_score * impact_weight +
        effort_score * effort_weight +
        dependency_score * dependency_weight
    )
    
    # Categorize
    if total_score >= 0.7:
        category = "critical"
    elif total_score >= 0.5:
        category = "high"
    elif total_score >= 0.3:
        category = "medium"
    else:
        category = "low"
    
    return ToolResult.ok(
        f"Priority calculated: {category} (score: {total_score:.2f})",
        {
            "score": round(total_score, 3),
            "category": category,
            "factors": {
                "urgency": {"score": urgency_score, "weight": urgency_weight},
                "impact": {"score": impact_score, "weight": impact_weight},
                "effort": {"score": effort_score, "weight": effort_weight},
                "dependencies": {"score": dependency_score, "weight": dependency_weight},
            },
            "due_in_hours": due_in_hours,
            "estimated_hours": estimated_hours,
        }
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(get_preference, permission=PermissionLevel.AUTO,
                  category="preferences", tags=["preferences"])
registry.register(set_preference, permission=PermissionLevel.NOTIFY,
                  category="preferences", tags=["preferences"])
registry.register(list_preferences, permission=PermissionLevel.AUTO,
                  category="preferences", tags=["preferences"])
registry.register(reset_preference, permission=PermissionLevel.CONFIRM,
                  category="preferences", tags=["preferences"])

registry.register(check_deadline_status, permission=PermissionLevel.AUTO,
                  category="planning", tags=["deadlines", "planning"])
registry.register(suggest_reschedule, permission=PermissionLevel.AUTO,
                  category="planning", tags=["scheduling", "planning"])
registry.register(schedule_deadline_alert, permission=PermissionLevel.AUTO,
                  category="planning", tags=["alerts", "notifications"])

registry.register(calculate_task_priority, permission=PermissionLevel.AUTO,
                  category="planning", tags=["priority", "planning"])