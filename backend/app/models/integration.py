from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class Integration(Base):
    """OAuth/external-service integration framework (§76).

    Backbone for Gmail (§34–38) now and Phase-I providers later. `status` is
    one of: disabled | connected | error. `config` holds provider-specific
    data; OAuth tokens are ALWAYS Fernet-encrypted there and are never
    returned by any API (the serializers strip secret-ish keys).
    """

    __tablename__ = "integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_integration_user_provider"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))  # gmail | email_mock | ...
    status: Mapped[str] = mapped_column(String(16), default="disabled")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)