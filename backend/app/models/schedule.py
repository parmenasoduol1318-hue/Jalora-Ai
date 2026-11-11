"""Scheduling (spec §49-51, §61-62, §73).

A Schedule row is the durable source of truth for a one-shot or recurring
automation. `services/scheduler.py` mirrors enabled rows into APScheduler jobs;
when a job fires it either raises a notification directly (`notify_only`
reminder) or runs the real agent pipeline to create a Task and completes it
(scheduled task).
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    # once | cron | interval — timing kind; `spec` holds the trigger arguments
    kind: Mapped[str] = mapped_column(String(16), default="cron", index=True)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule_expression: Mapped[str] = mapped_column(String(200), default="")
    task_template: Mapped[str] = mapped_column(Text, default="")
    # True = reminder (fire a notification only); False = scheduled agent task
    notify_only: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ScheduleOccurrence(Base):
    __tablename__ = "schedule_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id", "occurrence_key",
            name="uq_schedule_occurrences_schedule_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    occurrence_key: Mapped[str] = mapped_column(String(64), unique=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="dispatching", index=True)
    task_template: Mapped[str] = mapped_column(Text, default="")
    notify_only: Mapped[bool] = mapped_column(Boolean, default=False)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    notification_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SchedulerLease(Base):
    __tablename__ = "scheduler_leases"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)