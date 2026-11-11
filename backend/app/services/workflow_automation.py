"""Cross-app workflow automation (spec §5).

Provides workflow orchestration and execution across multiple tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.agent import AgentRun
from app.models.task import Task, TaskStep
from app.models.workflows import (
    CrossAppWorkflow,
    WorkflowExecutionRecord,
    WorkflowStepResult,
)
from app.models.user import User
from app.services.policy import normalize_autonomy, normalize_tool_policies
from app.services.tool_execution import invoke_registered_tool
from app.services.tool_preflight import validate_tool_arguments

logger = logging.getLogger("jalora.workflows")


def _elapsed_seconds(started_at: datetime, completed_at: datetime) -> float:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    return max(0.0, (completed_at - started_at).total_seconds())


@dataclass
class WorkflowStep:
    """A step in a workflow pattern."""
    order: int
    name: str
    tool: str
    args: dict
    conditions: list[str] = field(default_factory=list)
    next_step: str | None = None


@dataclass
class WorkflowExecutionResult:
    """Result of executing a workflow."""
    success: bool
    pattern_name: str
    steps_completed: int
    steps_total: int
    output_data: dict
    error: str | None = None
    status: str = "completed"
    execution_id: str | None = None


class WorkflowEngine:
    """Engine for orchestrating cross-app workflows."""
    
    def __init__(self):
        self.timeout_seconds = 60

    def prepare_canonical_execution(
        self,
        pattern_id: str,
        user_id: str,
        input_data: dict[str, Any] | None,
        *,
        task_id: str,
        run_id: str,
    ) -> str:
        """Create a history record linked to the active central task/run."""
        db = SessionLocal()
        try:
            task = db.get(Task, task_id)
            run = db.get(AgentRun, run_id)
            if (
                task is None
                or run is None
                or task.user_id != user_id
                or run.user_id != user_id
                or task.run_id != run.id
                or run.task_id != task.id
            ):
                raise ValueError(
                    "Saved workflows must be started by a valid canonical task/run."
                )
            if input_data is not None and not isinstance(input_data, dict):
                raise ValueError("Workflow input data must be an object.")

            pattern = db.scalar(
                select(CrossAppWorkflow).where(
                    CrossAppWorkflow.id == pattern_id,
                    CrossAppWorkflow.user_id == user_id,
                    CrossAppWorkflow.is_active.is_(True),
                )
            )
            if pattern is None:
                raise ValueError("Workflow pattern not found.")
            self._validated_steps(pattern.steps)

            execution = WorkflowExecutionRecord(
                user_id=user_id,
                pattern_id=pattern.id,
                pattern_name=pattern.name,
                task_id=task.id,
                run_id=run.id,
                input_data=input_data or {},
                status="running",
                triggered_by="central_agent",
                started_at=datetime.now(timezone.utc),
            )
            pattern.execution_count += 1
            pattern.last_executed = execution.started_at
            db.add(execution)
            db.commit()
            db.refresh(execution)
            return execution.id
        finally:
            db.close()

    @staticmethod
    def _validated_steps(steps: Any) -> list[dict[str, Any]]:
        from app.tools.base import registry

        if not isinstance(steps, list) or not steps:
            raise ValueError("A saved workflow must contain at least one step.")
        if len(steps) > 50:
            raise ValueError("A saved workflow cannot contain more than 50 steps.")

        validated: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        seen_orders: set[int] = set()
        for index, value in enumerate(steps, start=1):
            if not isinstance(value, dict):
                raise ValueError(f"Workflow step {index} must be an object.")
            tool_name = value.get("tool")
            args = value.get("args", {})
            order = value.get("order", index)
            name = value.get("name") or f"step_{index}"
            if not isinstance(tool_name, str) or registry.get(tool_name.strip()) is None:
                raise ValueError(
                    f"Workflow step {index} references an unavailable capability."
                )
            if not isinstance(args, dict):
                raise ValueError(f"Workflow step {index} arguments must be an object.")
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                raise ValueError(f"Workflow step {index} has an invalid order.")
            if order in seen_orders:
                raise ValueError(f"Workflow order {order} is duplicated.")
            if not isinstance(name, str) or not name.strip() or name in seen_names:
                raise ValueError(f"Workflow step {index} has an invalid or duplicate name.")
            if value.get("conditions") or value.get("next_step"):
                raise ValueError(
                    "Saved workflow branching and conditions are not supported."
                )
            seen_orders.add(order)
            seen_names.add(name)
            validated.append({
                **value,
                "order": order,
                "name": name,
                "tool": tool_name.strip(),
                "args": args,
            })
        return sorted(validated, key=lambda item: item["order"])

    def get_canonical_execution(self, execution_id: str, user_id: str) -> dict[str, Any]:
        """Return the validated saved definition for graph-level expansion."""
        db = SessionLocal()
        try:
            execution = db.scalar(
                select(WorkflowExecutionRecord).where(
                    WorkflowExecutionRecord.id == execution_id,
                    WorkflowExecutionRecord.user_id == user_id,
                )
            )
            if execution is None or not execution.task_id or not execution.run_id:
                raise ValueError("Canonical workflow execution was not found.")
            pattern = db.get(CrossAppWorkflow, execution.pattern_id)
            if pattern is None:
                raise ValueError("Saved workflow definition was removed.")
            return {
                "execution_id": execution.id,
                "pattern_id": pattern.id,
                "pattern_name": execution.pattern_name,
                "task_id": execution.task_id,
                "run_id": execution.run_id,
                "input_data": execution.input_data or {},
                "steps": self._validated_steps(pattern.steps),
            }
        finally:
            db.close()

    def record_canonical_step(
        self,
        *,
        execution_id: str,
        step_order: int,
        step_name: str,
        task_step_id: str | None,
        tool_name: str,
        tool_args: dict[str, Any],
        result: ToolResult,
        duration_seconds: float,
        is_last_step: bool,
    ) -> None:
        """Mirror one graph-executed step into the backward-compatible history."""
        from app.services.audit import redact

        db = SessionLocal()
        try:
            execution = db.get(WorkflowExecutionRecord, execution_id)
            if execution is None:
                raise ValueError("Workflow execution history row is missing.")
            step = db.scalar(
                select(WorkflowStepResult).where(
                    WorkflowStepResult.execution_id == execution_id,
                    WorkflowStepResult.step_order == step_order,
                )
            )
            payload = {
                "success": result.success,
                "message": result.message,
                "data": result.data or {},
                "error": result.error.model_dump() if result.error else None,
            }
            if step is None:
                task_step = db.get(TaskStep, task_step_id) if task_step_id else None
                started_at = (
                    task_step.started_at
                    if task_step is not None and task_step.started_at is not None
                    else datetime.now(timezone.utc)
                )
                step = WorkflowStepResult(
                    execution_id=execution_id,
                    step_order=step_order,
                    step_name=step_name,
                    tool_name=tool_name,
                    tool_args=redact(tool_args),
                    started_at=started_at,
                )
                db.add(step)
            step.step_name = step_name
            step.task_step_id = task_step_id
            step.tool_name = tool_name
            step.tool_args = redact(tool_args)
            step.success = result.success
            step.result_data = redact(payload)
            step.error_message = result.error.details if result.error else ""
            task_step = db.get(TaskStep, task_step_id) if task_step_id else None
            if task_step is not None:
                task_step.result_data = redact(payload)
                if not result.success:
                    error_code = result.error.code if result.error else "WORKFLOW_STEP_FAILED"
                    task_step.status = "failed"
                    task_step.outcome_status = (
                        "outcome_unknown"
                        if error_code in {"TIMEOUT", "TOOL_EXCEPTION", "OUTCOME_UNKNOWN"}
                        else "failed"
                    )
                    task_step.error_code = error_code
                    task_step.detail = (
                        result.error.details if result.error else result.message
                    )
            step.completed_at = (
                task_step.completed_at
                if task_step is not None and task_step.completed_at is not None
                else datetime.now(timezone.utc)
            )
            step.duration_seconds = (
                duration_seconds
                if duration_seconds > 0
                else _elapsed_seconds(step.started_at, step.completed_at)
            )
            execution.current_step = max(execution.current_step, step_order)
            results = dict(execution.intermediate_results or {})
            results[step.step_name] = redact(payload)
            execution.intermediate_results = results
            execution.output_data = results

            if result.success:
                skipped = bool((result.data or {}).get("skipped"))
                if is_last_step:
                    execution.status = "completed"
                    execution.completed_at = datetime.now(timezone.utc)
                    execution.duration_seconds = max(
                        0.0,
                        _elapsed_seconds(execution.started_at, execution.completed_at),
                    )
                elif skipped:
                    execution.status = "running"
            else:
                error_code = result.error.code if result.error else ""
                execution.status = (
                    "outcome_unknown"
                    if error_code in {"TIMEOUT", "TOOL_EXCEPTION", "OUTCOME_UNKNOWN"}
                    else "failed"
                )
                execution.error_step = step_order
                execution.error_message = (
                    result.error.details if result.error else result.message
                )
                execution.completed_at = datetime.now(timezone.utc)
                execution.duration_seconds = max(
                    0.0,
                    _elapsed_seconds(execution.started_at, execution.completed_at),
                )
            db.commit()
        finally:
            db.close()

    def set_canonical_execution_status(
        self,
        execution_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        db = SessionLocal()
        try:
            execution = db.get(WorkflowExecutionRecord, execution_id)
            if execution is None:
                raise ValueError("Workflow execution history row is missing.")
            execution.status = status
            if error:
                execution.error_message = error
            if status in {"completed", "failed", "outcome_unknown", "cancelled"}:
                execution.completed_at = datetime.now(timezone.utc)
                execution.duration_seconds = max(
                    0.0,
                    _elapsed_seconds(execution.started_at, execution.completed_at),
                )
            db.commit()
        finally:
            db.close()
    
    def list_patterns(self, user_id: str) -> list[dict]:
        """List user's workflow patterns."""
        db = SessionLocal()
        try:
            patterns = db.scalars(
                select(CrossAppWorkflow).where(
                    CrossAppWorkflow.user_id == user_id,
                    CrossAppWorkflow.is_active.is_(True),
                )
            ).all()
            
            return [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "steps_count": len(p.steps),
                    "last_executed": p.last_executed.isoformat() if p.last_executed else None
                }
                for p in patterns
            ]
        finally:
            db.close()
    
    def create_pattern(self, user_id: str, name: str, steps: list[dict],
                      description: str = "") -> dict:
        """Create a new workflow pattern."""
        from app.tools.base import registry

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Workflow name is required.")
        if not isinstance(steps, list) or not steps:
            raise ValueError("A workflow must contain at least one step.")
        seen_names: set[str] = set()
        seen_orders: set[int] = set()
        normalized_steps: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Workflow step {index} must be an object.")
            if not isinstance(step.get("tool"), str) or not step["tool"].strip():
                raise ValueError(f"Workflow step {index} needs a registered tool name.")
            tool_name = step["tool"].strip()
            if registry.get(tool_name) is None:
                raise ValueError(
                    f"Workflow step {index} references an unregistered capability "
                    f"'{tool_name}'."
                )
            if not isinstance(step.get("args", {}), dict):
                raise ValueError(f"Workflow step {index} arguments must be an object.")
            step_name = step.get("name")
            if step_name is not None:
                if not isinstance(step_name, str) or not step_name.strip():
                    raise ValueError(f"Workflow step {index} has an invalid name.")
                if step_name in seen_names:
                    raise ValueError(f"Workflow step name '{step_name}' is duplicated.")
                seen_names.add(step_name)
            order = step.get("order", index)
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                raise ValueError(f"Workflow step {index} needs a positive integer order.")
            if order in seen_orders:
                raise ValueError(f"Workflow step order {order} is duplicated.")
            seen_orders.add(order)
            if step.get("conditions"):
                raise ValueError(
                    f"Workflow step {index} conditions are not supported; "
                    "create a linear workflow without conditions."
                )
            if step.get("next_step"):
                raise ValueError(
                    f"Workflow step {index} custom branching is not supported; "
                    "use the ordered step sequence."
                )
            normalized_steps.append({**step, "tool": tool_name})

        db = SessionLocal()
        try:
            pattern = CrossAppWorkflow(
                user_id=user_id,
                name=name,
                description=description,
                steps=normalized_steps
            )
            db.add(pattern)
            db.commit()
            db.refresh(pattern)
            
            return {
                "id": pattern.id,
                "name": pattern.name,
                "created_at": pattern.created_at.isoformat()
            }
        finally:
            db.close()
    
    async def execute_workflow(
        self,
        pattern_id: str,
        user_id: str,
        input_data: dict | None = None,
    ) -> WorkflowExecutionResult:
        """Reject legacy direct execution; saved steps run only inside AgentState."""
        raise RuntimeError(
            "Saved workflows must be expanded and executed by the central agent graph."
        )

        """Legacy executor retained temporarily for source compatibility."""
        db = SessionLocal()
        try:
            pattern = db.scalar(
                select(CrossAppWorkflow).where(
                    CrossAppWorkflow.id == pattern_id,
                    CrossAppWorkflow.user_id == user_id,
                    CrossAppWorkflow.is_active.is_(True),
                )
            )

            if not pattern:
                return WorkflowExecutionResult(
                    success=False,
                    pattern_name="",
                    steps_completed=0,
                    steps_total=0,
                    output_data={},
                    error="Pattern not found",
                    status="not_found",
                )

            steps = sorted(
                pattern.steps or [],
                key=lambda step: step.get("order", 0) if isinstance(step, dict) else 0,
            )
            execution = WorkflowExecutionRecord(
                user_id=user_id,
                pattern_id=pattern_id,
                pattern_name=pattern.name,
                input_data=input_data or {},
                started_at=datetime.now(timezone.utc)
            )
            workflow_started = time.perf_counter()
            db.add(execution)
            db.commit()
            db.refresh(execution)

            results = {}
            steps_completed = 0
            status = "completed"
            error = None
            was_cancelled = False
            user = db.get(User, user_id)
            preferences = (user.prefs or {}) if user else {}
            autonomy = normalize_autonomy(preferences.get("autonomy"))
            tool_policies = normalize_tool_policies(preferences.get("tool_policies"))

            for step_index, step_data in enumerate(steps):
                if not isinstance(step_data, dict):
                    status = "failed"
                    error = f"Workflow step {step_index + 1} must be an object."
                    break

                step_order = int(step_data.get("order", step_index + 1))
                step_name = str(step_data.get("name") or f"step_{step_order}")
                tool_name = str(step_data.get("tool") or "")
                raw_args = step_data.get("args", {})
                step_started = datetime.now(timezone.utc)

                try:
                    context = {"input": input_data or {}, "steps": results}
                    tool_args = self._resolve_templates(raw_args, context)
                    tool_result = await self._execute_step(
                        tool_name,
                        tool_args,
                        autonomy=autonomy,
                        tool_policies=tool_policies,
                    )
                except asyncio.CancelledError:
                    was_cancelled = True
                    tool_args = raw_args if isinstance(raw_args, dict) else {}
                    tool_result = ToolResult.fail(
                        "OUTCOME_UNKNOWN",
                        "Workflow execution was cancelled while a capability was in flight.",
                        message=(
                            f"Workflow step '{step_name}' was interrupted. "
                            "Check its status before retrying."
                        ),
                    )
                except ValueError as exc:
                    tool_args = raw_args if isinstance(raw_args, dict) else {}
                    tool_result = ToolResult.fail(
                        "INVALID_WORKFLOW_INPUT", str(exc),
                        message="A workflow input or step result needed by this step is missing.",
                    )
                except Exception as e:
                    logger.exception("Workflow step %s failed unexpectedly", step_name)
                    tool_args = raw_args if isinstance(raw_args, dict) else {}
                    tool_result = ToolResult.fail(
                        "WORKFLOW_STEP_EXCEPTION",
                        f"{type(e).__name__}: {e}",
                        message=f"Workflow step '{step_name}' failed unexpectedly.",
                    )

                completed_at = datetime.now(timezone.utc)
                step_data_result = {
                    "success": tool_result.success,
                    "message": tool_result.message,
                    "data": tool_result.data or {},
                    "error": (
                        tool_result.error.model_dump()
                        if tool_result.error is not None else None
                    ),
                }
                results[step_name] = step_data_result
                step_result = WorkflowStepResult(
                    execution_id=execution.id,
                    step_order=step_order,
                    step_name=step_name,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    success=tool_result.success,
                    result_data=step_data_result,
                    error_message=(
                        tool_result.error.details if tool_result.error else ""
                    ),
                    started_at=step_started,
                    completed_at=completed_at,
                    duration_seconds=(completed_at - step_started).total_seconds(),
                )
                db.add(step_result)
                execution.current_step = step_index + 1
                execution.intermediate_results = results
                db.commit()

                if tool_result.success:
                    steps_completed += 1
                    continue

                error = tool_result.error.details if tool_result.error else tool_result.message
                error_code = tool_result.error.code if tool_result.error else None
                if error_code == "APPROVAL_REQUIRED":
                    status = "approval_required"
                elif error_code in {"TIMEOUT", "TOOL_EXCEPTION", "OUTCOME_UNKNOWN"}:
                    status = "outcome_unknown"
                else:
                    status = "failed"
                execution.error_step = step_order
                execution.error_message = error
                break

            execution.status = status
            execution.output_data = results
            execution.completed_at = datetime.now(timezone.utc)
            execution.duration_seconds = time.perf_counter() - workflow_started
            pattern.execution_count += 1
            pattern.last_executed = execution.completed_at
            db.commit()

            if was_cancelled:
                raise asyncio.CancelledError

            return WorkflowExecutionResult(
                success=status == "completed",
                pattern_name=pattern.name,
                steps_completed=steps_completed,
                steps_total=len(steps),
                output_data=results,
                error=error,
                status=status,
                execution_id=execution.id,
            )
        finally:
            db.close()

    async def _execute_step(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        autonomy: int = 2,
        tool_policies: dict[str, str] | None = None,
    ) -> ToolResult:
        """Reject legacy execution outside the central graph."""
        raise RuntimeError(
            "Saved workflow capabilities must execute through the central agent graph."
        )

        """Legacy helper retained temporarily for source compatibility."""
        from app.tools.base import PermissionLevel, registry
        from app.services.policy import decide

        spec = registry.get(tool_name)
        if spec is None:
            return ToolResult.fail(
                "UNKNOWN_TOOL", f"'{tool_name}' is not registered.",
                message=f"Workflow capability '{tool_name}' is unavailable.",
            )
        if not isinstance(args, dict):
            return ToolResult.fail(
                "INVALID_TOOL_ARGUMENTS", "Workflow arguments must be an object.",
                message="The workflow step has invalid arguments.",
            )
        if spec.permission >= PermissionLevel.CONFIRM:
            return ToolResult.fail(
                "APPROVAL_REQUIRED",
                f"Capability '{tool_name}' requires a separate explicit approval.",
                message=(
                    f"Workflow paused before '{tool_name}'. Run this action through "
                    "the assistant so its arguments can be reviewed and approved."
                ),
                data={"tool": tool_name, "permission": int(spec.permission)},
            )

        decision = decide(
            permission=spec.permission,
            autonomy=autonomy,
            policy=(tool_policies or {}).get(tool_name, ""),
            tags=spec.tags,
        )
        if not decision.allowed:
            return ToolResult.fail(
                decision.code,
                decision.detail,
                message=decision.detail,
                data={"tool": tool_name},
            )
        if decision.require_approval:
            return ToolResult.fail(
                "APPROVAL_REQUIRED",
                f"Capability '{tool_name}' requires explicit user approval.",
                message=(
                    f"Workflow paused before '{tool_name}'. Run this action through "
                    "the assistant so its arguments can be reviewed and approved."
                ),
                data={"tool": tool_name, "permission": int(spec.permission)},
            )

        args, validation_errors = validate_tool_arguments(spec, args)
        if validation_errors is not None:
            return ToolResult.fail(
                "INVALID_TOOL_ARGUMENTS",
                "Workflow arguments do not match the capability input schema.",
                message=f"Correct the arguments for '{tool_name}' before retrying.",
                data={"errors": validation_errors},
            )

        return await invoke_registered_tool(
            spec,
            args,
            timeout_seconds=self.timeout_seconds,
            label="Workflow capability",
        )

    @classmethod
    def _resolve_templates(cls, value: Any, context: dict[str, Any]) -> Any:
        """Resolve {{input.path}} and {{steps.name.data.path}} references."""
        if isinstance(value, dict):
            return {key: cls._resolve_templates(item, context) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._resolve_templates(item, context) for item in value]
        if not isinstance(value, str):
            return value

        def lookup(path: str) -> Any:
            current: Any = context
            for part in path.strip().split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                elif isinstance(current, list) and part.isdecimal():
                    index = int(part)
                    if index >= len(current):
                        raise ValueError(f"Workflow reference '{path}' is unavailable.")
                    current = current[index]
                else:
                    raise ValueError(f"Workflow reference '{path}' is unavailable.")
            return current

        match = re.fullmatch(r"\{\{\s*([^{}]+?)\s*\}\}", value)
        if match:
            return lookup(match.group(1))

        def replace(match: re.Match[str]) -> str:
            resolved = lookup(match.group(1))
            if isinstance(resolved, (dict, list)):
                return json.dumps(resolved, ensure_ascii=False)
            return str(resolved)

        return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", replace, value)

    def get_execution_status(self, execution_id: str, user_id: str) -> dict:
        """Get status of a workflow execution."""
        db = SessionLocal()
        try:
            execution = db.scalar(
                select(WorkflowExecutionRecord).where(
                    WorkflowExecutionRecord.id == execution_id,
                    WorkflowExecutionRecord.user_id == user_id,
                )
            )
            
            if not execution:
                return {"status": "not_found", "message": "Execution not found"}
            
            step_results = db.scalars(
                select(WorkflowStepResult)
                .where(WorkflowStepResult.execution_id == execution_id)
                .order_by(WorkflowStepResult.step_order)
            ).all()
            pattern = db.get(CrossAppWorkflow, execution.pattern_id)
            canonical_steps = (
                db.scalars(
                    select(TaskStep)
                    .where(
                        TaskStep.task_id == execution.task_id,
                        TaskStep.plan_step_id.like(
                            f"workflow:{execution.id}:%"
                        ),
                    )
                    .order_by(TaskStep.idx)
                ).all()
                if execution.task_id
                else []
            )
            task = db.get(Task, execution.task_id) if execution.task_id else None

            return {
                "execution_id": execution.id,
                "pattern_name": execution.pattern_name,
                "status": execution.status,
                "task_id": execution.task_id,
                "run_id": execution.run_id,
                "task_status": task.status if task is not None else None,
                "current_step": execution.current_step,
                "total_steps": len(pattern.steps or []) if pattern else len(step_results),
                "duration_seconds": execution.duration_seconds,
                "error": execution.error_message or None,
                "steps": [
                    {
                        "name": s.description.rsplit(": ", 1)[-1],
                        "tool": s.tool_id,
                        "task_step_id": s.id,
                        "success": s.status in {"done", "skipped"},
                        "status": s.status,
                        "outcome_status": s.outcome_status,
                        "approval_id": s.approval_id,
                        "duration": (
                            _elapsed_seconds(s.started_at, s.completed_at)
                            if s.started_at and s.completed_at else None
                        ),
                        "error_code": s.error_code,
                        "error": s.detail if s.status == "failed" else None,
                    }
                    for s in canonical_steps
                ] if canonical_steps else [
                    {
                        "name": s.step_name,
                        "tool": s.tool_name,
                        "success": s.success,
                        "duration": s.duration_seconds,
                        "error_code": (
                            s.result_data.get("error", {}).get("code")
                            if not s.success and isinstance(s.result_data, dict)
                            else None
                        ),
                        "error": s.error_message if not s.success else None
                    }
                    for s in step_results
                ]
            }
        finally:
            db.close()


_workflow_engine: WorkflowEngine | None = None

def get_workflow_engine() -> WorkflowEngine:
    """Get or create the workflow engine singleton."""
    global _workflow_engine
    if _workflow_engine is None:
        _workflow_engine = WorkflowEngine()
    return _workflow_engine