"""User feedback on tasks and tool calls (spec §70, Phase K).

Feedback rows are linked to the message, task or tool call they relate to.
A background job (preference_extractor) promotes repeated feedback into
``kind=preference`` long-term memories via memory_service.update_memory,
ensuring old contradictory entries are reconciled rather than duplicated.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Feedback(Base):
    """A piece of user feedback: thumbs up/down on a task, tool call, or reply."""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    # The entity this feedback refers to. Only one of these is set.
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("tasks.id"), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # sentiment: "positive" | "negative" | "neutral"
    sentiment: Mapped[str] = mapped_column(String(16), default="positive")
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
