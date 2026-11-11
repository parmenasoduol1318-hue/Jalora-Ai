"""User-facing task model (spec §27): goal, plan, progress, statuses.

A Task is created for every agent execution turn and mirrors its AgentRun;
TaskStep rows hold the model's plan so the UI can show live progress (§28).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("run_id", name="uq_tasks_run_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    contract_version: Mapped[str] = mapped_column(String(8), default="1.0")
    input_data: Mapped[dict] = mapped_column(JSON, default=dict)
    target_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trigger_data: Mapped[dict] = mapped_column(JSON, default=dict)
    trigger_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    # queued | running | waiting_for_approval | paused | completed | partial | failed | stopped | cancelled
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    plan: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"description","status"}]
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TaskStep(Base):
    __tablename__ = "task_steps"
    __table_args__ = (
        UniqueConstraint("task_id", "idx", name="uq_task_steps_task_idx"),
        UniqueConstraint("task_id", "plan_step_id", name="uq_task_steps_task_plan_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("tasks.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    plan_step_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # pending | active | done | failed | skipped
    status: Mapped[str] = mapped_column(String(16), default="pending")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    dependencies: Mapped[list | None] = mapped_column(JSON, nullable=True)  # step indices this depends on
    dependency_step_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tool_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    capability: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arguments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    completion_outcomes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome_status: Mapped[str] = mapped_column(String(32), default="planned")
    verification_status: Mapped[str] = mapped_column(String(24), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TaskStepAttempt(Base):
    __tablename__ = "task_step_attempts"
    __table_args__ = (
        UniqueConstraint("step_id", "attempt_number", name="uq_task_step_attempt_number"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("tasks.id"), index=True)
    step_id: Mapped[str] = mapped_column(String(32), ForeignKey("task_steps.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(32), ForeignKey("agent_runs.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="prepared", index=True)
    payload_digest: Mapped[str] = mapped_column(String(64))
    provider_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    result_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome_status: Mapped[str] = mapped_column(String(32), default="prepared")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
