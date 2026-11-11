"""Outcome tracking models for learning and improvement (spec §1, §14).

Provides models for tracking task outcomes to enable learning from
user feedback and automated preference tuning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Outcome(Base):
    """Record of a task/tool outcome for learning and improvement."""
    
    __tablename__ = "outcomes"
    
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    
    # Result indicators
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    result_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    
    # Timing
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    
    # Input context
    input_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_args_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    
    # Output
    output_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output_tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    
    # Quality metrics
    quality_metric: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    
    # User feedback
    user_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_feedback_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    
    # System metrics
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    
    # Learning signals
    preference_updates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    
    def __repr__(self) -> str:
        return f"<Outcome {self.id[:8]}... success={self.success} quality={self.quality_score}>"

    def to_dict(self, include_sensitive: bool = False, redact_user_id: bool = False) -> dict:
        """Serialize to dictionary for API responses.

        Args:
            include_sensitive: If False (default), excludes error_message, 
                preference_updates, and other sensitive/internal fields.
            redact_user_id: If True, masks user_id for privacy in logs/debugging.

        Returns:
            dict with ISO-formatted timestamps suitable for JSON serialization.
            Never includes raw passwords, API keys, or internal tokens.
        """
        # Helper to round floats safely (handle None values from tests)
        def safe_round(val, decimals):
            if val is None:
                return 0.0
            return round(float(val), decimals)

        # Base fields always included (public response data)
        result = {
            "id": self.id,
            "task_id": self.task_id,
            "user_id": "***" if redact_user_id else self.user_id,
            "tool_name": self.tool_name,
            "success": bool(self.success) if self.success is not None else False,
            "quality_score": safe_round(self.quality_score, 4),
            "result_confidence": safe_round(self.result_confidence, 4),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": safe_round(self.duration_seconds, 3),
            "output_summary": (self.output_summary[:1000] if self.output_summary else "")[:1000],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "user_rating": self.user_rating,  # User-provided rating - can be public
        }

        if include_sensitive:
            # Include sensitive fields only for authorized contexts
            result.update({
                "input_description": (self.input_description or "")[:1000],
                "input_args_summary": (self.input_args_summary or "")[:1000],
                "output_tokens_used": int(self.output_tokens_used) if self.output_tokens_used else 0,
                "quality_metric": safe_round(self.quality_metric, 4),
                "relevance_score": safe_round(self.relevance_score, 4),
                "user_feedback_text": self.user_feedback_text or "",
                "error_message": self.error_message or "",
                "error_type": self.error_type or "",
                "preference_updates": self.preference_updates if self.preference_updates else [],
            })

        return result

    @classmethod
    def from_dataclass(cls, outcome: "TaskOutcome") -> "Outcome":
        """Create SQL model from dataclass, used during task execution."""
        return cls(
            task_id=outcome.task_id,
            user_id=outcome.user_id,
            tool_name=outcome.tool_name,
            success=outcome.success or False,
            result_confidence=outcome.result_confidence or 0.5,
            started_at=outcome.started_at,
            completed_at=outcome.completed_at,
            duration_seconds=outcome.duration_seconds or 0.0,
            input_description=outcome.input_description or "",
            input_args_summary=outcome.input_args_summary or "",
            output_summary=outcome.output_summary or "",
            output_tokens_used=outcome.output_tokens_used or 0,
            quality_score=outcome.quality_score or 0.5,
            relevance_score=outcome.relevance_score or 0.5,
            user_rating=outcome.user_rating,
            user_feedback_text=outcome.user_feedback_text or "",
            error_message=outcome.error_message or "",
            error_type=outcome.error_type or "",
            preference_updates=outcome.preference_updates or [],
        )

    def to_dataclass(self) -> "TaskOutcome":
        """Convert to TaskOutcome dataclass for compatibility."""
        return TaskOutcome(
            task_id=self.task_id,
            user_id=self.user_id,
            tool_name=self.tool_name,
            success=self.success or False,
            result_confidence=self.result_confidence or 0.5,
            started_at=self.started_at or datetime.now(timezone.utc),
            completed_at=self.completed_at or datetime.now(timezone.utc),
            duration_seconds=self.duration_seconds or 0.0,
            input_description=self.input_description or "",
            input_args_summary=self.input_args_summary or "",
            output_summary=self.output_summary or "",
            output_tokens_used=self.output_tokens_used or 0,
            quality_score=self.quality_score or 0.5,
            relevance_score=self.relevance_score or 0.5,
            user_rating=self.user_rating,
            user_feedback_text=self.user_feedback_text or "",
            error_message=self.error_message or "",
            error_type=self.error_type or "",
            preference_updates=self.preference_updates or [],
        )


@dataclass
class TaskOutcome:
    """Outcome of a task execution for learning and improvement."""
    
    task_id: str
    user_id: str
    tool_name: str
    
    # Result indicators
    success: bool
    result_confidence: float = 0.5  # 0.0-1.0
    
    # Timing
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0.0
    
    # Input context
    input_description: str = ""
    input_args_summary: str = ""  # JSON summary of key args
    
    # Output
    output_summary: str = ""
    output_tokens_used: int = 0
    
    # Quality metrics
    quality_score: float = 0.5  # User-assessed quality 0.0-1.0
    relevance_score: float = 0.5  # How relevant to user's request
    
    # User feedback
    user_rating: int | None = None  # 1-5 scale
    user_feedback_text: str = ""
    
    # System metrics
    error_message: str = ""
    error_type: str = ""
    
    # Learning signals
    preference_updates: list[dict] = field(default_factory=list)
    
    def __post_init__(self):
        """Validate and normalize data."""
        if self.completed_at is None:
            self.completed_at = datetime.now(timezone.utc)
        if self.duration_seconds == 0 and self.started_at:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()


@dataclass
class OutcomeMetric:
    """Aggregate metrics for outcome evaluation."""
    
    metric_name: str
    value: float
    target: float | None = None
    threshold: float | None = None
    
    def meets_target(self) -> bool:
        """Check if metric meets target."""
        if self.target is None:
            if self.threshold is None:
                return True
            # For thresholds, we check if within acceptable range
            return self.value <= self.threshold
        return self.value >= self.target


@dataclass
class OutcomeEvaluation:
    """Comprehensive evaluation of an outcome."""
    
    outcome: TaskOutcome
    metrics: list[OutcomeMetric] = field(default_factory=list)
    
    # Overall assessment
    overall_score: float = 0.5
    is_improved: bool = False
    is_regression: bool = False
    
    # Recommendations
    recommendations: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    
    def calculate_score(self) -> float:
        """Calculate overall improvement score."""
        if not self.metrics:
            return 0.5
        
        # Weighted average of metrics
        total_weight = 0
        weighted_sum = 0
        
        for metric in self.metrics:
            weight = 1.0 / len(self.metrics)
            weighted_sum += metric.value * weight
            total_weight += weight
        
        self.overall_score = weighted_sum / total_weight if total_weight > 0 else 0.5
        return self.overall_score
    
    def analyze(self) -> dict:
        """Analyze the outcome and return insights."""
        self.calculate_score()
        
        # Determine improvement/regression
        if self.outcome.quality_score > 0.7:
            self.is_improved = True
        elif self.outcome.quality_score < 0.3:
            self.is_regression = True
        
        # Generate recommendations
        if self.is_regression:
            self.recommendations.append("Consider adjusting parameters for next attempt")
            self.recommendations.append("Check for environmental factors that may have affected result")
        
        if not self.is_improved and not self.is_regression:
            self.recommendations.append("Result is average - consider trying different approach")
        
        return {
            "score": self.overall_score,
            "improved": self.is_improved,
            "regression": self.is_regression,
            "metrics": {m.metric_name: m.value for m in self.metrics},
            "recommendations": self.recommendations,
        }


@dataclass
class SuccessRateTracker:
    """Track success rates for tools and tasks."""
    
    tool_name: str
    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    
    def current_success_rate(self) -> float:
        """Calculate current success rate."""
        if self.total_attempts == 0:
            return 0.0
        return self.successful_attempts / self.total_attempts
    
    def update(self, success: bool) -> float:
        """Update tracker with new result."""
        self.total_attempts += 1
        if success:
            self.successful_attempts += 1
        else:
            self.failed_attempts += 1
        return self.current_success_rate()


@dataclass
class LearningUpdate:
    """Update to preferences based on outcome analysis."""
    
    user_id: str
    preference_key: str
    old_value: Any
    new_value: Any
    confidence_change: float  # -1.0 to 1.0
    reason: str
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "user_id": self.user_id,
            "preference_key": self.preference_key,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "confidence_change": self.confidence_change,
            "reason": self.reason,
        }


# Outcome status constants
OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_PARTIAL = "partial"
OUTCOME_PENDING = "pending"
OUTCOME_INTERRUPTED = "interrupted"

# Quality levels
QUALITY_HIGH = "high"      # Score >= 0.8
QUALITY_MEDIUM = "medium"  # Score 0.5 - 0.8
QUALITY_LOW = "low"        # Score < 0.5