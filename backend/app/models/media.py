from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class MediaFile(Base):
    """A single attached media file (image/audio/video, §42–43).

    Bytes live under ``data/uploads/<user_id>/media/<storage_name>``; the row
    maps the user-facing id to the on-disk file and enforces ownership when
    analyze_image / transcribe_audio / analyze_video read it back.
    """

    __tablename__ = "media_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="image")  # image|audio|video
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)