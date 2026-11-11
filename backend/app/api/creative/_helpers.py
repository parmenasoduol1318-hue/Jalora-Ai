"""Shared helpers for the Creative Studio API: ownership checks, serialisers,
CreativeEngineError -> HTTP mapping and provider fallback."""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.database.session import get_db
from app.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models.creative import (
    CreativeProject,
    CreativeProjectShare,
    CreativeProjectVersion,
    GenerationJob,
    MediaAsset,
    RenderJob,
    TimelineClip,
    TimelineTrack,
)
from app.models.user import User
from app.services.creative import CreativeEngineError, storage

_MEDIA_TYPE_MAP = {
    "image": {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
              ".tiff": "image/tiff", ".tif": "image/tiff"},
    "audio": {".mp3": "audio/mpeg", ".wav": "audio/wav", ".aac": "audio/aac",
              ".flac": "audio/flac", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
              ".opus": "audio/opus"},
    "video": {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
              ".mkv": "video/x-matroska", ".m4v": "video/x-m4v", ".avi": "video/x-msvideo"},
}


def require_creative(settings: Settings = Depends(get_settings)) -> Settings:
    if not settings.creative_enabled:
        raise ServiceUnavailableError(
            "Creative Studio is disabled. Set JALORA_CREATIVE_ENABLED=true to enable it.",
            "CREATIVE_DISABLED",
        )
    return settings


def raise_http(exc: CreativeEngineError):
    """Map a CreativeEngineError to the appropriate HTTP JaloraError."""
    code = exc.code or "CREATIVE_ERROR"
    if code == "NOT_FOUND":
        raise NotFoundError(str(exc), code)
    if code.startswith("PROVIDER") or code in (
        "AI_IMAGE_GENERATION_UNAVAILABLE",
        "MODEL_UNAVAILABLE",
        "STT_UNAVAILABLE",
        "STT_MODEL_ERROR",
        "STT_FAILED",
    ):
        raise ServiceUnavailableError(str(exc), code)
    raise BadRequestError(str(exc), code)


def owned_project(db: Session, user: User, project_id: str) -> CreativeProject:
    project = db.get(CreativeProject, project_id)
    if project is None:
        raise NotFoundError("Project not found.", "PROJECT_NOT_FOUND")
    if project.user_id != user.id:
        share = (db.query(CreativeProjectShare)
                 .filter(CreativeProjectShare.project_id == project_id,
                         CreativeProjectShare.shared_with_user_id == user.id)
                 .first())
        if share is None:
            raise ForbiddenError("You do not have access to this project.", "NOT_OWNER")
    return project


def owned_asset(db: Session, user: User, asset_id: str) -> MediaAsset:
    asset = db.get(MediaAsset, asset_id)
    if asset is None:
        raise NotFoundError("Asset not found.", "ASSET_NOT_FOUND")
    if asset.user_id != user.id:
        raise ForbiddenError("You do not own this asset.", "NOT_OWNER")
    return asset


def owned_track(db: Session, user: User, track_id: str) -> TimelineTrack:
    track = db.get(TimelineTrack, track_id)
    if track is None:
        raise NotFoundError("Track not found.", "TRACK_NOT_FOUND")
    owned_project(db, user, track.project_id)
    return track


def owned_clip(db: Session, user: User, clip_id: str) -> TimelineClip:
    clip = db.get(TimelineClip, clip_id)
    if clip is None:
        raise NotFoundError("Clip not found.", "CLIP_NOT_FOUND")
    owned_project(db, user, clip.track.project_id)
    return clip


def persist_bytes(db: Session, user_id: str, data: bytes, filename: str, *,
                  file_type: str, source: str, subdir: str,
                  project_id: str | None = None, metadata: dict | None = None) -> MediaAsset:
    """Write bytes into the user's creative folder and register a MediaAsset."""
    folder = storage.creative_dir(user_id) / subdir
    folder.mkdir(parents=True, exist_ok=True)
    safe = Path(filename or "file").name
    name = f"{uuid.uuid4().hex}{Path(safe).suffix.lower()}"
    path = folder / name
    path.write_bytes(data)
    rel = path.relative_to(storage.artifacts.user_upload_dir(user_id)).as_posix()
    return storage.create_asset(
        db, user_id, rel_path=rel, filename=safe, file_type=file_type,
        project_id=project_id, size_bytes=len(data), source=source,
        metadata_json=metadata or {},
    )


def asset_url(asset_id: str) -> str:
    return f"/api/creative/files/{asset_id}"


def asset_mime(asset: MediaAsset) -> str:
    ext = Path(asset.filename or asset.file_path).suffix.lower()
    if asset.file_type in _MEDIA_TYPE_MAP and ext in _MEDIA_TYPE_MAP[asset.file_type]:
        return _MEDIA_TYPE_MAP[asset.file_type][ext]
    return mimetypes.guess_type(asset.filename or asset.file_path)[0] or "application/octet-stream"


# ------------------------------------------------------------------ serialise

def iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def serialize_asset(asset: MediaAsset, *, with_url: bool = True) -> dict:
    data = {
        "id": asset.id,
        "project_id": asset.project_id,
        "filename": asset.filename,
        "file_type": asset.file_type,
        "source": asset.source,
        "size_bytes": asset.size_bytes,
        "duration": asset.duration,
        "width": asset.width,
        "height": asset.height,
        "thumbnail_url": asset_url(asset.id) if asset.thumbnail_path else None,
        "metadata": asset.metadata_json or {},
        "created_at": iso(asset.created_at),
    }
    if with_url:
        data["url"] = asset_url(asset.id)
        data["file_path"] = asset.file_path
    return data


def serialize_clip(clip: TimelineClip) -> dict:
    return {
        "id": clip.id,
        "track_id": clip.track_id,
        "asset_id": clip.asset_id,
        "clip_type": clip.clip_type,
        "name": clip.name,
        "start_time": clip.start_time,
        "duration": clip.duration,
        "trim_start": clip.trim_start,
        "trim_end": clip.trim_end,
        "speed": clip.speed,
        "opacity": clip.opacity,
        "z_index": clip.z_index,
        "link_group": clip.link_group,
        "transform": clip.transform_json or {},
        "effects": clip.effects_json or {},
        "keyframes": clip.keyframes_json or {},
        "text": clip.text_json or {},
    }


def serialize_track(track: TimelineTrack) -> dict:
    return {
        "id": track.id,
        "track_index": track.track_index,
        "track_type": track.track_type,
        "name": track.name,
        "is_muted": track.is_muted,
        "is_locked": track.is_locked,
        "is_hidden": track.is_hidden,
        "is_solo": track.is_solo,
        "volume": track.volume,
        "clips": [serialize_clip(c) for c in track.clips],
    }


def serialize_project(project: CreativeProject, *, detail: bool = False) -> dict:
    data = {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "project_type": project.project_type,
        "aspect_ratio": project.aspect_ratio,
        "status": project.status,
        "brand_kit_id": project.brand_kit_id,
        "settings": project.settings_json or {},
        "thumbnail_url": project.thumbnail_url,
        "created_at": iso(project.created_at),
        "updated_at": iso(project.updated_at),
    }
    if detail:
        data["tracks"] = [serialize_track(t) for t in project.tracks]
        data["assets"] = [serialize_asset(a) for a in project.assets]
        data["transcripts"] = [
            {"id": ts.id, "asset_id": ts.asset_id, "start_time": ts.start_time,
             "end_time": ts.end_time, "text": ts.text, "speaker": ts.speaker,
             "words": ts.words_json or [], "is_filler": ts.is_filler}
            for ts in project.transcripts
        ]
    return data


def serialize_render_job(job: RenderJob) -> dict:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "kind": job.kind,
        "status": job.status,
        "progress_pct": job.progress_pct,
        "message": job.message,
        "output_asset_id": job.output_asset_id,
        "output_url": asset_url(job.output_asset_id) if job.output_asset_id else job.output_url,
        "error_message": job.error_message,
        "settings": job.settings_json or {},
        "created_at": iso(job.created_at),
        "updated_at": iso(job.updated_at),
    }


def serialize_generation_job(job: GenerationJob) -> dict:
    outputs = []
    for out in job.outputs_json or []:
        asset_id = out.get("asset_id")
        outputs.append({"asset_id": asset_id, "url": asset_url(asset_id) if asset_id else out.get("url"),
                        "meta": out.get("meta", {})})
    return {
        "id": job.id,
        "project_id": job.project_id,
        "kind": job.kind,
        "provider": job.provider,
        "model": job.model,
        "prompt": job.prompt,
        "negative_prompt": job.negative_prompt,
        "params": job.params_json or {},
        "status": job.status,
        "progress_pct": job.progress_pct,
        "message": job.message,
        "outputs": outputs,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "cost_estimate_usd": job.cost_estimate_usd,
        "created_at": iso(job.created_at),
        "updated_at": iso(job.updated_at),
    }


def serialize_version(version: CreativeProjectVersion) -> dict:
    return {
        "id": version.id,
        "project_id": version.project_id,
        "version_no": version.version_no,
        "label": version.label,
        "snapshot": version.snapshot_json or {},
        "created_at": iso(version.created_at),
    }


def serialize_share(share: CreativeProjectShare) -> dict:
    return {
        "id": share.id,
        "project_id": share.project_id,
        "shared_with_user_id": share.shared_with_user_id,
        "shared_with_email": share.shared_with_email,
        "role": share.role,
        "token": share.token,
        "created_at": iso(share.created_at),
        "expires_at": iso(share.expires_at),
    }
