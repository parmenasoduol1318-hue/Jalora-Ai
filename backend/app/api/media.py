"""Media upload for chat attachments (spec §42–43).

Images, audio and video are stored per-user under
``data/uploads/<user_id>/media/`` and referenced by a MediaFile id. The chat
request may carry `media_ids`; the agent then analyses them with the
analyze_image / transcribe_audio / analyze_video tools. Magic-byte sniffing
is enforced so a re-named binary can never pretend to be media (§39).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import get_settings
from app.errors import BadRequestError, NotFoundError
from app.models.media import MediaFile
from app.models.user import User, new_id
from app.schemas import MediaUploadOut
from app.services import artifacts
from app.services.extractors import sniff_mime

router = APIRouter(prefix="/api/media", tags=["media"])

MAX_BYTES = {
    "image": 15 * 1024 * 1024,
    "audio": 150 * 1024 * 1024,
    "video": 800 * 1024 * 1024,
}

KIND_FROM_MIME = {
    "image": "image",
    "audio": "audio",
    "video": "video",
}


@router.post("", response_model=MediaUploadOut, status_code=201)
async def upload_media(file: UploadFile = File(...),
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    content = await file.read()
    if not content:
        raise BadRequestError("The uploaded file is empty.", "EMPTY_FILE")
    mime = sniff_mime(content)
    kind = None
    for prefix, value in KIND_FROM_MIME.items():
        if mime.startswith(prefix + "/"):
            kind = value
            break
    if kind is None:
        raise BadRequestError(
            f"'{file.filename}' is not an image, audio or video file.",
            "UNSUPPORTED_FORMAT",
        )
    if len(content) > MAX_BYTES[kind]:
        raise BadRequestError(
            f"File exceeds the {MAX_BYTES[kind] // (1024 * 1024)} MB limit for {kind} files.",
            "FILE_TOO_LARGE",
        )

    ext = _ext_from_mime(mime)
    media_id = new_id()
    storage = f"media/{media_id}{ext}"
    path = artifacts.user_upload_dir(user.id) / storage
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    row = MediaFile(
        id=media_id, user_id=user.id, filename=file.filename or f"media{ext}",
        mime_type=mime, kind=kind, size_bytes=len(content), storage_name=storage,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    url = f"/api/artifacts/{user.id}/{storage}"
    return MediaUploadOut(
        id=row.id, filename=row.filename, mime_type=row.mime_type,
        kind=row.kind, size_bytes=row.size_bytes, url=url, created_at=row.created_at,
    )


def _ext_from_mime(mime: str) -> str:
    from app.services.extractors import MIME_EXTENSIONS

    return MIME_EXTENSIONS.get(mime, "")


def get_media_file(db: Session, user_id: str, media_id: str) -> MediaFile:
    row = db.get(MediaFile, media_id)
    if row is None or row.user_id != user_id:
        raise NotFoundError("Media file not found.", "MEDIA_NOT_FOUND")
    return row


def media_path(row: MediaFile) -> str:
    return str(artifacts.user_upload_dir(row.user_id) / row.storage_name)


# Re-exported for tools which need to locate stored media bytes.
def user_media_dir(user_id: str):
    return artifacts.user_upload_dir(user_id) / "media"


def outputs_dir(user_id: str):
    from app.config import get_settings

    folder = get_settings().outputs_dir / user_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder