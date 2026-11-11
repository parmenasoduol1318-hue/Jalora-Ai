"""Recovery and rollback tools (Phase 7).

Tools for system recovery, backup management, and rollback operations.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services.rollback import (
    get_rollback_manager,
    RollbackTrigger,
    RollbackStatus,
)
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Recovery Tools
# ============================================================================

async def create_backup(version: str, notes: str = "") -> ToolResult:
    """Create a backup point for potential rollback.
    
    Args:
        version: Current version to backup
        notes: Optional notes about the backup
        
    Returns:
        ToolResult with backup information
    """
    from app.services.rollback import create_backup
    
    result = await create_backup(version)
    return result


async def initiate_rollback(
    version: str,
    reason: str = "",
    trigger: str = "manual"
) -> ToolResult:
    """Initiate rollback to a specific version.
    
    Args:
        version: Target version to rollback to
        reason: Reason for rollback
        trigger: What triggered the rollback (health_check, error_rate, manual, etc.)
        
    Returns:
        ToolResult with rollback plan
    """
    from app.services.rollback import initiate_rollback_to
    
    result = await initiate_rollback_to(version, trigger)
    return result


async def get_recovery_status() -> ToolResult:
    """Get current recovery/rollback status.
    
    Returns:
        ToolResult with recovery status
    """
    manager = get_rollback_manager()
    history = manager.get_history(5)
    
    active = [p for p in history if p.status == RollbackStatus.IN_PROGRESS]
    pending = [p for p in history if p.status == RollbackStatus.INITIATED]
    completed = [p for p in history if p.status == RollbackStatus.COMPLETED]
    
    return ToolResult.ok(
        f"Recovery status: {len(active)} active, {len(pending)} pending",
        {
            "active_rollbacks": len(active),
            "pending_rollbacks": len(pending),
            "recent_completions": len(completed),
        }
    )


async def list_backup_points() -> ToolResult:
    """List all available backup points.
    
    Returns:
        ToolResult with backup points
    """
    manager = get_rollback_manager()
    
    points = [
        {
            "id": p.id,
            "version": p.version,
            "timestamp": p.timestamp.isoformat(),
            "has_db_backup": bool(p.db_backup),
            "has_config_backup": bool(p.config_backup),
            "artifact_count": len(p.artifacts),
        }
        for p in manager._rollback_points.values()
    ]
    
    points.sort(key=lambda p: p["timestamp"], reverse=True)
    
    return ToolResult.ok(
        f"Found {len(points)} backup points",
        {"backup_points": points, "total": len(points)}
    )


async def run_health_check() -> ToolResult:
    """Run health check to detect issues.
    
    Returns:
        ToolResult with health status
    """
    import sys
    
    issues = []
    
    # Check Python version
    if sys.version_info < (3, 10):
        issues.append({"severity": "warning", "message": f"Python {sys.version} is old"})
    
    # Check platform
    platform = sys.platform
    if platform.startswith("win"):
        issues.append({"severity": "info", "message": f"Running on Windows: {platform}"})
    
    is_healthy = len([i for i in issues if i["severity"] == "error"]) == 0
    
    return ToolResult.ok(
        f"Health check: {'healthy' if is_healthy else 'issues found'}",
        {
            "healthy": is_healthy,
            "issues": issues,
            "python_version": sys.version.split()[0],
            "platform": platform,
        }
    )


async def restore_from_backup(backup_id: str) -> ToolResult:
    """Restore system from a backup (placeholder for actual implementation).
    
    Args:
        backup_id: ID of the backup to restore
        
    Returns:
        ToolResult with restoration status
    """
    # This would need actual restoration logic
    # For now, it validates the backup exists
    manager = get_rollback_manager()
    point = manager._rollback_points.get(backup_id)
    
    if not point:
        return ToolResult.fail(
            "Backup not found",
            f"No backup with ID: {backup_id}"
        )
    
    return ToolResult.ok(
        f"Restore prepared from backup: {backup_id}",
        {
            "backup_id": backup_id,
            "version": point.version,
            "backup_timestamp": point.timestamp.isoformat(),
            "note": "Restore operation prepared - actual restoration requires additional steps",
        }
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(
    create_backup,
    permission=PermissionLevel.NOTIFY,  # Non-destructive, just creates marker
    category="recovery",
    tags=["backup", "checkpoint", "recovery"]
)

registry.register(
    initiate_rollback,
    permission=PermissionLevel.STRICT,  # Destructive operation
    category="recovery",
    tags=["rollback", "recovery", "destructive"]
)

registry.register(
    get_recovery_status,
    permission=PermissionLevel.AUTO,
    category="recovery",
    tags=["status", "health", "check"]
)

registry.register(
    list_backup_points,
    permission=PermissionLevel.AUTO,
    category="recovery",
    tags=["backup", "list"]
)

registry.register(
    run_health_check,
    permission=PermissionLevel.AUTO,
    category="recovery",
    tags=["health", "check", "status"]
)

registry.register(
    restore_from_backup,
    permission=PermissionLevel.STRICT,  # Destructive operation
    category="recovery",
    tags=["restore", "backup", "destructive"]
)