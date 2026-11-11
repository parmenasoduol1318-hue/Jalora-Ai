"""User preferences and intelligent adaptation (spec §7, §14).

Provides a preference system that learns from user feedback and automatically
tunes behavior based on outcomes.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.user import new_id, utcnow


class UserPreference(Base):
    """A stored user preference with optional weighting."""

    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    # Preference key (namespaced)
    key: Mapped[str] = mapped_column(String(128), index=True)
    
    # Preference value (JSON for flexibility)
    value: Mapped[dict] = mapped_column(JSON, server_default="{}")
    
    # Type of preference (for validation/organization)
    category: Mapped[str] = mapped_column(String(64), default="general")
    
    # Weight/importance (0.0-1.0), affects scoring
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    
    # Source: how was this preference set?
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual|inferred|derived|feedback
    
    # Confidence in the preference (0.0-1.0)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    
    # When this preference was last updated
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    
    # Related preferences (for derived preferences)
    derived_from: Mapped[str] = mapped_column(String(32), nullable=True)
    
    __table_args__ = (
        # Unique per user+key
        {"UniqueConstraint": ("user_id", "key", "id")},
    )


class TaskPreference(Base):
    """Preference values that apply to task execution."""
    
    __tablename__ = "task_preferences"
    
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    preference_key: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict] = mapped_column(JSON, server_default="{}")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeedbackRecord(Base):
    """Record of user feedback for preference learning."""
    
    __tablename__ = "feedback_records"
    
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    
    # What was the feedback about?
    context_type: Mapped[str] = mapped_column(String(64))  # task|tool|workflow|decision
    context_id: Mapped[str] = mapped_column(String(128))
    
    # Feedback data
    rating: Mapped[int] = mapped_column("rating", String(10), default="0")  # -1|0|1 | 1-5 scale
    comment: Mapped[str] = mapped_column(Text, default="")
    
    # What preference(s) does this affect?
    affected_preferences: Mapped[list] = mapped_column(JSON, server_default="[]")
    
    # Metadata
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[str] = mapped_column(String(32), default="explicit")  # explicit|implicit|audit


class TaskDeadline(Base):
    """Deadline tracking for tasks."""
    
    __tablename__ = "task_deadlines"
    
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    
    # Deadline configuration
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    buffer_percent: Mapped[float] = mapped_column(Float, default=0.1)  # 10% buffer
    advance_notice_hours: Mapped[int] = mapped_column(String(10), default=24)
    
    # Status tracking
    status: Mapped[str] = mapped_column(String(32), default="scheduled")  # scheduled|at_risk|met|missed
    earliest_completion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_completion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Alert history
    alerts_sent: Mapped[list] = mapped_column(JSON, server_default="[]")  # [{timestamp, type}]
    
    # Audit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ============================================================================
# Preference Defaults - System-wide defaults that can be overridden
# ============================================================================

DEFAULT_PREFERENCES = {
    "task_prioritization": {
        "urgency_weight": {"value": 0.4, "weight": 1.0},
        "impact_weight": {"value": 0.3, "weight": 1.0},
        "effort_weight": {"value": 0.2, "weight": 0.8},
        "dependency_weight": {"value": 0.1, "weight": 1.0},
    },
    "deadline_management": {
        "default_buffer_percent": {"value": 0.1, "weight": 1.0},
        "advance_notice_hours": {"value": 24, "weight": 0.8},
        "at_risk_threshold": {"value": 0.2, "weight": 1.0},  # 20% of time remaining
    },
    "verification": {
        "min_sources": {"value": 3, "weight": 1.0},
        "min_confidence": {"value": 0.7, "weight": 0.9},
        "credibility_threshold": {"value": 0.6, "weight": 0.8},
    },
    "meeting_prep": {
        "document_lookback_days": {"value": 30, "weight": 1.0},
        "agenda_template": {"value": "standard", "weight": 0.7},
        "summary_format": {"value": "executive", "weight": 0.8},
    },
    "form_completion": {
        "compliance_check": {"value": True, "weight": 1.0},
        "auto_fill_confidence_threshold": {"value": 0.8, "weight": 0.9},
        "manual_review_required": {"value": False, "weight": 1.0},
    },
    "code_review": {
        "security_check_enabled": {"value": True, "weight": 1.0},
        "performance_check_enabled": {"value": True, "weight": 0.8},
        "complexity_threshold": {"value": 10, "weight": 0.7},
    },
}