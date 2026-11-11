from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.user import new_id, utcnow


class DriveFile(Base):
    """A file in the user's Drive (§64).

    The mock Drive persists metadata (and small text bodies) here; larger
    uploads are written to disk under `data/drive/<user_id>/` and referenced by
    `storage_path`. The Google Drive adapter mirrors `external_id`.
    """

    __tablename__ = "drive_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(500), index=True)
    mime_type: Mapped[str] = mapped_column(String(150), default="text/plain")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Inline text for small/mocked files (read_drive_file returns this).
    content_text: Mapped[str] = mapped_column(Text, default="")
    # Relative path under the per-user drive dir for uploaded binaries.
    storage_path: Mapped[str] = mapped_column(String(1000), default="")
    parent_id: Mapped[str] = mapped_column(String(255), default="")
    # mock | google
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    external_id: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)
