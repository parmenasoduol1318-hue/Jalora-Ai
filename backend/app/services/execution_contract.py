"""Typed lifecycle and result contracts for canonical task execution."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskLifecycle(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"


class StepLifecycle(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ActionOutcome(str, Enum):
    PLANNED = "planned"
    PREPARED = "prepared"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    PROVIDER_ACCEPTED = "provider_accepted"
    DELIVERED = "delivered"
    VERIFIED = "verified"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class VerificationStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    NOTIFY = "notify"
    CONFIRM = "confirm"
    DENY = "deny"


class ApprovalDecision(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CANCELLED = "cancelled"


class ApprovalClaim(str, Enum):
    UNCLAIMED = "unclaimed"
    CLAIMED = "claimed"
    FINISHED = "finished"


class AttemptState(str, Enum):
    PREPARED = "prepared"
    CLAIMED = "claimed"
    DISPATCHING = "dispatching"
    RESPONSE_RECEIVED = "response_received"
    RECONCILING = "reconciling"
    COMPLETED = "completed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCELLED = "cancelled"


class ExecutionError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(default="", max_length=1000)


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "1.0"
    task_id: str = Field(min_length=1, max_length=32)
    run_id: str = Field(min_length=1, max_length=32)
    step_id: str = Field(min_length=1, max_length=32)
    action_id: str = Field(min_length=1, max_length=32)
    approval_id: str | None = None
    attempt: int = Field(ge=1)
    lifecycle: StepLifecycle
    outcome: ActionOutcome
    verification: VerificationStatus = VerificationStatus.PENDING
    error: ExecutionError | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    provider_receipt: dict[str, Any] | None = None
    safe_summary: str = Field(default="", max_length=2000)
    started_at: datetime
    finished_at: datetime


_TASK_STATUS_IN = {
    "waiting_for_approval": TaskLifecycle.AWAITING_APPROVAL,
    "error": TaskLifecycle.FAILED,
}
_STEP_STATUS_IN = {
    "active": StepLifecycle.RUNNING,
    "done": StepLifecycle.COMPLETED,
}
_RUN_STATUS_IN = {
    "paused_approval": TaskLifecycle.AWAITING_APPROVAL,
    "error": TaskLifecycle.FAILED,
}


def task_lifecycle(value: str) -> TaskLifecycle | None:
    try:
        return _TASK_STATUS_IN[value] if value in _TASK_STATUS_IN else TaskLifecycle(value)
    except ValueError:
        return None


def step_lifecycle(value: str) -> StepLifecycle | None:
    try:
        return _STEP_STATUS_IN[value] if value in _STEP_STATUS_IN else StepLifecycle(value)
    except ValueError:
        return None


def run_lifecycle(value: str) -> TaskLifecycle | None:
    try:
        return _RUN_STATUS_IN[value] if value in _RUN_STATUS_IN else TaskLifecycle(value)
    except ValueError:
        return None
