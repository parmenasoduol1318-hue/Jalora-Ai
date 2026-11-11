"""Shared, durable approval decisions and exact-payload claims."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.errors import BadRequestError
from app.models.agent import ActivityLog, Approval, ApprovalRevision, AgentRun
from app.models.task import Task, TaskStep
from app.models.user import utcnow
from app.services.audit import redact

ApprovalDecisionValue = Literal[
    "approved", "rejected", "skipped", "revoked", "cancelled"
]
DEFAULT_APPROVAL_TTL = timedelta(hours=24)


def _safe_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    return redact({
        key: value for key, value in arguments.items()
        if key != "call_id"
    })


def approval_payload_digest(
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    encoded = json.dumps(
        {"tool": tool_name, "arguments": _safe_payload(arguments)},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def initialize_approval(
    approval: Approval,
    *,
    now: datetime | None = None,
) -> Approval:
    now = now or utcnow()
    approval.decision_status = "pending"
    approval.claim_status = "unclaimed"
    approval.payload_version = max(1, approval.payload_version or 1)
    approval.payload_digest = approval_payload_digest(
        approval.tool_name, approval.arguments or {}
    )
    approval.expires_at = approval.expires_at or now + DEFAULT_APPROVAL_TTL
    return approval


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _expire_pending(db: Session, approval: Approval, *, now: datetime) -> bool:
    if approval.status != "pending" or approval.expires_at is None:
        return False
    if _aware(approval.expires_at) > _aware(now):
        return False
    changed = db.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.status == "pending",
            Approval.payload_version == approval.payload_version,
        )
        .values(
            status="expired",
            decision_status="expired",
            claim_status="finished",
            decision_actor_id=None,
            decided_at=now,
        )
    )
    if changed.rowcount != 1:
        db.refresh(approval)
        return approval.status == "expired"
    if approval.task_step_id:
        step = db.get(TaskStep, approval.task_step_id)
        if step is not None:
            step.status = "skipped"
            step.outcome_status = "failed"
            step.error_code = "APPROVAL_EXPIRED"
            step.completed_at = now
    if approval.task_id:
        task = db.get(Task, approval.task_id)
        if task is not None and task.status == "waiting_for_approval":
            task.status = "failed"
            task.error = "A required approval expired before execution."
            task.completed_at = now
        run = db.get(AgentRun, approval.run_id) if approval.run_id else None
        if run is not None and run.status == "paused_approval":
            run.status = "failed"
            run.state_json = None
            run.error = "A required approval expired before execution."
            run.completed_at = now
    db.add(ActivityLog(
        user_id=approval.user_id,
        run_id=approval.run_id or None,
        task_id=approval.task_id,
        task_step_id=approval.task_step_id,
        action_id=(
            db.get(TaskStep, approval.task_step_id).action_id
            if approval.task_step_id
            and db.get(TaskStep, approval.task_step_id) is not None
            else None
        ),
        agent="approval",
        tool=approval.tool_name,
        result_summary="Approval expired before dispatch.",
        success=False,
        approval_status="expired",
        outcome_status="failed",
        error_code="APPROVAL_EXPIRED",
    ))
    db.commit()
    db.refresh(approval)
    return True


def expire_stale_approvals(
    db: Session,
    *,
    user_id: str | None = None,
    now: datetime | None = None,
) -> int:
    now = now or utcnow()
    statement = select(Approval).where(
        Approval.status == "pending",
        Approval.expires_at.is_not(None),
    )
    if user_id is not None:
        statement = statement.where(Approval.user_id == user_id)
    stale = db.scalars(statement).all()
    expired = sum(_expire_pending(db, row, now=now) for row in stale)
    if db.in_transaction():
        db.commit()
    return expired


def claim_approval(
    db: Session,
    approval: Approval,
    *,
    actor_id: str,
    decision: ApprovalDecisionValue,
    now: datetime | None = None,
) -> Approval:
    """Atomically bind one decision to the exact approved payload revision."""
    now = now or utcnow()
    if approval.status != "pending":
        raise BadRequestError(
            f"This request was already {approval.status}.",
            "APPROVAL_DECIDED",
        )
    if _expire_pending(db, approval, now=now):
        raise BadRequestError(
            "This approval expired before the decision could be applied.",
            "APPROVAL_EXPIRED",
        )
    digest = approval_payload_digest(
        approval.tool_name, approval.arguments or {}
    )
    if approval.payload_digest and approval.payload_digest != digest:
        raise BadRequestError(
            "The reviewed approval payload changed. Review the current version before deciding.",
            "APPROVAL_PAYLOAD_MISMATCH",
        )
    old_version = approval.payload_version or 1
    updates: dict[str, Any] = {
        "status": "sending" if decision == "approved" else decision,
        "decision_status": decision,
        "claim_status": "claimed" if decision == "approved" else "finished",
        "decision_actor_id": actor_id,
        "claimed_at": now if decision == "approved" else None,
        "decided_at": None if decision == "approved" else now,
        "revoked_at": now if decision == "revoked" else None,
        "payload_digest": digest,
        "payload_version": old_version,
    }
    changed = db.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.user_id == actor_id,
            Approval.status == "pending",
            Approval.payload_version == old_version,
            or_(
                Approval.expires_at.is_(None),
                Approval.expires_at > now,
            ),
        )
        .values(**updates)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.refresh(approval)
        if _expire_pending(db, approval, now=now):
            raise BadRequestError(
                "This approval expired before the decision could be applied.",
                "APPROVAL_EXPIRED",
            )
        raise BadRequestError(
            "This approval is already being processed or was decided.",
            "APPROVAL_DECIDED",
        )
    if decision != "approved" and approval.task_step_id:
        step = db.get(TaskStep, approval.task_step_id)
        if step is not None:
            step.status = "skipped"
            step.outcome_status = decision
            step.error_code = None
            step.completed_at = now
    db.refresh(approval)
    return approval


def edit_approval_payload(
    db: Session,
    approval: Approval,
    *,
    arguments: dict[str, Any],
    editor_user_id: str,
    now: datetime | None = None,
) -> Approval:
    """Save a sanitized prior revision and CAS the pending payload to its edit."""
    now = now or utcnow()
    if approval.status != "pending":
        raise BadRequestError(
            f"This request was already {approval.status}.", "APPROVAL_DECIDED"
        )
    if _expire_pending(db, approval, now=now):
        raise BadRequestError(
            "This approval expired before it could be edited.",
            "APPROVAL_EXPIRED",
        )
    call_id = str((approval.arguments or {}).get("call_id") or "")
    current_arguments = dict(approval.arguments or {})
    current_digest = approval_payload_digest(
        approval.tool_name, current_arguments
    )
    if approval.payload_digest and approval.payload_digest != current_digest:
        raise BadRequestError(
            "The approval payload changed concurrently. Reload it before editing.",
            "APPROVAL_PAYLOAD_MISMATCH",
        )
    next_arguments = {"call_id": call_id, **arguments} if call_id else arguments
    next_digest = approval_payload_digest(approval.tool_name, next_arguments)
    if next_digest == current_digest:
        return approval
    old_version = approval.payload_version or 1
    changed = db.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.user_id == editor_user_id,
            Approval.status == "pending",
            Approval.payload_version == old_version,
            or_(
                Approval.payload_digest.is_(None),
                Approval.payload_digest == current_digest,
            ),
            or_(Approval.expires_at.is_(None), Approval.expires_at > now),
        )
        .values(
            arguments=next_arguments,
            payload_version=old_version + 1,
            payload_digest=next_digest,
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        db.rollback()
        db.refresh(approval)
        if _expire_pending(db, approval, now=now):
            raise BadRequestError(
                "This approval expired before it could be edited.",
                "APPROVAL_EXPIRED",
            )
        raise BadRequestError(
            "This approval changed while it was being edited. Reload and retry.",
            "APPROVAL_EDIT_CONFLICT",
        )
    db.add(ApprovalRevision(
        approval_id=approval.id,
        version=old_version,
        arguments=_safe_payload(current_arguments),
        payload_digest=current_digest,
        editor_user_id=editor_user_id,
        created_at=now,
    ))
    db.refresh(approval)
    return approval


def finalize_approval(
    db: Session,
    approval: Approval,
    *,
    status: Literal["approved", "failed", "outcome_unknown"],
    result: dict[str, Any] | None = None,
) -> Approval:
    if approval.status != "sending" or approval.claim_status != "claimed":
        raise BadRequestError(
            "The approval claim is no longer active.",
            "APPROVAL_CLAIM_LOST",
        )
    now = utcnow()
    approval.status = status
    approval.claim_status = "finished"
    approval.decided_at = now
    approval.result = redact(result or {"status": status})
    if approval.task_step_id:
        step = db.get(TaskStep, approval.task_step_id)
        if step is not None:
            if status == "outcome_unknown":
                step.outcome_status = "outcome_unknown"
                step.verification_status = "unavailable"
                step.error_code = "OUTCOME_UNKNOWN"
                step.completed_at = now
            elif status == "failed":
                step.status = "failed"
                step.outcome_status = "failed"
                step.verification_status = "failed"
                step.error_code = str((result or {}).get("error_code") or "ACTION_FAILED")
                step.completed_at = now
            else:
                # The tool execution result records the actual action state.
                step.outcome_status = (
                    str((result or {}).get("action_outcome") or "succeeded")
                )
    db.flush()
    return approval


def reconcile_unknown_approval(
    db: Session,
    approval: Approval,
    *,
    actor_id: str,
    result: dict[str, Any],
) -> Approval:
    now = utcnow()
    changed = db.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.user_id == actor_id,
            Approval.status == "outcome_unknown",
        )
        .values(
            status="approved",
            decision_status="approved",
            claim_status="finished",
            decision_actor_id=actor_id,
            decided_at=now,
            result=redact(result),
        )
    )
    if changed.rowcount != 1:
        db.refresh(approval)
        if approval.status != "approved":
            raise BadRequestError(
                "The approval changed while its outcome was being reconciled.",
                "APPROVAL_DECISION_CHANGED",
            )
    if approval.task_step_id:
        step = db.get(TaskStep, approval.task_step_id)
        if step is not None:
            step.outcome_status = "provider_accepted"
            step.verification_status = "pending"
            step.error_code = None
            step.result_data = redact({
                **(step.result_data or {}),
                "reconciliation": result,
            })
    db.flush()
    return approval


def cancel_task_pending_approvals(
    db: Session,
    *,
    task: Task,
    actor_id: str,
    now: datetime | None = None,
) -> int:
    now = now or utcnow()
    pending = db.scalars(
        select(Approval).where(
            Approval.user_id == actor_id,
            Approval.status == "pending",
            or_(
                Approval.task_id == task.id,
                and_(
                    Approval.task_id.is_(None),
                    Approval.run_id == (task.run_id or ""),
                ),
            ),
        )
    ).all()
    changed = db.execute(
        update(Approval)
        .where(
            Approval.user_id == actor_id,
            Approval.status == "pending",
            or_(
                Approval.task_id == task.id,
                and_(
                    Approval.task_id.is_(None),
                    Approval.run_id == (task.run_id or ""),
                ),
            ),
        )
        .values(
            status="cancelled",
            decision_status="cancelled",
            claim_status="finished",
            decision_actor_id=actor_id,
            decided_at=now,
        )
    )
    for approval in pending:
        if approval.task_step_id:
            step = db.get(TaskStep, approval.task_step_id)
            if step is not None:
                step.status = "skipped"
                step.outcome_status = "cancelled"
                step.completed_at = now
    return int(changed.rowcount or 0)


def cancel_run_pending_approvals(
    db: Session,
    *,
    run_id: str,
    actor_id: str,
    now: datetime | None = None,
) -> int:
    now = now or utcnow()
    pending = db.scalars(
        select(Approval).where(
            Approval.run_id == run_id,
            Approval.user_id == actor_id,
            Approval.status == "pending",
        )
    ).all()
    changed = db.execute(
        update(Approval)
        .where(
            Approval.run_id == run_id,
            Approval.user_id == actor_id,
            Approval.status == "pending",
        )
        .values(
            status="cancelled",
            decision_status="cancelled",
            claim_status="finished",
            decision_actor_id=actor_id,
            decided_at=now,
        )
    )
    for approval in pending:
        if approval.task_step_id:
            step = db.get(TaskStep, approval.task_step_id)
            if step is not None:
                step.status = "skipped"
                step.outcome_status = "cancelled"
                step.completed_at = now
    return int(changed.rowcount or 0)
