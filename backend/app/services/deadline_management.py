"""Deadline and due date management service (spec §27-28).

Provides services for managing deadlines, due dates, and time-based
task scheduling and reminders.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.task import Task

logger = logging.getLogger("jalora.deadline_management")


class DeadlineStatus(str, Enum):
    """Status of a deadline."""
    ON_TRACK = "on_track"        # Ahead of schedule
    ON_TIME = "on_time"          # Due at expected time
    WARNING = "warning"          # Approaching deadline (24h-1h before)
    OVERDUE = "overdue"          # Past deadline
    FLEXIBLE = "flexible"        # No strict deadline


class UrgencyLevel(str, Enum):
    """Urgency levels for deadline-based prioritization."""
    IMMEDIATE = "immediate"      # Due within 1 hour
    HIGH = "high"               # Due within 24 hours
    MEDIUM = "medium"           # Due within 1 week
    LOW = "low"                 # Due after 1 week
    NONE = "none"               # No deadline


@dataclass
class DeadlineInfo:
    """Information about a task's deadline."""
    task_id: str
    due_at: datetime | None
    status: DeadlineStatus
    urgency: UrgencyLevel
    time_remaining: timedelta | None
    time_until_warning: timedelta | None  # Time until warning threshold
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "task_id": self.task_id,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "status": self.status.value,
            "urgency": self.urgency.value,
            "time_remaining": self.time_remaining.total_seconds() if self.time_remaining else None,
            "time_until_warning": self.time_until_warning.total_seconds() if self.time_until_warning else None,
        }


@dataclass
class DeadlineRule:
    """Rule for deadline calculation and thresholds."""
    warning_threshold_hours: int = 24    # Warn when within this many hours
    grace_period_hours: int = 0          # Hours after deadline before truly overdue
    critical_threshold_hours: int = 1    # Hours before deadline considered "immediate"


class DeadlineManager:
    """Manages deadlines and due dates for tasks."""
    
    def __init__(self, rules: DeadlineRule | None = None):
        """Initialize the deadline manager."""
        self.rules = rules or DeadlineRule()
        self._deadline_cache: dict[str, DeadlineInfo] = {}
    
    def check_deadline(self, 
                      due_at: datetime | None,
                      now: datetime | None = None) -> DeadlineInfo:
        """Check deadline status for a due date.
        
        Args:
            due_at: The due date/time
            now: Current time (defaults to now)
            
        Returns:
            DeadlineInfo with status and urgency
        """
        if now is None:
            now = datetime.now(timezone.utc)
        
        if due_at is None:
            return DeadlineInfo(
                task_id="",
                due_at=None,
                status=DeadlineStatus.FLEXIBLE,
                urgency=UrgencyLevel.NONE,
                time_remaining=None,
                time_until_warning=None,
            )
        
        # Calculate time remaining
        time_remaining = due_at - now
        time_until_warning = due_at - timedelta(hours=self.rules.warning_threshold_hours)
        
        # Determine status
        if time_remaining < timedelta(hours=-self.rules.grace_period_hours):
            status = DeadlineStatus.OVERDUE
        elif time_remaining < timedelta(hours=0):
            status = DeadlineStatus.WARNING
        elif time_remaining < timedelta(hours=self.rules.critical_threshold_hours):
            status = DeadlineStatus.WARNING  # Also urgent
        elif time_remaining < timedelta(hours=self.rules.warning_threshold_hours):
            status = DeadlineStatus.WARNING
        else:
            status = DeadlineStatus.ON_TRACK
        
        # Determine urgency
        if time_remaining < timedelta(hours=0):
            urgency = UrgencyLevel.IMMEDIATE
        elif time_remaining < timedelta(hours=self.rules.critical_threshold_hours):
            urgency = UrgencyLevel.IMMEDIATE
        elif time_remaining < timedelta(hours=self.rules.warning_threshold_hours):
            urgency = UrgencyLevel.HIGH
        elif time_remaining < timedelta(days=7):
            urgency = UrgencyLevel.MEDIUM
        else:
            urgency = UrgencyLevel.LOW
        
        return DeadlineInfo(
            task_id="",  # Set by caller
            due_at=due_at,
            status=status,
            urgency=urgency,
            time_remaining=time_remaining if time_remaining.total_seconds() > 0 else None,
            time_until_warning=time_until_warning if time_until_warning.total_seconds() > 0 else None,
        )
    
    def get_deadline_info(self, task_id: str) -> DeadlineInfo | None:
        """Get deadline info for a task.
        
        Args:
            task_id: Task ID
            
        Returns:
            DeadlineInfo or None if task doesn't have a deadline
        """
        if task_id in self._deadline_cache:
            return self._deadline_cache[task_id]
        
        db = SessionLocal()
        try:
            task = db.get(Task, task_id)
            if not task:
                return None
            
            due_at = getattr(task, 'due_at', None)
            if due_at is None:
                return None
            
            info = self.check_deadline(due_at)
            info.task_id = task_id
            self._deadline_cache[task_id] = info
            return info
            
        finally:
            db.close()
    
    def get_overdue_tasks(self, user_id: str | None = None) -> list[DeadlineInfo]:
        """Get all overdue tasks.
        
        Args:
            user_id: Optional user ID to filter by
            
        Returns:
            List of overdue task deadline info
        """
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            
            query = select(Task, Task.due_at).where(
                Task.due_at < now,
                Task.status != "completed"
            )
            
            if user_id:
                query = query.where(Task.user_id == user_id)
            
            results = []
            for task, due_at in db.execute(query).all():
                info = DeadlineInfo(
                    task_id=task.id,
                    due_at=due_at,
                    status=DeadlineStatus.OVERDUE,
                    urgency=UrgencyLevel.IMMEDIATE,
                    time_remaining=due_at - now,
                    time_until_warning=None,
                )
                results.append(info)
            
            return results
        finally:
            db.close()
    
    def get_upcoming_deadlines(self, 
                               user_id: str | None = None,
                               hours: int = 24) -> list[DeadlineInfo]:
        """Get tasks with deadlines within the next X hours.
        
        Args:
            user_id: Optional user ID to filter by
            hours: Number of hours to look ahead
            
        Returns:
            List of upcoming task deadline info
        """
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            future = now + timedelta(hours=hours)
            
            query = select(Task, Task.due_at).where(
                Task.due_at >= now,
                Task.due_at <= future,
                Task.status != "completed"
            ).order_by(Task.due_at)
            
            if user_id:
                query = query.where(Task.user_id == user_id)
            
            results = []
            for task, due_at in db.execute(query).all():
                info = self.check_deadline(due_at)
                info.task_id = task.id
                results.append(info)
            
            return results
        finally:
            db.close()
    
    def get_deadline_summary(self, user_id: str) -> dict:
        """Get a summary of deadlines for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Dictionary with deadline statistics
        """
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            
            all_tasks = db.query(Task).filter(
                Task.user_id == user_id,
                Task.status != "completed"
            ).all()
            
            overdue = 0
            warning = 0
            on_track = 0
            no_deadline = 0
            
            for task in all_tasks:
                due_at = getattr(task, 'due_at', None)
                if due_at is None:
                    no_deadline += 1
                else:
                    info = self.check_deadline(due_at)
                    if info.status == DeadlineStatus.OVERDUE:
                        overdue += 1
                    elif info.status == DeadlineStatus.WARNING:
                        warning += 1
                    else:
                        on_track += 1
            
            return {
                "total_active": len(all_tasks),
                "overdue": overdue,
                "warning": warning,
                "on_track": on_track,
                "no_deadline": no_deadline,
            }
        finally:
            db.close()
    
    def suggest_deadline_extension(self, 
                                   task_id: str,
                                   current_due_at: datetime,
                                   completion_time_estimate: timedelta | None = None) -> datetime:
        """Suggest an appropriate deadline extension.
        
        Args:
            task_id: Task ID
            current_due_at: Current due date
            completion_time_estimate: Estimated time to complete
            
        Returns:
            Suggested new due date
        """
        now = datetime.now(timezone.utc)
        
        # If already overdue, suggest extension to today + 1 day
        if current_due_at < now:
            return now + timedelta(days=1)
        
        # Calculate buffer based on task complexity
        buffer = timedelta(hours=2)  # Default 2-hour buffer
        
        if completion_time_estimate:
            buffer = completion_time_estimate + timedelta(hours=1)
        
        # Add additional buffer for uncertainty
        suggested = current_due_at + buffer
        
        # Don't suggest a past-due date
        if suggested < now:
            return now + timedelta(hours=24)
        
        return suggested
    
    def clear_cache(self) -> None:
        """Clear the deadline cache."""
        self._deadline_cache.clear()


@dataclass
class TimeEstimate:
    """Estimated time for task completion."""
    task_id: str
    estimated_minutes: int
    confidence: float  # 0.0-1.0
    factors: list[str] = field(default_factory=list)  # What affected the estimate


class TimeEstimator:
    """Estimates time for task completion."""
    
    def __init__(self):
        self._estimate_cache: dict[str, TimeEstimate] = {}
    
    def estimate_completion(self, task: Task) -> TimeEstimate:
        """Estimate time to complete a task.
        
        Args:
            task: The task to estimate
            
        Returns:
            TimeEstimate with minutes and confidence
        """
        task_id = task.id
        
        if task_id in self._estimate_cache:
            return self._estimate_cache[task_id]
        
        # Simple heuristic: longer plans = longer time
        plan_length = len(task.goal) if task.goal else 0
        step_count = len(task.plan) if task.plan else 1
        
        # Base estimation
        base_minutes = 10  # Minimum 10 minutes for any task
        
        if plan_length > 100:
            base_minutes += 30
        elif plan_length > 50:
            base_minutes += 15
        
        # Each step adds approximately 10 minutes
        base_minutes += step_count * 10
        
        # Adjust for task type (would be derived from tool names in reality)
        tool_type = self._infer_task_type(task)
        if tool_type == "complex":
            base_minutes *= 2
        elif tool_type == "medium":
            base_minutes *= 1.5
        
        confidence = 0.7  # Default confidence
        
        estimate = TimeEstimate(
            task_id=task_id,
            estimated_minutes=int(base_minutes),
            confidence=confidence,
            factors=["plan_length", "step_count", "task_type"]
        )
        
        self._estimate_cache[task_id] = estimate
        return estimate
    
    def _infer_task_type(self, task: Task) -> str:
        """Infer task type from goal/plan."""
        goal_lower = (task.goal or "").lower()
        plan_lower = str(task.plan or "").lower()
        
        complex_keywords = ["research", "analyze", "compare", "evaluate", "synthesize"]
        medium_keywords = ["write", "create", "generate", "plan", "design"]
        
        for keyword in complex_keywords:
            if keyword in goal_lower or keyword in plan_lower:
                return "complex"
        
        for keyword in medium_keywords:
            if keyword in goal_lower or keyword in plan_lower:
                return "medium"
        
        return "simple"
    
    def clear_cache(self) -> None:
        """Clear the estimate cache."""
        self._estimate_cache.clear()


# Singleton instances
_deadline_manager: DeadlineManager | None = None
_time_estimator: TimeEstimator | None = None


def get_deadline_manager() -> DeadlineManager:
    """Get or create the deadline manager singleton."""
    global _deadline_manager
    if _deadline_manager is None:
        _deadline_manager = DeadlineManager()
    return _deadline_manager


def get_time_estimator() -> TimeEstimator:
    """Get or create the time estimator singleton."""
    global _time_estimator
    if _time_estimator is None:
        _time_estimator = TimeEstimator()
    return _time_estimator