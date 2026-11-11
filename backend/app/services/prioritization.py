"""Task prioritization service (spec §27-29).

Provides services for calculating task priorities, managing priority queues,
and optimizing task execution order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.actions import UserAction
from app.models.task import Task, TaskStep
from app.models.task_priority import (
    PriorityLevel,
    PriorityStrategy,
    PriorityFactors,
    PriorityCalculation,
    PriorityHistory,
    TaskWithPriority,
    calculate_deadline_factor,
    calculate_impact_factor,
    rank_priority_level,
)
from app.models.user import User

logger = logging.getLogger("jalora.prioritization")


@dataclass
class PriorityQueue:
    """Queue of tasks sorted by priority."""
    tasks: list[TaskWithPriority] = field(default_factory=list)
    strategy: PriorityStrategy = PriorityStrategy.HYBRID
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def add_task(self, task: TaskWithPriority) -> None:
        """Add a task to the queue."""
        self.tasks.append(task)
        self._recalculate()
    
    def pop_highest(self) -> TaskWithPriority | None:
        """Remove and return the highest priority task."""
        if not self.tasks:
            return None
        task = max(self.tasks, key=lambda t: t.priority_score)
        self.tasks.remove(task)
        self.last_updated = datetime.now(timezone.utc)
        return task
    
    def peek(self) -> TaskWithPriority | None:
        """Return the highest priority task without removing it."""
        if not self.tasks:
            return None
        return max(self.tasks, key=lambda t: t.priority_score)
    
    def _recalculate(self) -> None:
        """Re-sort the queue based on priority."""
        self.tasks.sort(key=lambda t: t.priority_score, reverse=True)
        self.last_updated = datetime.now(timezone.utc)


class TaskPrioritizer:
    """Calculates and manages task priorities."""
    
    def __init__(self):
        """Initialize the prioritizer."""
        self._history: dict[str, PriorityHistory] = {}
    
    def calculate_priority(self,
                          task: Task | TaskWithPriority,
                          deadline: datetime | None = None,
                          impact_description: str | None = None,
                          affected_users: int | None = None,
                          business_critical: bool = False,
                          dependencies: list[str] | None = None,
                          context_relevance: float = 0.5,
                          user_preference: float = 0.5) -> PriorityCalculation:
        """Calculate priority for a task.
        
        Args:
            task: The task to prioritize
            deadline: Optional deadline
            impact_description: Description of impact
            affected_users: Number of affected users
            business_critical: Whether task is business critical
            dependencies: Task IDs this depends on
            context_relevance: How relevant to current context (0-1)
            user_preference: User-set priority preference (0-1)
            
        Returns:
            PriorityCalculation with level, score, and factors
        """
        # Calculate factors
        deadline_factor = calculate_deadline_factor(deadline or getattr(task, 'due_at', None))
        impact_factor = calculate_impact_factor(
            impact_description, 
            affected_users,
            business_critical
        )
        
        # Dependency factor (blocking others increases priority)
        dependency_factor = 0.0
        if dependencies:
            db = SessionLocal()
            try:
                blocking_tasks = db.query(Task).filter(
                    Task.id.in_(dependencies),
                    Task.status != "completed"
                ).count()
                dependency_factor = min(1.0, blocking_tasks / 10.0)
            finally:
                db.close()
        
        factors = PriorityFactors(
            deadline_factor=deadline_factor,
            impact_factor=impact_factor,
            urgency_factor=context_relevance,  # Using context_relevance as urgency
            user_priority=user_preference,
            dependency_factor=dependency_factor,
            context_factor=context_relevance,
            learned_preference=0.5,  # Default - would be updated from history
        )
        
        score = factors.weighted_score()
        priority_level = rank_priority_level(score)
        
        # Generate reasoning
        reasoning = []
        if deadline_factor > 0.5:
            reasoning.append(f"Deadline approaching ({deadline_term(deadline)})")
        if impact_factor > 0.3:
            reasoning.append("High impact task")
        if business_critical:
            reasoning.append("Business critical")
        if dependency_factor > 0:
            reasoning.append(f"Has {int(dependency_factor * 10)} blocking dependencies")
        
        if not reasoning:
            reasoning.append("Standard priority based on context and preferences")
        
        return PriorityCalculation(
            task_id=task.id if hasattr(task, 'id') else str(id(task)),
            priority_level=priority_level,
            score=score,
            factors=factors,
            strategy=PriorityStrategy.HYBRID,
            reasoning=reasoning,
        )
    
    def prioritize_queue(self,
                        tasks: list[Task | TaskWithPriority],
                        strategy: PriorityStrategy = PriorityStrategy.HYBRID) -> list[tuple[Task | TaskWithPriority, PriorityCalculation]]:
        """Prioritize a list of tasks.
        
        Args:
            tasks: Tasks to prioritize
            strategy: Prioritization strategy
            
        Returns:
            List of (task, calculation) tuples sorted by priority
        """
        results = []
        
        for task in tasks:
            calc = self.calculate_priority(task)
            results.append((task, calc))
        
        results.sort(key=lambda x: x[1].score, reverse=True)
        return results
    
    def get_queue(self, user_id: str, limit: int = 50) -> list[TaskWithPriority]:
        """Get prioritized queue of tasks for a user.
        
        Args:
            user_id: User ID
            limit: Maximum tasks to return
            
        Returns:
            List of tasks with priorities
        """
        db = SessionLocal()
        try:
            tasks = db.query(Task).filter(
                Task.user_id == user_id,
                Task.status != "completed"
            ).limit(limit).all()
            
            # Get recent actions to infer learned preferences
            recent_actions = db.query(UserAction).filter(
                UserAction.user_id == user_id,
                UserAction.started_at >= datetime.now(timezone.utc) - timedelta(days=7)
            ).all()
            
            # Simple preference inference from recent actions
            preferred_tools = {}
            for action in recent_actions:
                tool = action.tool_name
                preferred_tools[tool] = preferred_tools.get(tool, 0) + 1
            
            result = []
            for task in tasks:
                calc = self.calculate_priority(
                    task,
                    deadline=getattr(task, 'due_at', None),
                    context_relevance=0.5,
                )
                
                task_with_priority = TaskWithPriority(
                    task_id=task.id,
                    title=getattr(task, 'goal', '')[:100],
                    description=getattr(task, 'plan', ''),
                    priority_level=calc.priority_level,
                    priority_score=calc.score,
                    deadline=getattr(task, 'due_at', None),
                    created_at=task.created_at,
                )
                result.append(task_with_priority)
            
            result.sort(key=lambda t: t.priority_score, reverse=True)
            return result[:limit]
            
        finally:
            db.close()
    
    def adjust_priority(self,
                       task_id: str,
                       adjustment: float,
                       reason: str) -> PriorityCalculation:
        """Manually adjust a task's priority.
        
        Args:
            task_id: Task to adjust
            adjustment: Adjustment to apply (-1.0 to 1.0)
            reason: Reason for adjustment
            
        Returns:
            Updated priority calculation
        """
        db = SessionLocal()
        try:
            task = db.get(Task, task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")
            
            calc = self.calculate_priority(task)
            new_score = max(0.0, min(1.0, calc.score + adjustment))
            calc.priority_level = rank_priority_level(new_score)
            calc.score = new_score
            calc.reasoning.append(f"Manual adjustment: {reason}")
            
            return calc
        finally:
            db.close()


@dataclass
class NextAction:
    """Recommended next action based on priority analysis."""
    action_type: str  # "execute_task", "review_deadlines", "reassess_priority"
    task_id: str | None = None
    priority_level: PriorityLevel | None = None
    urgency: str = "later"  # "now", "soon", "later"
    justification: str = ""
    estimated_time_minutes: int = 5
    suggested_parameters: dict = field(default_factory=dict)


class NextActionPlanner:
    """Plans the next action based on priority analysis."""
    
    def __init__(self):
        self._prioritizer = TaskPrioritizer()
    
    def plan_next_action(self, user_id: str) -> NextAction:
        """Plan the next action to take.
        
        Args:
            user_id: User to plan for
            
        Returns:
            NextAction recommendation
        """
        # Get high priority tasks
        db = SessionLocal()
        try:
            # Find critical tasks first
            critical_tasks = db.query(Task).filter(
                Task.user_id == user_id,
                Task.status != "completed"
            ).all()
            
            # Check for overdue tasks
            now = datetime.now(timezone.utc)
            overdue = [t for t in critical_tasks 
                        if hasattr(t, 'due_at') and t.due_at and t.due_at < now]
            
            if overdue:
                # Sort by how overdue they are
                overdue.sort(key=lambda t: t.due_at)
                task = overdue[0]
                return NextAction(
                    action_type="execute_task",
                    task_id=task.id,
                    priority_level=PriorityLevel.CRITICAL,
                    urgency="now",
                    justification=f"Task is overdue by {(now - task.due_at).seconds // 3600} hours",
                    estimated_time_minutes=30,
                )
            
            # No overdue tasks, pick highest priority
            if critical_tasks:
                calculations = []
                for task in critical_tasks:
                    calc = self._prioritizer.calculate_priority(task)
                    calculations.append((task, calc))
                
                calculations.sort(key=lambda x: x[1].score, reverse=True)
                task, calc = calculations[0]
                
                urgency = "now" if calc.score > 0.8 else "soon"
                
                return NextAction(
                    action_type="execute_task",
                    task_id=task.id,
                    priority_level=calc.priority_level,
                    urgency=urgency,
                    justification="; ".join(calc.reasoning),
                    estimated_time_minutes=15,
                )
            
            # No active tasks
            return NextAction(
                action_type="execute_task",
                task_id=None,
                priority_level=None,
                urgency="later",
                justification="No active tasks for this user",
            )
            
        finally:
            db.close()


# Helper function
def deadline_term(deadline: datetime | None) -> str:
    """Return human-readable deadline term."""
    if not deadline:
        return "no deadline"
    
    now = datetime.now(timezone.utc)
    delta = deadline - now
    
    hours = delta.total_seconds() / 3600
    
    if hours <= 1:
        return "within the hour"
    elif hours <= 24:
        return f"in {int(hours)} hours"
    elif hours <= 168:
        days = int(hours / 24)
        return f"in {days} days"
    else:
        return "in more than a week"


# Singleton instances
_prioritizer: TaskPrioritizer | None = None
_next_planner: NextActionPlanner | None = None


def get_prioritizer() -> TaskPrioritizer:
    """Get or create the prioritizer singleton."""
    global _prioritizer
    if _prioritizer is None:
        _prioritizer = TaskPrioritizer()
    return _prioritizer


def get_next_action_planner() -> NextActionPlanner:
    """Get or create the next action planner singleton."""
    global _next_planner
    if _next_planner is None:
        _next_planner = NextActionPlanner()
    return _next_planner