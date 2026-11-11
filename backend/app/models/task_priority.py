"""Task priority and prioritization models (spec §27-28).

Provides models for task priority calculation, priority history tracking,
and prioritization strategies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


class PriorityLevel(str, Enum):
    """Task priority levels."""
    CRITICAL = "critical"      # Must be done immediately
    HIGH = "high"              # Should be done soon
    MEDIUM = "medium"          # Normal priority
    LOW = "low"                # Can wait
    FUTURE = "future"          # Scheduled for later


class PriorityStrategy(str, Enum):
    """Strategies for calculating task priority."""
    DEADLINE_BASED = "deadline_based"      # Closer deadlines = higher priority
    IMPACT_BASED = "impact_based"          # Higher impact = higher priority
    USER_REQUEST = "user_request"          # Direct user request
    CONTEXTUAL = "contextual"              # Based on current context
    HYBRID = "hybrid"                      # Combination of factors
    TIME_SENSITIVE = "time_sensitive"      # Time-critical tasks
    QUEUED = "queued"                      # Standard queue order


@dataclass
class PriorityFactors:
    """Factors considered when calculating priority."""
    deadline_factor: float = 0.0           # 0.0-1.0 (closer = higher)
    impact_factor: float = 0.0             # 0.0-1.0 (higher impact = higher)
    urgency_factor: float = 0.0            # 0.0-1.0 (more urgent = higher)
    user_priority: float = 0.5             # 0.0-1.0 (direct user setting)
    dependency_factor: float = 0.0         # 0.0-1.0 (blocking others = higher)
    context_factor: float = 0.0            # 0.0-1.0 (relevant to current work)
    learned_preference: float = 0.5        # From historical feedback
    
    def weighted_score(self) -> float:
        """Calculate weighted priority score."""
        weights = {
            "deadline_factor": 0.20,
            "impact_factor": 0.15,
            "urgency_factor": 0.15,
            "user_priority": 0.15,
            "dependency_factor": 0.15,
            "context_factor": 0.10,
            "learned_preference": 0.10,
        }
        
        total = 0.0
        for factor, value in [
            ("deadline_factor", self.deadline_factor),
            ("impact_factor", self.impact_factor),
            ("urgency_factor", self.urgency_factor),
            ("user_priority", self.user_priority),
            ("dependency_factor", self.dependency_factor),
            ("context_factor", self.context_factor),
            ("learned_preference", self.learned_preference),
        ]:
            total += value * weights[factor]
        
        return min(1.0, max(0.0, total))


@dataclass
class PriorityCalculation:
    """Result of a priority calculation."""
    task_id: str
    priority_level: PriorityLevel
    score: float  # 0.0-1.0
    factors: PriorityFactors
    strategy: PriorityStrategy
    calculated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reasoning: list[str] = field(default_factory=list)
    
    def explain(self) -> str:
        """Return human-readable explanation of the priority."""
        lines = [f"Task {self.task_id} priority: {self.priority_level.value} (score: {self.score:.2f})"]
        lines.append(f"Strategy: {self.strategy.value}")
        lines.append("Key factors:")
        lines.append(f"  - Deadline factor: {self.factors.deadline_factor:.2f}")
        lines.append(f"  - Impact factor: {self.factors.impact_factor:.2f}")
        lines.append(f"  - Urgency factor: {self.factors.urgency_factor:.2f}")
        lines.append(f"  - User priority: {self.factors.user_priority:.2f}")
        if self.reasoning:
            lines.append("Reasoning:")
            lines.extend(f"  - {r}" for r in self.reasoning)
        return "\n".join(lines)


@dataclass
class PriorityHistory:
    """History of priority changes for a task."""
    task_id: str
    changes: list[PriorityCalculation] = field(default_factory=list)
    
    def add_change(self, calculation: PriorityCalculation) -> None:
        """Add a priority change to history."""
        self.changes.append(calculation)
    
    def current_priority(self) -> PriorityLevel | None:
        """Get the current priority level."""
        if not self.changes:
            return None
        return self.changes[-1].priority_level


@dataclass
class TaskWithPriority:
    """A task with its calculated priority."""
    task_id: str
    title: str
    description: str = ""
    priority_level: PriorityLevel = PriorityLevel.MEDIUM
    priority_score: float = 0.5
    deadline: datetime | None = None
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    assigned_to: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Priority calculation utilities
def calculate_deadline_factor(deadline: datetime | None, 
                              now: datetime | None = None) -> float:
    """Calculate deadline factor (0.0-1.0, higher for closer deadlines)."""
    if not deadline:
        return 0.0
    
    if now is None:
        now = datetime.now(timezone.utc)
    
    if deadline <= now:
        return 1.0  # Overdue = highest factor
    
    hours_until = (deadline - now).total_seconds() / 3600
    
    if hours_until <= 1:
        return 1.0
    elif hours_until <= 24:
        return 0.9
    elif hours_until <= 72:
        return 0.7
    elif hours_until <= 168:  # 1 week
        return 0.5
    elif hours_until <= 720:  # 1 month
        return 0.3
    else:
        return 0.1


def calculate_impact_factor(impact_description: str | None = None,
                            affected_users: int | None = None,
                            business_critical: bool = False) -> float:
    """Calculate impact factor (0.0-1.0, higher for bigger impact)."""
    score = 0.0
    
    if business_critical:
        score += 0.4
    
    if affected_users is not None:
        if affected_users > 1000:
            score += 0.3
        elif affected_users > 100:
            score += 0.2
        elif affected_users > 10:
            score += 0.1
        elif affected_users > 0:
            score += 0.05
    
    if impact_description:
        desc_lower = impact_description.lower()
        if "critical" in desc_lower or "urgent" in desc_lower:
            score += 0.2
        elif "important" in desc_lower or "significant" in desc_lower:
            score += 0.1
    
    return min(1.0, score)


def rank_priority_level(score: float) -> PriorityLevel:
    """Convert a score (0.0-1.0) to a priority level."""
    if score >= 0.8:
        return PriorityLevel.CRITICAL
    elif score >= 0.6:
        return PriorityLevel.HIGH
    elif score >= 0.4:
        return PriorityLevel.MEDIUM
    elif score >= 0.2:
        return PriorityLevel.LOW
    else:
        return PriorityLevel.FUTURE