from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Credential(Base):
    """Per-user, per-domain login credential (spec §18).

    `secret` is always Fernet-encrypted at rest; the plaintext value never
    enters the LLM context, the audit log or any API response.
    """

    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "domain", "username",
                         name="uq_cred_user_domain_username"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    domain: Mapped[str] = mapped_column(String(255))  # lowercase hostname
    username: Mapped[str] = mapped_column(String(255))
    secret: Mapped[str] = mapped_column(Text)         # Fernet token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)