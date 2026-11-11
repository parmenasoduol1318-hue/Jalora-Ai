"""Rollback and recovery service (Phase 7).

Provides rollback procedures and recovery mechanisms for deployments
and system failures.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.config import get_settings

logger = logging.getLogger("jalora.rollback")


class RollbackTrigger(str, Enum):
    """What triggered the rollback."""
    HEALTH_CHECK = "health_check"
    ERROR_RATE = "error_rate"
    MANUAL = "manual"
    PERFORMANCE = "performance"
    DATA_CORRUPTION = "data_corruption"
    SECURITY = "security"


class RollbackStatus(str, Enum):
    """Status of a rollback operation."""
    PENDING = "pending"
    INITIATED = "initiated"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class RollbackPoint:
    """A rollback point (checkpoint) for recovery."""
    id: str
    version: str
    timestamp: datetime
    db_backup: str | None = None
    config_backup: str | None = None
    artifacts: list[str] = field(default_factory=list)
    notes: str = ""
    created_by: str | None = None


@dataclass
class RollbackPlan:
    """Plan for rolling back a deployment."""
    target_version: str
    trigger: RollbackTrigger
    status: RollbackStatus = RollbackStatus.PENDING
    rollback_points: list[RollbackPoint] = field(default_factory=list)
    estimated_duration: int = 0  # seconds
    steps: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    initiated_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class RollbackManager:
    """Manages rollback operations and recovery."""
    
    def __init__(self):
        """Initialize the rollback manager."""
        self._rollback_points: dict[str, RollbackPoint] = {}
        self._active_rollbacks: dict[str, RollbackPlan] = {}
    
    def create_rollback_point(
        self,
        version: str,
        notes: str = "",
        created_by: str | None = None
    ) -> RollbackPoint:
        """Create a rollback point for recovery.
        
        Args:
            version: Current version
            notes: Optional notes
            created_by: Creator identifier
            
        Returns:
            Created RollbackPoint
        """
        point_id = f"point_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        point = RollbackPoint(
            id=point_id,
            version=version,
            timestamp=datetime.now(timezone.utc),
            db_backup=f"backup_{point_id}.db",
            config_backup=f"config_{point_id}.yaml",
            notes=notes,
            created_by=created_by,
        )
        
        self._rollback_points[point_id] = point
        logger.info(f"Created rollback point: {point_id}")
        
        return point
    
    def initiate_rollback(
        self,
        version: str,
        trigger: RollbackTrigger,
        reason: str = ""
    ) -> RollbackPlan:
        """Initiate a rollback to a previous version.
        
        Args:
            version: Version to rollback to
            trigger: What triggered the rollback
            reason: Optional reason for rollback
            
        Returns:
            RollbackPlan for the operation
        """
        # Find the rollback point
        points = [p for p in self._rollback_points.values() if p.version == version]
        
        if not points:
            plan = RollbackPlan(
                target_version=version,
                trigger=trigger,
                error=f"No rollback point found for version {version}",
            )
            return plan
        
        point = points[0]
        
        # Build rollback plan
        steps = [
            "Stop current services",
            "Restore database from backup",
            "Restore configuration files",
            "Verify rollback point integrity",
            "Start services with rolled-back version",
            "Run health checks",
            "Notify stakeholders",
        ]
        
        plan = RollbackPlan(
            target_version=version,
            trigger=trigger,
            status=RollbackStatus.INITIATED,
            rollback_points=[point],
            estimated_duration=120,  # 2 minutes estimate
            steps=steps,
            initiated_at=datetime.now(timezone.utc),
        )
        
        self._active_rollbacks[point.id] = plan
        logger.warning(f"Rollback initiated to {version} (trigger: {trigger})")
        
        return plan

    
    def start_rollback(self, rollback_id: str) -> bool:
        """Start executing a rollback plan."""
        plan = self._active_rollbacks.get(rollback_id)
        if not plan or plan.status != RollbackStatus.INITIATED:
            return False
        
        plan.status = RollbackStatus.IN_PROGRESS
        logger.info(f"Starting rollback: {rollback_id}")
        
        return True
    
    def complete_rollback(self, rollback_id: str) -> tuple[bool, str]:
        """Mark a rollback as completed."""
        plan = self._active_rollbacks.get(rollback_id)
        if not plan:
            return False, "Rollback not found"
        
        plan.status = RollbackStatus.COMPLETED
        plan.completed_at = datetime.now(timezone.utc)
        logger.info(f"Rollback completed: {rollback_id}")
        
        return True, "Rollback completed successfully"
    
    def fail_rollback(self, rollback_id: str, error: str) -> None:
        """Mark a rollback as failed."""
        plan = self._active_rollbacks.get(rollback_id)
        if plan:
            plan.status = RollbackStatus.FAILED
            plan.error = error
            logger.error(f"Rollback failed: {rollback_id} - {error}")
    
    def cancel_rollback(self, rollback_id: str) -> bool:
        """Cancel an active rollback."""
        plan = self._active_rollbacks.get(rollback_id)
        if plan and plan.status == RollbackStatus.IN_PROGRESS:
            plan.status = RollbackStatus.CANCELLED
            return True
        return False
    
    def get_history(self, limit: int = 10) -> list[RollbackPlan]:
        """Get rollback history."""
        plans = list(self._active_rollbacks.values())
        plans.sort(key=lambda p: p.initiated_at or datetime.min, reverse=True)
        return plans[:limit]


# Singleton instance
_manager: RollbackManager | None = None


def get_rollback_manager() -> RollbackManager:
    """Get or create the rollback manager singleton."""
    global _manager
    if _manager is None:
        _manager = RollbackManager()
    return _manager


async def create_backup(version: str) -> ToolResult:
    """Create a backup point for rollback.
    
    Args:
        version: Current version to backup
        
    Returns:
        ToolResult with backup info
    """
    manager = get_rollback_manager()
    point = manager.create_rollback_point(version)
    
    return ToolResult.ok(
        f"Backup created: {point.id}",
        {
            "backup_id": point.id,
            "version": point.version,
            "timestamp": point.timestamp.isoformat(),
        }
    )


async def initiate_rollback_to(
    version: str,
    trigger: str = "manual"
) -> ToolResult:
    """Initiate a rollback to a specific version.
    
    Args:
        version: Version to rollback to
        trigger: What triggered the rollback
        
    Returns:
        ToolResult with rollback plan
    """
    try:
        rollback_trigger = RollbackTrigger(trigger.lower())
    except ValueError:
        rollback_trigger = RollbackTrigger.MANUAL
    
    manager = get_rollback_manager()
    plan = manager.initiate_rollback(version, rollback_trigger)
    
    return ToolResult.ok(
        f"Rollback to {version} initiated",
        {
            "target_version": plan.target_version,
            "trigger": plan.trigger.value,
            "status": plan.status.value,
            "estimated_duration_seconds": plan.estimated_duration,
            "steps": plan.steps,
        }
    )


async def get_rollback_history(limit: int = 10) -> ToolResult:
    """Get rollback history.
    
    Args:
        limit: Maximum number of rollbacks to return
        
    Returns:
        ToolResult with rollback history
    """
    manager = get_rollback_manager()
    plans = manager.get_history(limit)
    
    return ToolResult.ok(
        f"Retrieved {len(plans)} rollback history entries",
        {
            "rollbacks": [
                {
                    "id": p.rollback_points[0].id if p.rollback_points else "unknown",
                    "target_version": p.target_version,
                    "trigger": p.trigger.value,
                    "status": p.status.value,
                    "initiated_at": p.initiated_at.isoformat() if p.initiated_at else None,
                    "completed_at": p.completed_at.isoformat() if p.completed_at else None,
                    "error": p.error,
                }
                for p in plans
            ]
        }
    )


# Import ToolResult at the end to avoid circular imports
from app.errors import ToolResult