"""Cross-app workflow tools (spec §5).

Tools for creating, managing, and executing cross-app workflows.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.workflow_automation import get_workflow_engine


def _user_id() -> str:
    return get_tool_context().user_id


# ============================================================================
# Workflow Management Tools
# ============================================================================

async def list_workflow_patterns() -> ToolResult:
    """List all workflow patterns for the user.
    
    Returns:
        ToolResult with list of workflow patterns
    """
    engine = get_workflow_engine()
    
    patterns = engine.list_patterns(_user_id())
    
    return ToolResult.ok(
        f"Found {len(patterns)} workflow patterns",
        {"patterns": patterns, "total": len(patterns)}
    )


async def create_workflow_pattern(name: str, steps: list[dict],
                                  description: str = "") -> ToolResult:
    """Create a new workflow pattern.
    
    Args:
        name: Workflow name
        steps: List of workflow steps
        description: Workflow description
        
    Returns:
        ToolResult with created pattern info
    """
    engine = get_workflow_engine()
    
    pattern = engine.create_pattern(_user_id(), name, steps, description)
    
    return ToolResult.ok(
        f"Created workflow: {name}",
        pattern
    )


async def execute_workflow(pattern_id: str, input_data: dict | None = None) -> ToolResult:
    """Request execution of a saved workflow inside the active central run.

    Args:
        pattern_id: ID of the workflow pattern
        input_data: Input data for the workflow
        
    Returns:
        A marker consumed by the central graph, which expands the saved steps
        into ordinary policy-gated capability calls.
    """
    try:
        context = get_tool_context()
    except RuntimeError as exc:
        return ToolResult.fail(
            "CANONICAL_WORKFLOW_CONTEXT_REQUIRED",
            str(exc),
            message="Start the workflow through Jalora's central assistant.",
        )
    if not context.task_id or not context.run_id:
        return ToolResult.fail(
            "CANONICAL_WORKFLOW_CONTEXT_REQUIRED",
            "A saved workflow can only execute within a canonical task and run.",
            message="Start the workflow through Jalora's central assistant.",
        )
    engine = get_workflow_engine()
    try:
        execution_id = engine.prepare_canonical_execution(
            pattern_id,
            _user_id(),
            input_data,
            task_id=context.task_id,
            run_id=context.run_id,
        )
    except ValueError as exc:
        return ToolResult.fail(
            "INVALID_SAVED_WORKFLOW",
            str(exc),
            message="The saved workflow could not be started.",
        )
    return ToolResult.ok(
        "Saved workflow accepted by the central task executor.",
        {"canonical_workflow_execution_id": execution_id},
    )


async def get_execution_status(execution_id: str) -> ToolResult:
    """Get status of a workflow execution.
    
    Args:
        execution_id: Execution ID
        
    Returns:
        ToolResult with execution status
    """
    engine = get_workflow_engine()
    
    status = engine.get_execution_status(execution_id, _user_id())

    if status.get("status") == "not_found":
        return ToolResult.fail(
            "WORKFLOW_EXECUTION_NOT_FOUND",
            "The execution does not exist or is not owned by this user.",
            message="Workflow execution not found.",
        )
    return ToolResult.ok(
        f"Execution status: {status.get('status', 'unknown')}",
        status
    )


# ============================================================================
# Tool Registration
# ============================================================================

registry.register(list_workflow_patterns, permission=PermissionLevel.AUTO,
                  category="workflows", tags=["workflow", "management"])
registry.register(create_workflow_pattern, permission=PermissionLevel.CONFIRM,
                  category="workflows", tags=["workflow", "creation"])
registry.register(execute_workflow, permission=PermissionLevel.CONFIRM,
                  category="workflows", tags=["execution", "automation"])
registry.register(get_execution_status, permission=PermissionLevel.AUTO,
                  category="workflows", tags=["status", "monitoring"])