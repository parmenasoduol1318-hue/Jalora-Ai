"""Asset library: upload, list, inspect, probe, thumbnail and delete."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.api.creative._helpers import (
    asset_url,
    owned_asset,
    owned_project,
    persist_bytes,
    require_creative,
    serialize_asset,
)
from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.database.session import get_db
from app.errors import BadRequestError
from app.models.creative import MediaAsset
from app.models.user import User
from app.services.creative import CreativeEngineError, media_engine, storage

router = APIRouter(dependencies=[Depends(require_creative)])

_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_AUDIO_EXT = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".opus"}


def _classify(filename: str, content: bytes) -> tuple[str, float | None, int | None, int | None]:
    file_type, _ext = storage.sniff_and_classify(filename, content)
    duration = width = height = None
    ext = Path(filename or "").suffix.lower()
    if file_type == "video" or ext in _VIDEO_EXT:
        try:
            data = persist_probe_bytes(content, ext)
            info = media_engine.probe(data)
            duration = info.get("duration")
            width = info.get("width")
            height = info.get("height")
        except Exception:
            pass
    elif file_type == "image":
        try:
            from app.services.creative import image_engine
            img = image_engine.open_image(content)
            width, height = img.size
        except Exception:
            pass
    elif ext in _AUDIO_EXT:
        try:
            from app.services.creative import audio_engine
            data, sr = _probe_audio_bytes(content, ext)
            duration = round(len(data) / sr, 3) if sr else None
        except Exception:
            pass
    return file_type, duration, width, height


def persist_probe_bytes(content: bytes, ext: str) -> Path:
    import tempfile
    path = Path(tempfile.mkdtemp()) / f"probe{ext or '.bin'}"
    path.write_bytes(content)
    return path


def _probe_audio_bytes(content: bytes, ext: str):
    import tempfile

    from app.services.creative import audio_engine
    path = Path(tempfile.mkdtemp()) / f"probe{ext}"
    path.write_bytes(content)
    return audio_engine.load_audio(path)


@router.post("/projects/{project_id}/assets", status_code=status.HTTP_201_CREATED)
async def upload_asset(project_id: str, file: UploadFile = File(...),
                       db: Session = Depends(get_db), user: User = Depends(get_current_user),
                       settings: Settings = Depends(get_settings)):
    project = owned_project(db, user, project_id)
    content = await file.read()
    max_bytes = settings.creative_max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise BadRequestError(
            f"File exceeds the {settings.creative_max_upload_mb} MB upload limit.",
            "FILE_TOO_LARGE")
    if not content:
        raise BadRequestError("Uploaded file is empty.", "EMPTY_FILE")
    try:
        file_type, duration, width, height = _classify(file.filename or "upload", content)
    except ValueError as exc:
        raise BadRequestError(str(exc), "UNSUPPORTED_FILE") from exc

    asset = persist_bytes(db, user.id, content, file.filename or "upload",
                          file_type=file_type, source="upload", subdir="uploads",
                          project_id=project.id,
                          metadata={"original_name": file.filename})
    asset.duration = duration
    asset.width = width
    asset.height = height
    db.commit()
    db.refresh(asset)
    return {"success": True, "asset": serialize_asset(asset)}


@router.get("/projects/{project_id}/assets")
def list_project_assets(project_id: str, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    return {"success": True, "assets": [serialize_asset(a) for a in project.assets]}


@router.get("/assets")
def list_all_assets(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                    file_type: str | None = None, limit: int = 200):
    query = db.query(MediaAsset).filter(MediaAsset.user_id == user.id)
    if file_type:
        query = query.filter(MediaAsset.file_type == file_type)
    assets = query.order_by(MediaAsset.created_at.desc()).limit(min(limit, 1000)).all()
    return {"success": True, "assets": [serialize_asset(a) for a in assets]}


@router.get("/assets/{asset_id}")
def get_asset(asset_id: str, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    asset = owned_asset(db, user, asset_id)
    data = serialize_asset(asset)
    path = storage.resolve(user.id, asset.file_path)
    data["exists"] = path.exists()
    return {"success": True, "asset": data}


@router.post("/assets/{asset_id}/probe")
def probe_asset(asset_id: str, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    asset = owned_asset(db, user, asset_id)
    path = storage.resolve(user.id, asset.file_path)
    info: dict = {"file_type": asset.file_type, "size_bytes": asset.size_bytes}
    try:
        if asset.file_type == "video":
            info.update(media_engine.probe(path))
        elif asset.file_type == "audio":
            from app.services.creative import audio_engine
            data, sr = audio_engine.load_audio(path)
            info.update({"sample_rate": sr, "duration": round(len(data) / sr, 3),
                         "channels": 2 if getattr(data, "ndim", 1) == 2 else 1})
        elif asset.file_type == "image":
            from app.services.creative import image_engine
            img = image_engine.open_image(path)
            info.update({"width": img.width, "height": img.height, "mode": img.mode})
    except CreativeEngineError as exc:
        from app.api.creative._helpers import raise_http
        raise_http(exc)
    return {"success": True, "info": info}


@router.post("/assets/{asset_id}/thumbnail")
def make_asset_thumbnail(asset_id: str, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    asset = owned_asset(db, user, asset_id)
    path = storage.resolve(user.id, asset.file_path)
    thumb_dir = storage.creative_dir(user.id) / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    dest = thumb_dir / f"{asset.id}.jpg"
    try:
        if asset.file_type == "image":
            from app.services.creative import image_engine
            img = image_engine.open_image(path)
            image_engine.export(image_engine.thumbnail(img, (480, 480)), dest, fmt="jpg")
        else:
            media_engine.make_thumbnail(path, dest)
    except CreativeEngineError as exc:
        from app.api.creative._helpers import raise_http
        raise_http(exc)
    rel = dest.relative_to(storage.artifacts.user_upload_dir(user.id)).as_posix()
    asset.thumbnail_path = rel
    db.commit()
    return {"success": True, "thumbnail_url": asset_url_inline(asset.id), "path": rel}


def asset_url_inline(asset_id: str) -> str:
    return f"/api/creative/files/{asset_id}?variant=thumb"


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    asset = owned_asset(db, user, asset_id)
    try:
        path = storage.resolve(user.id, asset.file_path)
        if path.exists():
            path.unlink()
    except Exception:
        pass
    db.delete(asset)
    db.commit()
    return {"success": True, "message": "Asset deleted."}
