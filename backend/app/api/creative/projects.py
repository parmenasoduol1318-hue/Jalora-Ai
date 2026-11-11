"""Projects, versions and collaboration shares."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.creative._helpers import (
    iso,
    owned_asset,
    owned_project,
    require_creative,
    serialize_project,
    serialize_share,
    serialize_version,
)
from app.api.creative._schemas import (
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ShareCreateRequest,
    VersionCreateRequest,
)
from app.api.deps import get_current_user
from app.database.session import get_db
from app.errors import BadRequestError, NotFoundError
from app.models.creative import (
    CreativeProject,
    CreativeProjectShare,
    CreativeProjectVersion,
    TimelineTrack,
)
from app.models.user import User

router = APIRouter(dependencies=[Depends(require_creative)])


def _default_tracks(db: Session, project_id: str) -> None:
    defaults = [
        (0, "video", "Video 1"),
        (1, "audio", "Audio 1"),
        (2, "text", "Subtitles 1"),
    ]
    for idx, ttype, name in defaults:
        db.add(TimelineTrack(id=uuid.uuid4().hex, project_id=project_id,
                             track_index=idx, track_type=ttype, name=name))


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    projects = (db.query(CreativeProject)
                .filter(CreativeProject.user_id == user.id)
                .order_by(CreativeProject.updated_at.desc())
                .all())
    return {"success": True, "projects": [serialize_project(p) for p in projects]}


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(req: ProjectCreateRequest, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    settings_json = dict(req.settings_json or {})
    settings_json.setdefault("width", None)
    settings_json.setdefault("height", None)
    project = CreativeProject(
        id=uuid.uuid4().hex,
        user_id=user.id,
        title=req.title,
        description=req.description,
        project_type=req.project_type or "video",
        aspect_ratio=req.aspect_ratio or "16:9",
        brand_kit_id=req.brand_kit_id,
        settings_json=settings_json,
    )
    db.add(project)
    _default_tracks(db, project.id)
    db.commit()
    db.refresh(project)
    return {"success": True, "project": serialize_project(project, detail=True)}


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    return {"success": True, "project": serialize_project(project, detail=True)}


@router.patch("/projects/{project_id}")
def update_project(project_id: str, req: ProjectUpdateRequest,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    data = req.model_dump(exclude_unset=True)
    if "thumbnail_asset_id" in data:
        asset_id = data.pop("thumbnail_asset_id")
        if asset_id:
            owned_asset(db, user, asset_id)
            project.thumbnail_url = f"/api/creative/files/{asset_id}"
        else:
            project.thumbnail_url = None
    if "settings_json" in data and data["settings_json"] is not None:
        project.settings_json = data.pop("settings_json")
    for key, value in data.items():
        if value is not None:
            setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return {"success": True, "project": serialize_project(project, detail=True)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    if project.user_id != user.id:
        raise BadRequestError("Only the owner can delete a project.", "NOT_OWNER")
    db.delete(project)
    db.commit()
    return {"success": True, "message": "Project deleted."}


@router.post("/projects/{project_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_project(project_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    source = owned_project(db, user, project_id)
    clone = CreativeProject(
        id=uuid.uuid4().hex, user_id=user.id, title=f"{source.title} (copy)",
        description=source.description, project_type=source.project_type,
        aspect_ratio=source.aspect_ratio, brand_kit_id=source.brand_kit_id,
        settings_json=dict(source.settings_json or {}),
    )
    db.add(clone)
    for track in source.tracks:
        new_track = TimelineTrack(
            id=uuid.uuid4().hex, project_id=clone.id, track_index=track.track_index,
            track_type=track.track_type, name=track.name, is_muted=track.is_muted,
            is_locked=track.is_locked, is_hidden=track.is_hidden, volume=track.volume,
        )
        db.add(new_track)
        for clip in track.clips:
            from app.models.creative import TimelineClip
            db.add(TimelineClip(
                id=uuid.uuid4().hex, track_id=new_track.id, asset_id=clip.asset_id,
                clip_type=clip.clip_type, name=clip.name, start_time=clip.start_time,
                duration=clip.duration, trim_start=clip.trim_start, trim_end=clip.trim_end,
                speed=clip.speed, opacity=clip.opacity, z_index=clip.z_index,
                transform_json=dict(clip.transform_json or {}),
                effects_json=dict(clip.effects_json or {}),
                keyframes_json=dict(clip.keyframes_json or {}),
                text_json=dict(clip.text_json or {}),
            ))
    db.commit()
    db.refresh(clone)
    return {"success": True, "project": serialize_project(clone, detail=True)}


# --------------------------------------------------------------- versions

@router.get("/projects/{project_id}/versions")
def list_versions(project_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    owned_project(db, user, project_id)
    versions = (db.query(CreativeProjectVersion)
                .filter(CreativeProjectVersion.project_id == project_id)
                .order_by(CreativeProjectVersion.version_no.desc()).all())
    return {"success": True, "versions": [serialize_version(v) for v in versions]}


@router.post("/projects/{project_id}/versions", status_code=status.HTTP_201_CREATED)
def create_version(project_id: str, req: VersionCreateRequest,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    last = (db.query(CreativeProjectVersion)
            .filter(CreativeProjectVersion.project_id == project_id)
            .order_by(CreativeProjectVersion.version_no.desc()).first())
    snapshot = serialize_project(project, detail=True)
    version = CreativeProjectVersion(
        id=uuid.uuid4().hex, project_id=project_id, user_id=user.id,
        version_no=(last.version_no + 1 if last else 1),
        label=req.label, snapshot_json=snapshot,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return {"success": True, "version": serialize_version(version)}


@router.post("/projects/{project_id}/versions/{version_id}/restore")
def restore_version(project_id: str, version_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    version = db.get(CreativeProjectVersion, version_id)
    if version is None or version.project_id != project_id:
        raise NotFoundError("Version not found.", "VERSION_NOT_FOUND")
    snapshot = version.snapshot_json or {}
    project.title = snapshot.get("title", project.title)
    project.aspect_ratio = snapshot.get("aspect_ratio", project.aspect_ratio)
    project.settings_json = snapshot.get("settings", project.settings_json)
    # rebuild tracks/clips from the snapshot
    from app.models.creative import TimelineClip
    for track in list(project.tracks):
        db.delete(track)
    db.flush()
    for tdata in snapshot.get("tracks", []):
        track = TimelineTrack(
            id=uuid.uuid4().hex, project_id=project.id,
            track_index=tdata.get("track_index", 0), track_type=tdata.get("track_type", "video"),
            name=tdata.get("name", "Track"), is_muted=tdata.get("is_muted", False),
            is_locked=tdata.get("is_locked", False), is_hidden=tdata.get("is_hidden", False),
            volume=tdata.get("volume", 1.0),
        )
        db.add(track)
        db.flush()
        for cdata in tdata.get("clips", []):
            db.add(TimelineClip(
                id=uuid.uuid4().hex, track_id=track.id, asset_id=cdata.get("asset_id"),
                clip_type=cdata.get("clip_type", "media"), name=cdata.get("name"),
                start_time=cdata.get("start_time", 0.0), duration=cdata.get("duration", 5.0),
                trim_start=cdata.get("trim_start", 0.0), trim_end=cdata.get("trim_end", 5.0),
                speed=cdata.get("speed", 1.0), opacity=cdata.get("opacity", 1.0),
                z_index=cdata.get("z_index", 0),
                transform_json=cdata.get("transform", {}), effects_json=cdata.get("effects", {}),
                keyframes_json=cdata.get("keyframes", {}), text_json=cdata.get("text", {}),
            ))
    db.commit()
    db.refresh(project)
    return {"success": True, "project": serialize_project(project, detail=True)}


# ----------------------------------------------------------------- shares

@router.get("/projects/{project_id}/shares")
def list_shares(project_id: str, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    if project.user_id != user.id:
        raise BadRequestError("Only the owner can view shares.", "NOT_OWNER")
    shares = (db.query(CreativeProjectShare)
              .filter(CreativeProjectShare.project_id == project_id).all())
    return {"success": True, "shares": [serialize_share(s) for s in shares]}


@router.post("/projects/{project_id}/shares", status_code=status.HTTP_201_CREATED)
def create_share(project_id: str, req: ShareCreateRequest,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    if project.user_id != user.id:
        raise BadRequestError("Only the owner can share a project.", "NOT_OWNER")
    expires = None
    if req.expires_in_days:
        expires = datetime.utcnow() + timedelta(days=req.expires_in_days)
    share = CreativeProjectShare(
        id=uuid.uuid4().hex, project_id=project_id, owner_id=user.id,
        shared_with_email=req.shared_with_email, role=req.role or "viewer",
        token=secrets.token_urlsafe(24), expires_at=expires,
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return {"success": True, "share": serialize_share(share)}


@router.delete("/projects/{project_id}/shares/{share_id}")
def delete_share(project_id: str, share_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    if project.user_id != user.id:
        raise BadRequestError("Only the owner can remove shares.", "NOT_OWNER")
    share = db.get(CreativeProjectShare, share_id)
    if share is None or share.project_id != project_id:
        raise NotFoundError("Share not found.", "SHARE_NOT_FOUND")
    db.delete(share)
    db.commit()
    return {"success": True, "message": "Share removed."}
