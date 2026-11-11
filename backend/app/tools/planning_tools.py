"""Task planning and prioritization tools (Phase 2).

Tools for creating plans, breaking down tasks, and managing priorities.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.context import get_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.task import Task
from app.models.task_priority import (
    PriorityLevel,
    PriorityFactors,
    calculate_deadline_factor,
    calculate_impact_factor,
    rank_priority_level,
)
from app.tools.base import PermissionLevel, registry

logger = logging.getLogger("jalora.planning_tools")


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Planning Tools
# ============================================================================

async def create_task_plan(
    goal: str,
    steps: list[str] | None = None,
    deadline_str: str | None = None,
    tags: list[str] | None = None,
    priority: str = "medium"
) -> ToolResult:
    """Create a task with a structured plan.
    
    Args:
        goal: The primary goal or description of the task
        steps: Optional list of steps/steps to complete (auto-generated if not provided)
        deadline_str: Optional ISO format deadline string
        tags: Optional list of tags for categorization
        priority: Priority level (critical, high, medium, low)
        
    Returns:
        ToolResult with the created task ID and plan details
    """
    try:
        user_id = _user_id()
        
        # Auto-generate steps if not provided
        if steps is None:
            steps = _generate_standard_plan(goal)
        
        # Parse deadline
        deadline = None
        if deadline_str:
            try:
                deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
            except ValueError:
                return ToolResult.fail("Invalid deadline format", f"'{deadline_str}' is not a valid ISO format")
        
        # Calculate priority score
        try:
            priority_level = PriorityLevel(priority.lower())
            priority_score = _priority_to_score(priority_level)
        except ValueError:
            return ToolResult.fail("Invalid priority", f"'{priority}' is not a valid priority level")
        
        # Create task plan structure
        plan = []
        for i, step in enumerate(steps):
            plan.append({
                "step": i + 1,
                "description": step,
                "status": "pending",
                "estimated_minutes": _estimate_step_time(step, i),
            })
        
        # Create the task
        task = Task(
            user_id=user_id,
            goal=goal,
            plan=plan,
            status="queued",
            priority=priority_score,
            due_at=deadline,
        )
        
        if hasattr(task, 'tags') and tags:
            task.tags = tags
        
        db = SessionLocal()
        try:
            db.add(task)
            db.commit()
            db.refresh(task)
            
            return ToolResult.ok(
                f"Created task '{goal}' with {len(plan)} steps",
                {
                    "task_id": task.id,
                    "goal": task.goal,
                    "steps_count": len(plan),
                    "plan": plan,
                    "priority": priority,
                    "deadline": deadline.isoformat() if deadline else None,
                }
            )
        finally:
            db.close()
    except Exception as e:
        logger.exception("create_task_plan failed")
        return ToolResult.fail("Failed to create task", str(e))


async def generate_plan_steps(
    goal: str,
    framework: str = "standard"
) -> ToolResult:
    """Generate a detailed plan for achieving a goal.
    
    Args:
        goal: The goal to plan for
        framework: Planning framework (standard, agile, waterfall, mindmap)
        
    Returns:
        ToolResult with the generated steps
    """
    if framework == "standard":
        steps = _generate_standard_plan(goal)
    elif framework == "agile":
        steps = _generate_agile_plan(goal)
    elif framework == "waterfall":
        steps = _generate_waterfall_plan(goal)
    elif framework == "mindmap":
        steps = _generate_mindmap_plan(goal)
    else:
        return ToolResult.fail("Invalid framework", f"Unknown framework: {framework}")
    
    return ToolResult.ok(
        f"Generated {len(steps)} steps for goal: {goal}",
        {"steps": steps, "framework": framework}
    )


async def prioritize_tasks(
    task_ids: list[str],
    strategy: str = "hybrid",
    user_id: str | None = None
) -> ToolResult:
    """Prioritize a list of existing tasks.
    
    Args:
        task_ids: List of task IDs to prioritize
        strategy: Prioritization strategy (deadline, impact, hybrid, custom)
        user_id: Optional user ID (uses context if not provided)
        
    Returns:
        ToolResult with prioritized task list
    """
    try:
        actual_user_id = user_id or _user_id()
        
        db = SessionLocal()
        try:
            tasks = db.query(Task).filter(
                Task.id.in_(task_ids),
                Task.user_id == actual_user_id
            ).all()
        finally:
            db.close()
        
        if not tasks:
            return ToolResult.fail("No tasks found", "None of the provided task IDs belong to this user")
        
        prioritized = []
        for task in tasks:
            # Calculate priority based on strategy
            factors = PriorityFactors()
            
            if hasattr(task, 'due_at') and task.due_at:
                factors.deadline_factor = calculate_deadline_factor(task.due_at)
            
            # User-defined priority field
            if hasattr(task, 'priority') and task.priority:
                factors.user_priority = task.priority / 100.0 if task.priority else 0.5
            
            score = factors.weighted_score()
            priority_level = rank_priority_level(score)
            
            prioritized.append({
                "task_id": task.id,
                "goal": task.goal,
                "priority": priority_level.value,
                "priority_score": score,
                "status": task.status,
            })
        
        # Sort by priority score
        prioritized.sort(key=lambda x: x["priority_score"], reverse=True)
        
        return ToolResult.ok(
            f"Prioritized {len(prioritized)} tasks",
            {"tasks": prioritized}
        )
    except Exception as e:
        logger.exception("prioritize_tasks failed")
        return ToolResult.fail("Failed to prioritize tasks", str(e))


async def get_task_deadline_info(
    task_id: str
) -> ToolResult:
    """Get deadline information for a task.
    
    Args:
        task_id: Task ID to look up
        
    Returns:
        ToolResult with deadline info
    """
    try:
        db = SessionLocal()
        try:
            task = db.get(Task, task_id)
        finally:
            db.close()
        
        if not task:
            return ToolResult.fail("Task not found", f"Task {task_id} does not exist")
        
        due_at = getattr(task, 'due_at', None)
        now = datetime.now(timezone.utc)
        
        if due_at:
            time_remaining = due_at - now
            is_overdue = time_remaining.total_seconds() < 0
            hours_remaining = time_remaining.total_seconds() / 3600
            
            return ToolResult.ok(
                "Retrieved deadline info",
                {
                    "task_id": task.id,
                    "due_at": due_at.isoformat(),
                    "is_overdue": is_overdue,
                    "time_remaining_hours": hours_remaining,
                    "time_remaining_formatted": _format_time_remaining(time_remaining),
                }
            )
        else:
            return ToolResult.ok(
                "Task has no deadline",
                {
                    "task_id": task.id,
                    "has_deadline": False,
                    "due_at": None,
                }
            )
    except Exception as e:
        logger.exception("get_task_deadline_info failed")
        return ToolResult.fail("Failed to get deadline info", str(e))


# ============================================================================
# Internal Functions
# ============================================================================

def _priority_to_score(priority: PriorityLevel) -> float:
    """Convert PriorityLevel to score."""
    mapping = {
        PriorityLevel.CRITICAL: 0.9,
        PriorityLevel.HIGH: 0.7,
        PriorityLevel.MEDIUM: 0.5,
        PriorityLevel.LOW: 0.3,
        PriorityLevel.FUTURE: 0.1,
    }
    return mapping.get(priority, 0.5)


def _estimate_step_time(step: str, index: int) -> int:
    """Estimate time in minutes for a step."""
    # Simple heuristic based on step length and complexity
    base_time = 10
    
    if "analyze" in step.lower() or "research" in step.lower():
        base_time = 30
    elif "write" in step.lower() or "create" in step.lower():
        base_time = 20
    elif "review" in step.lower() or "check" in step.lower():
        base_time = 15
    
    return base_time + (index * 5)  # Add time for iteration


def _format_time_remaining(delta: timedelta) -> str:
    """Format time remaining in human-readable format."""
    total_seconds = int(delta.total_seconds())
    
    if total_seconds < 0:
        return "Overdue"
    
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    
    if hours > 24:
        days = hours // 24
        return f"{days}d {hours % 24}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def _generate_standard_plan(goal: str) -> list[str]:
    """Generate a standard plan (research, plan, execute, review)."""
    return [
        f"Clarify and define the goal: {goal}",
        "Research and gather relevant information",
        "Break down into smaller sub-tasks",
        "Create a detailed action plan",
        "Execute the plan step by step",
        "Review and validate results",
        "Iterate or adjust as needed",
    ]


def _generate_agile_plan(goal: str) -> list[str]:
    """Generate an agile-style plan (sprints, backlog)."""
    return [
        f"Product backlog: {goal}",
        "Identify sprint goal and scope",
        "Create user stories and acceptance criteria",
        "Prioritize backlog items",
        "Sprint planning and task estimation",
        "Daily standup: track progress",
        "Sprint review and retrospective",
    ]


def _generate_waterfall_plan(goal: str) -> list[str]:
    """Generate a waterfall-style plan (sequential phases)."""
    return [
        f"Initiation: Define {goal} objectives",
        "Planning: Create detailed project plan",
        "Analysis: Gather and analyze requirements",
        "Design: Create system architecture/design",
        "Implementation: Build and configure solutions",
        "Testing: Validate quality and functionality",
        "Deployment: Release to production",
        "Maintenance: Monitor and support",
    ]


def _generate_mindmap_plan(goal: str) -> list[str]:
    """Generate a mind map style plan (branches from central idea)."""
    return [
        f"Central node: {goal}",
        "Branch 1: Problem definition and scope",
        "Branch 2: Solution approaches and alternatives",
        "Branch 3: Resources and constraints",
        "Branch 4: Timeline and milestones",
        "Branch 5: Risks and mitigation strategies",
        "Branch 6: Success metrics and validation",
    ]


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(
    create_task_plan,
    permission=PermissionLevel.AUTO,
    category="planning",
    tags=["plan", "task", "create", "workflow"]
)
registry.register(
    generate_plan_steps,
    permission=PermissionLevel.AUTO,
    category="planning",
    tags=["plan", "brainstorm", "steps"]
)
registry.register(
    prioritize_tasks,
    permission=PermissionLevel.AUTO,
    category="planning",
    tags=["priority", "sort", "rank"]
)
registry.register(
    get_task_deadline_info,
    permission=PermissionLevel.AUTO,
    category="planning",
    tags=["deadline", "schedule", "time"]
)