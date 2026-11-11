"""Drive service (§64).

Mock-first file store. Small text files keep their body inline; uploaded
content is written under `data/drive/<user_id>/` and referenced by
`storage_path`. A Google Drive adapter is optional; the local store always
works offline.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import SessionLocal
from app.models.drive import DriveFile


class DriveError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _drive_dir(user_id: str) -> Path:
    path = get_settings().data_dir / "drive" / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_as_dict(f: DriveFile, *, include_content: bool = False) -> dict:
    data = {
        "id": f.id,
        "name": f.name,
        "mime_type": f.mime_type,
        "size_bytes": f.size_bytes,
        "provider": f.provider,
        "external_id": f.external_id,
    }
    if include_content:
        data["content"] = read_content(f)
    return data


def get_file(db: Session, *, user_id: str, file_id: str) -> DriveFile:
    row = db.get(DriveFile, file_id)
    if row is None or row.user_id != user_id:
        raise DriveError("FILE_NOT_FOUND", "Drive file not found.")
    return row


def read_content(f: DriveFile) -> str:
    if f.content_text:
        return f.content_text
    stored = Path(f.storage_path).name if f.storage_path else ""
    if stored:
        target = _drive_dir(f.user_id) / stored
        if target.exists():
            return target.read_text(encoding="utf-8", errors="replace")
    return ""


def search_files(db: Session, *, user_id: str, query: str = "", limit: int = 20) -> list[DriveFile]:
    stmt = select(DriveFile).where(DriveFile.user_id == user_id)
    q = (query or "").strip()
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(DriveFile.name.ilike(like),
                              DriveFile.mime_type.ilike(like),
                              DriveFile.content_text.ilike(like)))
    stmt = stmt.order_by(DriveFile.name).limit(max(1, min(int(limit or 20), 100)))
    return list(db.scalars(stmt).all())


def create_file(db: Session, *, user_id: str, name: str, content: str = "",
                mime_type: str = "text/plain") -> DriveFile:
    name = (name or "").strip()
    if not name:
        raise DriveError("INVALID_FILE", "A file name is required.")
    row = DriveFile(user_id=user_id, name=name, mime_type=mime_type or "text/plain",
                    content_text=content or "", provider="mock")
    row.size_bytes = len((content or "").encode("utf-8"))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def upload_file(db: Session, *, user_id: str, name: str, content: str = "",
                mime_type: str = "text/plain") -> DriveFile:
    name = (name or "").strip()
    if not name:
        raise DriveError("INVALID_FILE", "A file name is required.")
    stored = f"{uuid.uuid4().hex}_{Path(name).name}"
    data = (content or "").encode("utf-8")
    (_drive_dir(user_id) / stored).write_bytes(data)
    row = DriveFile(user_id=user_id, name=Path(name).name,
                    mime_type=mime_type or "text/plain", storage_path=stored,
                    size_bytes=len(data), provider="mock")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- public API (own session) ----------------------------------------------
def list_drive(*, user_id: str, query: str = "", limit: int = 20) -> list[dict]:
    db = SessionLocal()
    try:
        return [file_as_dict(f) for f in search_files(
            db, user_id=user_id, query=query, limit=limit)]
    finally:
        db.close()


def read_drive(*, user_id: str, file_id: str) -> dict:
    db = SessionLocal()
    try:
        row = get_file(db, user_id=user_id, file_id=file_id)
        return file_as_dict(row, include_content=True)
    finally:
        db.close()


def create_drive(*, user_id: str, name: str, content: str = "",
                 mime_type: str = "text/plain") -> dict:
    db = SessionLocal()
    try:
        return file_as_dict(create_file(db, user_id=user_id, name=name,
                                        content=content, mime_type=mime_type))
    finally:
        db.close()


def upload_drive(*, user_id: str, name: str, content: str = "",
                 mime_type: str = "text/plain") -> dict:
    db = SessionLocal()
    try:
        return file_as_dict(upload_file(db, user_id=user_id, name=name,
                                        content=content, mime_type=mime_type))
    finally:
        db.close()
