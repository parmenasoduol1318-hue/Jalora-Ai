"""User preferences and intelligent adaptation engine.

Provides a preference system that learns from user feedback and automatically
tunes behavior based on outcomes.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.preferences import UserPreference, FeedbackRecord, TaskPreference, DEFAULT_PREFERENCES

logger = logging.getLogger("jalora.preferences")


@dataclass
class WeightedValue:
    """A preference value with its weight and confidence."""
    value: Any
    weight: float = 1.0
    confidence: float = 1.0
    source: str = "manual"


@dataclass  
class PreferenceUpdate:
    """Result of a preference update operation."""
    key: str
    old_value: Any
    new_value: Any
    reason: str
    confidence_change: float


class PreferenceEngine:
    """Manages user preferences with learning from feedback."""
    
    def __init__(self):
        self._cache: dict[str, WeightedValue] = {}
    
    def get(self, user_id: str, key: str, context: dict | None = None) -> WeightedValue:
        """Get a preference value with its weight and confidence."""
        cache_key = f"{user_id}:{key}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        db = SessionLocal()
        try:
            pref = db.scalar(
                select(UserPreference).where(
                    UserPreference.user_id == user_id,
                    UserPreference.key == key
                )
            )
            if pref:
                result = WeightedValue(
                    value=pref.value if isinstance(pref.value, dict) else pref.value.get("value", pref.value),
                    weight=pref.weight,
                    confidence=pref.confidence,
                    source=pref.source
                )
            else:
                # Return default preference
                default = self._get_default(key)
                result = WeightedValue(**default) if default else WeightedValue(value=None)
            self._cache[cache_key] = result
            return result
        finally:
            db.close()
    
    def _get_default(self, key: str) -> dict | None:
        """Get default preference value from system defaults."""
        for category, prefs in DEFAULT_PREFERENCES.items():
            if key in prefs:
                return prefs[key]
        return None
    
    def set(self, user_id: str, key: str, value: Any, 
            weight: float = 1.0, category: str = "general",
            source: str = "manual") -> None:
        """Set or update a user preference."""
        db = SessionLocal()
        try:
            pref = db.scalar(
                select(UserPreference).where(
                    UserPreference.user_id == user_id,
                    UserPreference.key == key
                )
            )
            if pref:
                pref.value = {"value": value} if not isinstance(value, dict) else value
                pref.weight = weight
                pref.source = source
                pref.updated_at = datetime.now(timezone.utc)
                pref.confidence = min(1.0, max(0.0, pref.confidence + 0.1))
            else:
                pref = UserPreference(
                    user_id=user_id,
                    key=key,
                    value={"value": value} if not isinstance(value, dict) else value,
                    category=category,
                    weight=weight,
                    source=source,
                    confidence=1.0
                )
                db.add(pref)
            db.commit()
            # Update cache
            self._cache[f"{user_id}:{key}"] = WeightedValue(
                value=value, weight=weight, confidence=1.0, source=source
            )
        finally:
            db.close()
    
    def infer_from_behavior(self, user_id: str, action: str, 
                            outcome: str, context: dict) -> list[PreferenceUpdate]:
        """Infer preferences from user actions and outcomes."""
        updates = []
        
        # Example inference rules
        if action == "task_completed_quickly" and outcome == "success":
            # User prefers quick task completion
            updates.append(PreferenceUpdate(
                key="task_completion_preference",
                old_value=None,
                new_value="efficiency",
                reason="User completed tasks quickly with good outcomes",
                confidence_change=0.2
            ))
        
        if action == "task_missed_deadline":
            # User needs earlier deadlines
            updates.append(PreferenceUpdate(
                key="deadline_buffer_percent",
                old_value=0.1,
                new_value=0.25,
                reason="User missed deadlines, suggesting buffer increase",
                confidence_change=0.15
            ))
        
        if action == "repeated_choice" and context.get("choice_type"):
            # User consistently makes same type of choice
            updates.append(PreferenceUpdate(
                key=f"choice_preference_{context.get('choice_type')}",
                old_value=None,
                new_value=context.get("choice_value"),
                reason=f"Consistent {context.get('choice_type')} choice",
                confidence_change=0.25
            ))
        
        # Apply updates
        for update in updates:
            current = self.get(user_id, update.key)
            self.set(
                user_id=user_id,
                key=update.key,
                value=update.new_value,
                weight=current.weight + 0.1,  # Increase weight with confidence
                source="inferred"
            )
        
        return updates
    
    def update_from_feedback(self, user_id: str, feedback: FeedbackRecord) -> list[PreferenceUpdate]:
        """Update preferences based on explicit user feedback."""
        updates = []
        
        for pref_key in feedback.affected_preferences:
            if not pref_key:
                continue
            
            current = self.get(user_id, pref_key)
            rating = int(feedback.rating) if str(feedback.rating).isdigit() else 0
            
            # Map rating to preference adjustment
            if rating >= 4:  # Positive feedback
                # Increase preference strength
                self.set(
                    user_id=user_id,
                    key=pref_key,
                    value=current.value,
                    weight=min(1.0, current.weight + 0.1),
                    source="feedback"
                )
                updates.append(PreferenceUpdate(
                    key=pref_key,
                    old_value=current.value,
                    new_value=current.value,
                    reason="Positive feedback confirmation",
                    confidence_change=0.1
                ))
            elif rating <= 2:  # Negative feedback
                # Either change or weaken
                self.set(
                    user_id=user_id,
                    key=pref_key,
                    value=current.value,
                    weight=max(0.0, current.weight - 0.2),
                    source="feedback"
                )
                updates.append(PreferenceUpdate(
                    key=pref_key,
                    old_value=current.value,
                    new_value=current.value,
                    reason="Negative feedback - preference weakened",
                    confidence_change=-0.1
                ))
        
        return updates
    
    def clear_cache(self) -> None:
        """Clear the preference cache."""
        self._cache.clear()


# Singleton instance
_preference_engine: PreferenceEngine | None = None

def get_preference_engine() -> PreferenceEngine:
    """Get or create the preference engine singleton."""
    global _preference_engine
    if _preference_engine is None:
        _preference_engine = PreferenceEngine()
    return _preference_engine


# ============================================================================
# Deadline Management
# ============================================================================

class DeadlineManager:
    """Manages task deadlines and alerts."""
    
    def __init__(self, preference_engine: PreferenceEngine | None = None):
        self.engine = preference_engine or get_preference_engine()
    
    def get_deadline_status(self, task_id: str, due_date: datetime, 
                           estimated_completion: datetime | None = None) -> dict:
        """Calculate deadline status and risk level."""
        now = datetime.now(timezone.utc)
        time_remaining = due_date - now
        
        # Get user preferences
        buffer = self.engine.get("default_deadline_buffer", "deadline_management").value or 0.1
        at_risk_threshold = self.engine.get("at_risk_threshold", "deadline_management").value or 0.2
        
        total_duration = (due_date - (estimated_completion or now)) if estimated_completion else time_remaining
        
        if time_remaining <= timedelta(0):
            status = "missed"
        elif time_remaining.total_seconds() < total_duration.total_seconds() * at_risk_threshold:
            status = "at_risk"
        else:
            status = "on_track"
        
        return {
            "status": status,
            "time_remaining": time_remaining,
            "buffer_applied": time_remaining * buffer,
            "eta": estimated_completion,
            "risk_level": self._calculate_risk_level(status, time_remaining),
        }
    
    def _calculate_risk_level(self, status: str, time_remaining: timedelta) -> str:
        """Convert status to risk level."""
        if status == "missed":
            return "critical"
        if status == "at_risk":
            return "high"
        if time_remaining < timedelta(hours=24):
            return "medium"
        return "low"
    
    def suggest_rescheduling(self, task_id: str, current_due: datetime,
                            estimated_effort: timedelta) -> list[dict]:
        """Suggest alternative due dates."""
        options = []
        buffer = self.engine.get("default_deadline_buffer", "deadline_management").value or 0.1
        
        # Option 1: Maintain buffer
        safe_end = current_due - (estimated_effort * buffer)
        options.append({
            "due_date": safe_end,
            "buffer_hours": (current_due - safe_end).total_seconds() / 3600,
            "description": "With buffer"
        })
        
        # Option 2: Earlier by 25%
        earlier = current_due - estimated_effort * 0.25
        options.append({
            "due_date": earlier,
            "buffer_hours": (current_due - earlier).total_seconds() / 3600,
            "description": "Accelerated timeline"
        })
        
        # Option 3: Extended with confidence boost
        extended = current_due + estimated_effort * 0.1
        options.append({
            "due_date": extended,
            "buffer_hours": 0,
            "description": "Extended for confidence"
        })
        
        return options
    
    def schedule_alert(self, task_id: str, user_id: str, 
                       alert_time: datetime, alert_type: str = "deadline") -> None:
        """Schedule a deadline alert."""
        # Store alert in database or send to notification system
        logger.info(f"Scheduled {alert_type} alert for task {task_id} at {alert_time}")


# ============================================================================
# Feature Flags for Gradual Rollout
# ============================================================================

FEATURE_FLAGS = {
    "preferences_enabled": True,
    "deadline_management_enabled": True,
    "feedback_learning_enabled": False,  # Roll out after testing
    "conflict_resolution_v2": False,   # New algorithm
}