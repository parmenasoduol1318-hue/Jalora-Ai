from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class AgentRun(Base):
    """Durable record of an agent execution; state_json lets a paused run
    (waiting for approval) be resumed later."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("tasks.id"), nullable=True, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    # running | completed | failed | partial | paused_approval | paused | stopped
    state_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Approval(Base):
    """Human-in-the-loop approval for Level 3/4 tool calls (spec §24)."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("tasks.id"), nullable=True, index=True)
    task_step_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("task_steps.id"), nullable=True, index=True)
    plan_step_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    level: Mapped[int] = mapped_column(Integer, default=3)
    # kind: "tool" | "credentials"
    kind: Mapped[str] = mapped_column(String(16), default="tool")
    # pending -> sending -> approved|failed|outcome_unknown, or pending -> rejected|skipped
    # ``sending`` is the durable idempotency claim: a second approval request
    # cannot invoke the provider while the first request is in flight.
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decision_status: Mapped[str] = mapped_column(String(16), default="pending")
    claim_status: Mapped[str] = mapped_column(String(16), default="unclaimed")
    payload_version: Mapped[int] = mapped_column(Integer, default=1)
    payload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_actor_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActivityLog(Base):
    """Audit trail for every action (spec §25)."""

    __tablename__ = "activity_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("tasks.id"), nullable=True, index=True)
    task_step_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("task_steps.id"), nullable=True, index=True)
    action_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("task_step_attempts.id"), nullable=True, index=True)
    outcome_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent: Mapped[str] = mapped_column(String(64), default="chat")
    tool: Mapped[str] = mapped_column(String(128), default="")
    arguments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # Phase L (§75): audit completeness — provider that handled the action + error code.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ApprovalRevision(Base):
    __tablename__ = "approval_revisions"
    __table_args__ = (
        UniqueConstraint("approval_id", "version", name="uq_approval_revision_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    approval_id: Mapped[str] = mapped_column(String(32), ForeignKey("approvals.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    arguments: Mapped[dict] = mapped_column(JSON)
    payload_digest: Mapped[str] = mapped_column(String(64))
    editor_user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
