from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class CalendarEvent(Base):
    """A calendar event (§63).

    The mock calendar provider persists here; the Google Calendar adapter keeps
    the same shape and mirrors `external_id`. `attendees` is a JSON list of
    {name, email, phone}. Times are stored naive UTC.
    """

    __tablename__ = "calendar_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Nairobi")
    location: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    attendees: Mapped[list] = mapped_column(JSON, default=list)
    # mock | google
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    external_id: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(20), default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)
