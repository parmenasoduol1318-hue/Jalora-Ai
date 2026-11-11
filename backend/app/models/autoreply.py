from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class AutoReplyRule(Base):
    """Inbound email auto-reply rule (§38).

    Match is case-insensitive substring on sender / topic; the rule applies
    inside its time window (minutes since midnight) and honours frequency
    caps (per hour / per day) plus per-rule approval. When `requires_approval`
    is on, the engine holds the reply instead of sending it.
    """

    __tablename__ = "auto_reply_rules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="Auto-reply")
    enabled: Mapped[bool] = mapped_column(default=True)
    sender_pattern: Mapped[str] = mapped_column(String(255), default="")   # substring, "" = any
    topic_pattern: Mapped[str] = mapped_column(String(255), default="")    # substring over subject+body
    time_window_start: Mapped[int] = mapped_column(Integer, default=0)      # minutes since midnight
    time_window_end: Mapped[int] = mapped_column(Integer, default=1440)
    tone: Mapped[str] = mapped_column(String(16), default="professional")   # professional|friendly|brief
    template: Mapped[str] = mapped_column(Text, default="")                 # {sender} placeholder
    max_per_hour: Mapped[int] = mapped_column(Integer, default=0)           # 0 = unlimited
    max_per_day: Mapped[int] = mapped_column(Integer, default=10)
    requires_approval: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)

    # frequency counters (deterministic, refreshed on bucket rollover)
    last_hour_bucket: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hourly_count: Mapped[int] = mapped_column(Integer, default=0)
    last_day: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    daily_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)