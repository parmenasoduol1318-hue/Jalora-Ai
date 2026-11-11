from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Memory(Base):
    """Long-term memory entry. Embedding stored as JSON list for the local
    vector store; swap-in Chroma/FAISS implementations keep the same row."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="fact")  # fact | preference | contact | task_result
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="user")  # user | agent | explicit_tool
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dims: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
