from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class IdeaProject(Base):
    __tablename__ = "idea_projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    brief: Mapped[str] = mapped_column(Text, default="")
    goals: Mapped[str] = mapped_column(Text, default="")
    next_steps: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    milestones: Mapped[list[str]] = mapped_column(JSON, default=list)
    task_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    conversation_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
