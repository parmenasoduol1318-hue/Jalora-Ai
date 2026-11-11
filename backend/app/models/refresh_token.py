"""Refresh token model (Phase L, sec 74).

Refresh tokens are stored as SHA-256 hashes so that a database leak does not
compromise active sessions.  Each token is tied to a single user and expires
after ``JALORA_REFRESH_TOKEN_EXPIRE_DAYS`` (default 30).  Revocation is done
by flipping the ``revoked`` flag — logout is therefore server-side.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), index=True, nullable=False
    )
    # SHA-256 hex digest of the raw token — raw token is never persisted.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        expires = self.expires_at
        # SQLite strips tzinfo on round-trip; normalise naive to UTC.
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now >= expires
