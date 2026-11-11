from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class CommsIdentity(Base):
    """Links a phone address (SMS/WhatsApp) to a user and their comms conversation."""

    __tablename__ = "comms_identities"
    __table_args__ = (
        UniqueConstraint("channel", "address", name="uq_comms_channel_address"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))          # sms | whatsapp
    address: Mapped[str] = mapped_column(String(32))          # E.164, e.g. +254...
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("conversations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
