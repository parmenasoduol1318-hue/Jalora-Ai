"""Persistence helpers for the canonical chat execution lifecycle."""
from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.agent import AgentRun
from app.models.task import Task, TaskStep, TaskStepAttempt
from app.models.user import new_id, utcnow
from app.services.execution_contract import (
    ActionOutcome,
    AttemptState,
    ExecutionError,
    ExecutionResult,
    StepLifecycle,
    VerificationStatus,
)


@dataclass(frozen=True)
class AttemptHandle:
    attempt_id: str | None
    step_id: str
    action_id: str
    attempt_number: int
    idempotency_key: str
    cached_raw: str | None = None
    blocked_code: str | None = None


def _payload_digest(tool_name: str, arguments: dict[str, Any]) -> str:
    from app.services.audit import redact

    encoded = json.dumps(
        {"tool": tool_name, "arguments": redact(arguments)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def begin_tool_attempt(
    *,
    task_id: str,
    run_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    plan_step_index: int | None,
    side_effect: str,
) -> AttemptHandle:
    """Durably claim a tool action before dispatch; uncertain actions are not replayed."""
    digest = _payload_digest(tool_name, arguments)
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        run = db.get(AgentRun, run_id)
        if task is None or run is None or task.user_id != run.user_id:
            raise RuntimeError("Canonical execution task/run linkage is missing or invalid.")
        task.status = "running"

        step: TaskStep | None = None
        if plan_step_index is not None:
            step = db.scalar(
                select(TaskStep).where(
                    TaskStep.task_id == task_id,
                    TaskStep.idx == plan_step_index,
                )
            )
        else:
            previous_attempt = db.scalar(
                select(TaskStepAttempt)
                .where(
                    TaskStepAttempt.task_id == task_id,
                    TaskStepAttempt.run_id == run_id,
                    TaskStepAttempt.tool_call_id == tool_call_id,
                )
                .order_by(TaskStepAttempt.attempt_number.desc())
            )
            if previous_attempt is not None:
                step = db.get(TaskStep, previous_attempt.step_id)
            if step is None:
                step = next(
                    (
                        candidate
                        for candidate in db.scalars(
                            select(TaskStep)
                            .where(
                                TaskStep.task_id == task_id,
                                TaskStep.plan_step_id.is_(None),
                                TaskStep.tool_id == tool_name,
                            )
                            .order_by(TaskStep.idx.desc())
                        ).all()
                        if candidate.arguments == arguments
                        and candidate.outcome_status == "awaiting_approval"
                    ),
                    None,
                )
        if step is None:
            next_index = (
                db.scalar(
                    select(TaskStep.idx)
                    .where(TaskStep.task_id == task_id)
                    .order_by(TaskStep.idx.desc())
                    .limit(1)
                )
            )
            step = TaskStep(
                task_id=task_id,
                idx=(next_index + 1) if next_index is not None else 0,
                description=f"Execute {tool_name}",
                status="active",
                tool_id=tool_name,
                arguments=arguments,
                outcome_status=ActionOutcome.PREPARED.value,
            )
            db.add(step)
            db.flush()

        if step.tool_id and step.tool_id != tool_name:
            raise RuntimeError("Planned step capability does not match the selected tool.")
        from app.services.audit import redact

        safe_arguments = redact(arguments)
        if step.arguments and step.arguments != safe_arguments:
            raise RuntimeError("Planned step arguments changed after execution was selected.")
        step.tool_id = tool_name
        step.capability = tool_name
        step.policy_decision = "allow"
        step.policy_version = "v1"
        step.arguments = safe_arguments
        step.status = "active"
        step.started_at = step.started_at or utcnow()
        step.action_id = step.action_id or new_id()
        step.attempt_count = step.attempt_count or 0

        prior = db.scalars(
            select(TaskStepAttempt)
            .where(TaskStepAttempt.step_id == step.id)
            .order_by(TaskStepAttempt.attempt_number.desc())
        ).all()
        same_payload = [item for item in prior if item.payload_digest == digest]
        previous = same_payload[0] if same_payload else None
        if previous is not None:
            if previous.state == AttemptState.COMPLETED.value:
                cached = (previous.result_data or {}).get("raw")
                db.commit()
                return AttemptHandle(
                    attempt_id=previous.id,
                    step_id=step.id,
                    action_id=step.action_id,
                    attempt_number=previous.attempt_number,
                    idempotency_key=previous.provider_idempotency_key or "",
                    cached_raw=cached if isinstance(cached, str) else None,
                )
            if previous.state in {
                AttemptState.DISPATCHING.value,
                AttemptState.RECONCILING.value,
                AttemptState.OUTCOME_UNKNOWN.value,
            }:
                previous.state = AttemptState.OUTCOME_UNKNOWN.value
                previous.outcome_status = ActionOutcome.OUTCOME_UNKNOWN.value
                previous.finished_at = utcnow()
                step.outcome_status = ActionOutcome.OUTCOME_UNKNOWN.value
                step.verification_status = "unavailable"
                step.completed_at = utcnow()
                db.commit()
                return AttemptHandle(
                    attempt_id=previous.id,
                    step_id=step.id,
                    action_id=step.action_id,
                    attempt_number=previous.attempt_number,
                    idempotency_key=previous.provider_idempotency_key or "",
                    blocked_code="OUTCOME_UNKNOWN",
                )
        attempt_number = step.attempt_count + 1
        idempotency_key = hashlib.sha256(
            f"{task_id}:{step.id}:{digest}".encode("utf-8")
        ).hexdigest()
        attempt = TaskStepAttempt(
            task_id=task_id,
            step_id=step.id,
            run_id=run_id,
            attempt_number=attempt_number,
            tool_call_id=tool_call_id,
            state=AttemptState.DISPATCHING.value,
            payload_digest=digest,
            provider_idempotency_key=idempotency_key,
        )
        step.attempt_count = attempt_number
        step.idempotency_key = idempotency_key
        step.outcome_status = ActionOutcome.SUBMITTED.value
        db.add(attempt)
        db.commit()
        return AttemptHandle(
            attempt_id=attempt.id,
            step_id=step.id,
            action_id=step.action_id,
            attempt_number=attempt_number,
            idempotency_key=idempotency_key,
        )


def finish_tool_attempt(
    *,
    handle: AttemptHandle,
    task_id: str,
    run_id: str,
    raw: Any,
    tool_name: str,
    success: bool,
    error_code: str | None = None,
    outcome: ActionOutcome | None = None,
) -> ExecutionResult | None:
    """Persist the typed result and terminal attempt state after tool execution."""
    if handle.attempt_id is None:
        return None
    normalized_raw = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    from app.services.audit import redact
    from app.services.tool_execution import decode_tool_result

    decoded = decode_tool_result(normalized_raw)
    result_data = decoded.data if decoded is not None else {}
    if not isinstance(result_data, dict):
        result_data = {"value": result_data}
    actual_outcome = outcome or (
        ActionOutcome.SUCCEEDED if success else ActionOutcome.FAILED
    )
    pending_device_submission = (
        success
        and tool_name in {
            "request_remote_android_action",
            "request_remote_windows_action",
            "request_remote_url",
        }
        and str(result_data.get("protocol_status") or "").lower()
        in {"queued", "delivered", "running"}
    )
    if pending_device_submission:
        actual_outcome = ActionOutcome.SUBMITTED
    if success and outcome is None:
        provider_status = str(result_data.get("status") or "").lower()
        if pending_device_submission:
            pass
        elif provider_status == "delivered":
            actual_outcome = ActionOutcome.DELIVERED
        elif provider_status == "verified":
            actual_outcome = ActionOutcome.VERIFIED
        elif result_data.get("provider") and provider_status in {
            "accepted", "queued", "sent", "submitted",
        }:
            actual_outcome = ActionOutcome.PROVIDER_ACCEPTED
    with SessionLocal() as db:
        attempt = db.get(TaskStepAttempt, handle.attempt_id)
        step = db.get(TaskStep, handle.step_id)
        if attempt is None or step is None:
            raise RuntimeError("Canonical execution attempt disappeared before completion.")
        attempt.state = (
            AttemptState.COMPLETED.value
            if success
            else (
                AttemptState.OUTCOME_UNKNOWN.value
                if actual_outcome == ActionOutcome.OUTCOME_UNKNOWN
                else AttemptState.FAILED.value
            )
        )
        attempt.outcome_status = actual_outcome.value
        attempt.error_code = error_code
        attempt.finished_at = utcnow()
        step.outcome_status = actual_outcome.value
        step.error_code = error_code
        step.status = (
            "active" if pending_device_submission
            else "done" if success
            else "failed"
        )
        step.completed_at = None if pending_device_submission else utcnow()
        verification_status = (
            "pending"
            if pending_device_submission
            else "verified"
            if actual_outcome in {ActionOutcome.DELIVERED, ActionOutcome.VERIFIED}
            else (
                "pending"
                if success
                else (
                    "unavailable"
                    if actual_outcome == ActionOutcome.OUTCOME_UNKNOWN
                    else "failed"
                )
            )
        )
        step.verification_status = verification_status
        provider_receipt = {
            key: result_data[key]
            for key in ("provider", "message_id", "thread_id", "status")
            if isinstance(result_data.get(key), str)
        }
        typed_result = ExecutionResult(
            task_id=task_id,
            run_id=run_id,
            step_id=step.id,
            action_id=handle.action_id,
            approval_id=step.approval_id,
            attempt=handle.attempt_number,
            lifecycle=(
                StepLifecycle.RUNNING
                if pending_device_submission
                else StepLifecycle.COMPLETED if success
                else StepLifecycle.FAILED
            ),
            outcome=actual_outcome,
            verification=(
                verification_status
            ),
            error=ExecutionError(code=error_code) if error_code else None,
            output=redact(result_data),
            evidence=(
                result_data["evidence"]
                if isinstance(result_data.get("evidence"), list)
                else []
            ),
            provider_receipt=provider_receipt or None,
            safe_summary=(
                str(redact(decoded.message or ""))[:2000]
                if decoded is not None
                else ""
            ),
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
        )
        persisted_result = {
            "raw": normalized_raw,
            "execution_result": typed_result.model_dump(mode="json"),
        }
        attempt.result_data = persisted_result
        step.result_data = persisted_result
        step.completion_outcomes = [actual_outcome.value]
        db.commit()
        return typed_result


def reconcile_unknown_attempt_as_provider_accepted(
    *,
    db: Session | None = None,
    task_id: str,
    run_id: str,
    step_id: str,
    approval_id: str,
    provider_receipt: dict[str, Any],
) -> None:
    """Resolve a previously unknown action from provider evidence without replay."""
    session = nullcontext(db) if db is not None else SessionLocal()
    with session as db:
        task = db.get(Task, task_id)
        run = db.get(AgentRun, run_id)
        step = db.get(TaskStep, step_id)
        attempt = db.scalar(
            select(TaskStepAttempt)
            .where(
                TaskStepAttempt.task_id == task_id,
                TaskStepAttempt.run_id == run_id,
                TaskStepAttempt.step_id == step_id,
            )
            .order_by(TaskStepAttempt.attempt_number.desc())
        )
        if (
            task is None
            or run is None
            or step is None
            or step.task_id != task_id
            or attempt is None
            or attempt.state != AttemptState.OUTCOME_UNKNOWN.value
        ):
            raise RuntimeError(
                "The unknown execution attempt could not be reconciled safely."
            )

        now = utcnow()
        receipt = dict(provider_receipt)
        typed_result = ExecutionResult(
            task_id=task_id,
            run_id=run_id,
            step_id=step_id,
            action_id=step.action_id or "",
            approval_id=approval_id,
            attempt=attempt.attempt_number,
            lifecycle=StepLifecycle.COMPLETED,
            outcome=ActionOutcome.PROVIDER_ACCEPTED,
            verification=VerificationStatus.PENDING,
            output=receipt,
            evidence=[{"type": "provider_receipt", **receipt}],
            provider_receipt=receipt,
            safe_summary="Provider acceptance was confirmed during reconciliation.",
            started_at=attempt.started_at,
            finished_at=now,
        )
        from app.errors import ToolResult

        raw = ToolResult.ok(
            "Provider acceptance was confirmed during reconciliation.",
            receipt,
        ).model_dump_json()
        persisted_result = {
            "raw": raw,
            "execution_result": typed_result.model_dump(mode="json"),
        }
        attempt.state = AttemptState.COMPLETED.value
        attempt.outcome_status = ActionOutcome.PROVIDER_ACCEPTED.value
        attempt.result_data = persisted_result
        attempt.finished_at = now
        step.status = "done"
        step.outcome_status = ActionOutcome.PROVIDER_ACCEPTED.value
        step.verification_status = VerificationStatus.PENDING.value
        step.result_data = persisted_result
        step.completion_outcomes = [ActionOutcome.PROVIDER_ACCEPTED.value]
        step.error_code = None
        step.completed_at = now

        plan = task.plan if isinstance(task.plan, list) else []
        for plan_step in plan:
            if (
                isinstance(plan_step, dict)
                and plan_step.get("id") == step.plan_step_id
            ):
                plan_step["status"] = "done"
                plan_step["outcome_status"] = ActionOutcome.PROVIDER_ACCEPTED.value
        task.plan = plan
        all_steps_terminal = all(
            item.status in {"done", "skipped"}
            for item in db.scalars(
                select(TaskStep).where(TaskStep.task_id == task_id)
            ).all()
        )
        if all_steps_terminal:
            task.status = "completed"
            task.progress = 100
            task.current_step = len(plan)
            task.error = None
            task.result = (
                "Email provider acceptance was confirmed during reconciliation."
            )
            task.completed_at = now
            run.status = "completed"
            run.state_json = None
        else:
            task.status = "partial"
            run.status = "partial"
        run.updated_at = now
        db.commit()
