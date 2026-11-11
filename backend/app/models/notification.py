"""In-app notifications (spec §50, §61-62).

Kind classifies the source; read_at marks dismissal by the user. The frontend
bell polls unread rows; the durable row is the source of truth (the live
emitter is only used when a request context is active).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("occurrence_id", name="uq_notifications_occurrence_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    # reminder | scheduled_task_completed | scheduled_task_failed |
    # approval_required | inbound_email | inbound_comms | system
    kind: Mapped[str] = mapped_column(String(32), default="system", index=True)
    link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    schedule_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    occurrence_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)