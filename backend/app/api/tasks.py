from __future__ import annotations

import re
from datetime import timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.errors import BadRequestError, NotFoundError
from app.models.agent import AgentRun, Approval
from app.models.conversation import Message
from app.models.task import Task, TaskStep, TaskStepAttempt
from app.models.user import User
from app.models.workflows import WorkflowExecutionRecord
from app.services import run_registry

router = APIRouter(prefix="/api", tags=["tasks"])

ACTIVE_STATUSES = {"queued", "running", "waiting_for_approval", "paused"}

# Default and max limits for pagination
DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _view(row: Task, *, resumable: bool = False) -> dict[str, Any]:
    """Convert Task model to response dict - avoids Pydantic serialization overhead."""
    return {
        "id": row.id,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "run_id": row.run_id,
        "goal": row.goal,
        "status": row.status,
        "resumable": resumable,
        "plan": row.plan,
        "current_step": row.current_step,
        "progress": row.progress,
        "error": row.error,
        "result": row.result,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _view_detail(
    row: Task,
    steps: list[TaskStep],
    *,
    resumable: bool = False,
    outreach_queue: list[dict[str, Any]] | None = None,
    approvals: list[Approval] | None = None,
) -> dict[str, Any]:
    """Convert Task with steps to response dict."""
    from app.services.audit import redact

    approval_by_id = {approval.id: approval for approval in approvals or []}
    approval_by_plan_step = {
        approval.plan_step_id: approval
        for approval in approvals or []
        if approval.plan_step_id
    }

    def step_view(step: TaskStep) -> dict[str, Any]:
        result_data = step.result_data if isinstance(step.result_data, dict) else {}
        execution_result = result_data.get("execution_result")
        if not isinstance(execution_result, dict):
            execution_result = {}
        approval = approval_by_id.get(step.approval_id or "")
        if approval is None and step.plan_step_id:
            approval = approval_by_plan_step.get(step.plan_step_id)
        evidence = execution_result.get("evidence")
        dependencies = step.dependencies
        dependency_step_ids = step.dependency_step_ids
        return {
            "id": step.id,
            "plan_step_id": step.plan_step_id,
            "task_id": step.task_id,
            "idx": step.idx,
            "description": step.description,
            "status": step.status,
            "detail": step.detail,
            "dependencies": (
                [item for item in dependencies if isinstance(item, int) and not isinstance(item, bool)]
                if isinstance(dependencies, list)
                else []
            ),
            "dependency_step_ids": (
                [str(item) for item in dependency_step_ids if item]
                if isinstance(dependency_step_ids, list)
                else []
            ),
            "tool_id": step.tool_id,
            "capability": step.capability,
            "tool_version": step.tool_version,
            "outcome_status": step.outcome_status,
            "verification_status": step.verification_status,
            "error_code": step.error_code,
            "approval_id": step.approval_id or (approval.id if approval else None),
            "approval_status": approval.status if approval is not None else None,
            "safe_summary": (
                execution_result.get("safe_summary", "")
                if isinstance(execution_result.get("safe_summary"), str)
                else ""
            ),
            "evidence": (
                [redact(item) for item in evidence if isinstance(item, dict)]
                if isinstance(evidence, list)
                else []
            ),
            "updated_at": step.updated_at.isoformat() if step.updated_at else None,
        }

    def contains_redaction(value: Any) -> bool:
        if isinstance(value, dict):
            return any(contains_redaction(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_redaction(item) for item in value)
        return value == "***redacted***"

    def approval_view(approval: Approval) -> dict[str, Any]:
        safe_arguments = redact(approval.arguments or {})
        return {
            "id": approval.id,
            "run_id": approval.run_id,
            "task_step_id": approval.task_step_id,
            "plan_step_id": approval.plan_step_id,
            "tool_name": approval.tool_name,
            "arguments": safe_arguments,
            "level": approval.level,
            "kind": approval.kind,
            "status": approval.status,
            "can_approve": (
                approval.status == "pending"
                and approval.claim_status == "unclaimed"
                and approval.kind != "credentials"
                and not contains_redaction(safe_arguments)
            ),
            "decision_status": approval.decision_status,
            "claim_status": approval.claim_status,
            "payload_version": approval.payload_version,
            "can_edit": (
                approval.status == "pending"
                and approval.claim_status == "unclaimed"
                and approval.kind != "credentials"
                and approval.tool_name in {
                    "send_email", "send_email_batch", "request_user_input"
                }
                and not contains_redaction(safe_arguments)
            ),
            "created_at": (
                approval.created_at.isoformat() if approval.created_at else None
            ),
            "expires_at": (
                approval.expires_at.isoformat() if approval.expires_at else None
            ),
        }

    task_approvals = [approval_view(approval) for approval in approvals or []]
    return {
        "id": row.id,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "run_id": row.run_id,
        "goal": row.goal,
        "status": row.status,
        "resumable": resumable,
        "plan": row.plan,
        "current_step": row.current_step,
        "progress": row.progress,
        "error": row.error,
        "result": row.result,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "steps": [step_view(step) for step in steps],
        "approvals": task_approvals,
        "outreach_queue": outreach_queue,
    }


def _outreach_queue(
    task: Task,
    run: AgentRun | None,
    approvals: list[Approval],
    db: Session,
) -> list[dict[str, Any]] | None:
    """Derive the durable sequential outreach view from the canonical run."""
    plan = task.plan
    messages = []
    if run is not None and run.state_json:
        from app.agents.graph import deserialize_state

        state = deserialize_state(run.state_json)
        if isinstance(state.get("plan"), list):
            plan = state["plan"]
        messages = state.get("messages", [])
    if not messages and run is not None and task.conversation_id:
        from langchain_core.messages import AIMessage, ToolMessage

        persisted_messages = db.scalars(
            select(Message)
            .where(
                Message.conversation_id == task.conversation_id,
                Message.meta["run_id"].as_string() == run.id,
            )
            .order_by(Message.created_at, Message.id)
        ).all()
        for row in persisted_messages:
            if row.role == "assistant" and isinstance(row.tool_calls, list):
                messages.append(AIMessage(content="", tool_calls=row.tool_calls))
            elif row.role == "tool":
                messages.append(ToolMessage(
                    content=row.content,
                    tool_call_id=str((row.meta or {}).get("call_id") or ""),
                ))

    if not isinstance(plan, list) or not any(
        isinstance(step, dict) and step.get("tool") == "search_public_contacts"
        for step in plan
    ):
        return None

    from app.agents.graph import (
        _parse_tool_result,
        _public_contact_page_matches_organization,
    )
    from langchain_core.messages import AIMessage, ToolMessage

    calls_by_id = {
        str(call.get("id") or ""): call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls or []
    }
    page_results: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        call = calls_by_id.get(message.tool_call_id)
        if not isinstance(call, dict) or call.get("name") != "open_url":
            continue
        result = _parse_tool_result(str(message.content or ""))
        if not result.success or not isinstance(result.data, dict):
            continue
        url = str(result.data.get("url") or "").strip()
        if url:
            page_results[url] = result.data

    approval_by_call_id: dict[str, Approval] = {}
    approval_by_plan_step: dict[str, Approval] = {}
    for approval in approvals:
        call_id = str((approval.arguments or {}).get("call_id") or "")
        if call_id:
            approval_by_call_id[call_id] = approval
        if approval.plan_step_id:
            approval_by_plan_step[approval.plan_step_id] = approval

    queue: list[dict[str, Any]] = []
    for send_step in plan:
        if not isinstance(send_step, dict) or send_step.get("tool") != "send_email":
            continue
        arguments = send_step.get("arguments")
        recipient = (
            str(arguments.get("to") or "").strip()
            if isinstance(arguments, dict)
            else ""
        )
        if not recipient:
            match = re.search(
                r"[\w.!#$%&'*+/=?^_`{|}~-]+@[\w-]+(?:\.[\w-]+)+",
                str(send_step.get("description") or ""),
            )
            recipient = match.group(0) if match else ""

        dependencies = send_step.get("dependencies")
        dependencies = dependencies if isinstance(dependencies, list) else []
        source_data: dict[str, Any] | None = None
        organization = ""
        for dependency in dependencies:
            try:
                verification_index = int(dependency)
            except (TypeError, ValueError):
                verification_index = next(
                    (
                        index for index, step in enumerate(plan)
                        if isinstance(step, dict)
                        and str(step.get("id") or "") == str(dependency)
                    ),
                    -1,
                )
            if not 0 <= verification_index < len(plan):
                continue
            verify_step = plan[verification_index]
            if not isinstance(verify_step, dict) or verify_step.get("tool") != "open_url":
                continue

            description = str(verify_step.get("description") or "")
            organization_match = re.search(
                r"official contact page for\s+(.+?)(?:\.|$)",
                description,
                flags=re.IGNORECASE,
            )
            if organization_match:
                organization = organization_match.group(1).strip()

            verify_arguments = verify_step.get("arguments")
            source_url = (
                str(verify_arguments.get("url") or "").strip()
                if isinstance(verify_arguments, dict)
                else ""
            )
            candidate_urls = re.search(
                r"Candidate URLs:\s*(.+)$",
                description,
                flags=re.IGNORECASE,
            )
            allowed_urls = {source_url} if source_url else set()
            if candidate_urls:
                allowed_urls.update(
                    url.strip()
                    for url in candidate_urls.group(1).split(";")
                    if url.strip()
                )
            source_data = next(
                (
                    page_results[url]
                    for url in allowed_urls
                    if url in page_results
                ),
                None,
            )
            if source_data:
                break

        matching_call_ids = [
            call_id
            for call_id, call in calls_by_id.items()
            if call.get("name") == "send_email"
            and isinstance(call.get("args"), dict)
            and str(call["args"].get("to") or "").strip().casefold()
            == recipient.casefold()
        ]
        approval = approval_by_plan_step.get(str(send_step.get("id") or ""))
        if approval is None:
            approval = next(
                (
                    approval_by_call_id[call_id]
                    for call_id in reversed(matching_call_ids)
                    if call_id in approval_by_call_id
                ),
                None,
            )
        if approval is not None:
            recipient = str(
                (approval.arguments or {}).get("to") or recipient
            ).strip()
        approval_status = approval.status if approval is not None else None
        approval_result = (
            approval.result
            if approval is not None and isinstance(approval.result, dict)
            else {}
        )
        provider_receipt = approval_result.get("provider_receipt")
        provider_receipt = (
            provider_receipt if isinstance(provider_receipt, dict) else {}
        )
        step_status = str(send_step.get("status") or "pending")
        if approval_status == "pending":
            status = "awaiting_approval"
        elif approval_status == "approved":
            status = "sent"
        elif approval_status == "outcome_unknown":
            status = "outcome_unknown"
        elif approval_status in {"failed", "sending"}:
            status = approval_status
        elif approval_status == "rejected":
            status = "rejected"
        elif approval_status == "skipped":
            status = "skipped"
        else:
            status = step_status

        source_emails = (
            source_data.get("public_emails", [])
            if isinstance(source_data, dict)
            else []
        )
        organization_match_on_source = bool(
            organization
            and isinstance(source_data, dict)
            and _public_contact_page_matches_organization(
                organization, source_data
            )
        )
        queue.append({
            "position": len(queue) + 1,
            "plan_step_id": str(send_step.get("id") or ""),
            "organization": organization,
            "recipient": recipient,
            "status": status,
            "source_url": (
                str(source_data.get("url") or "")
                if isinstance(source_data, dict)
                else ""
            ),
            "source_title": (
                str(source_data.get("title") or "")
                if isinstance(source_data, dict)
                else ""
            ),
            "address_found_on_source": bool(
                recipient
                and isinstance(source_emails, list)
                and any(
                    isinstance(address, str)
                    and address.strip().casefold() == recipient.casefold()
                    for address in source_emails
                )
            ),
            "organization_match_on_source": organization_match_on_source,
            "approval_id": approval.id if approval is not None else None,
            "draft": (
                {
                    "to": str((approval.arguments or {}).get("to") or recipient),
                    "cc": str((approval.arguments or {}).get("cc") or ""),
                    "bcc": str((approval.arguments or {}).get("bcc") or ""),
                    "subject": str((approval.arguments or {}).get("subject") or ""),
                    "body": str((approval.arguments or {}).get("body") or ""),
                    "regards": str((approval.arguments or {}).get("regards") or ""),
                    "attachments": (
                        (approval.arguments or {}).get("attachments")
                        if isinstance(
                            (approval.arguments or {}).get("attachments"), list
                        )
                        else []
                    ),
                }
                if approval is not None and approval.status == "pending"
                else None
            ),
            "provider": str(provider_receipt.get("provider") or ""),
            "provider_message_id": str(provider_receipt.get("message_id") or ""),
            "provider_thread_id": str(provider_receipt.get("thread_id") or ""),
        })
    return queue


@router.get("/tasks")
def list_tasks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status: str | None = None,
    run_id: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """List tasks for the authenticated user.

    Optimizations:
    - Default limit reduced to 50 for faster initial load
    - Max limit capped at 100 to prevent DoS
    - Uses indexed columns (user_id, status) for fast queries
    - Direct dict serialization avoids Pydantic overhead
    """
    # Cap limit to prevent excessive data transfer
    limit = min(max(1, limit), MAX_LIMIT)

    # Build query using indexed columns for optimal performance
    stmt = select(Task).where(Task.user_id == user.id)
    if status:
        stmt = stmt.where(Task.status == status)
    if run_id:
        stmt = stmt.where(Task.run_id == run_id)

    # Order by created_at desc (indexed) and limit
    rows = db.scalars(
        stmt.order_by(Task.created_at.desc()).limit(limit)
    ).all()

    run_ids = [row.run_id for row in rows if row.run_id]
    runs = db.scalars(
        select(AgentRun).where(
            AgentRun.user_id == user.id,
            AgentRun.id.in_(run_ids),
        )
    ).all() if run_ids else []
    resumable_run_ids = {
        run.id for run in runs
        if run.state_json and run.status in {"paused", "partial"}
    }
    return [_view(row, resumable=row.run_id in resumable_run_ids) for row in rows]


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Get a single task with its steps."""
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise NotFoundError("Task not found.")
    steps = db.scalars(
        select(TaskStep).where(TaskStep.task_id == task.id)
        .order_by(TaskStep.idx)
    ).all()
    run = db.get(AgentRun, task.run_id) if task.run_id else None
    resumable = bool(
        run is not None and run.user_id == user.id and run.state_json
        and run.status in {"paused", "partial"}
    )
    approvals = db.scalars(
        select(Approval).where(
            Approval.user_id == user.id,
            Approval.run_id == task.run_id,
        ).order_by(Approval.created_at)
    ).all() if task.run_id else []
    return _view_detail(
        task,
        steps,
        resumable=resumable,
        outreach_queue=_outreach_queue(task, run, approvals, db),
        approvals=approvals,
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Cancel a queued, running, paused, or approval-waiting task."""
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise NotFoundError("Task not found.")
    if task.status not in ACTIVE_STATUSES:
        raise BadRequestError(
            f"Task is '{task.status}' and cannot be cancelled.", "TASK_NOT_CANCELLABLE"
        )
    from app.services.chat_service import _utcnow

    now = _utcnow()
    stopped = 0
    if task.run_id:
        sending_approval = db.scalar(
            select(Approval.id)
            .where(
                Approval.user_id == user.id,
                Approval.run_id == task.run_id,
                Approval.status == "sending",
            )
            .limit(1)
        )
        if sending_approval is not None:
            db.rollback()
            raise BadRequestError(
                "An approved action is already in progress. Wait for its "
                "result before cancelling the task.",
                "TASK_ACTION_IN_PROGRESS",
            )

        stopped = run_registry.stop_run(user.id, task.run_id)
        active_attempt = db.scalar(
            select(TaskStepAttempt.id)
            .where(
                TaskStepAttempt.task_id == task.id,
                TaskStepAttempt.state.in_(("dispatching", "reconciling")),
            )
            .limit(1)
        )
        from app.services.approval_lifecycle import cancel_task_pending_approvals

        cancel_task_pending_approvals(db, task=task, actor_id=user.id, now=now)
        run = db.get(AgentRun, task.run_id)
        if run is not None and run.user_id == user.id:
            if active_attempt is not None:
                task.status = "paused"
                task.error = (
                    "Cancellation was requested while an action was in flight; "
                    "its outcome must be reconciled before the task can finish."
                )
                run.status = "running"
            else:
                run.status = "stopped"
                run.state_json = None
                run.error = None
    else:
        active_attempt = None

    if active_attempt is None:
        task.status = "cancelled"
        task.completed_at = now
    workflow_status = "outcome_unknown" if active_attempt is not None else "cancelled"
    workflow_executions = db.scalars(
        select(WorkflowExecutionRecord).where(
            WorkflowExecutionRecord.task_id == task.id,
            WorkflowExecutionRecord.run_id == task.run_id,
            WorkflowExecutionRecord.status.in_(
                ("running", "approval_required", "paused")
            ),
        )
    ).all()
    for execution in workflow_executions:
        execution.status = workflow_status
        execution.error_message = task.error or ""
        execution.completed_at = now
        started_at = execution.started_at
        if started_at is not None and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        execution.duration_seconds = max(
            0.0,
            (now - started_at).total_seconds() if started_at is not None else 0.0,
        )
    if (task.trigger_data or {}).get("kind") == "schedule":
        from app.services.scheduler import sync_task_occurrence

        sync_task_occurrence(db, task)
    db.commit()
    return {
        "success": True,
        "task_id": task.id,
        "status": task.status,
        "stopped_run": bool(stopped),
        "outcome_reconciliation_required": active_attempt is not None,
    }