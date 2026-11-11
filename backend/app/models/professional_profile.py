from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import utcnow


class ProfessionalProfile(Base):
    __tablename__ = "professional_profiles"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    courses: Mapped[list[str]] = mapped_column(JSON, default=list)
    qualifications: Mapped[list[str]] = mapped_column(JSON, default=list)
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    phone: Mapped[str] = mapped_column(String(50), default="")
    discoverable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    share_email: Mapped[bool] = mapped_column(Boolean, default=False)
    share_phone: Mapped[bool] = mapped_column(Boolean, default=False)
    search_text: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
