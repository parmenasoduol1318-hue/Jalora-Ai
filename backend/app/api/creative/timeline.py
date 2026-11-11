"""Timeline editing: tracks, clips and bulk timeline save."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.creative._helpers import (
    owned_clip,
    owned_project,
    owned_track,
    require_creative,
    serialize_clip,
    serialize_track,
)
from app.api.creative._schemas import (
    ClipCreateRequest,
    ClipUpdateRequest,
    TimelineSaveRequest,
    TrackCreateRequest,
    TrackUpdateRequest,
)
from app.api.deps import get_current_user
from app.database.session import get_db
from app.errors import BadRequestError, NotFoundError
from app.models.creative import TimelineClip, TimelineTrack
from app.models.user import User

router = APIRouter(dependencies=[Depends(require_creative)])

_DEFAULT_TRANSFORM = {"x": 0, "y": 0, "scale": 1.0, "rotation": 0, "opacity": 1.0, "fit": "cover"}


@router.post("/projects/{project_id}/tracks", status_code=status.HTTP_201_CREATED)
def create_track(project_id: str, req: TrackCreateRequest,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    if req.track_index is None:
        req.track_index = (max([t.track_index for t in project.tracks], default=-1) + 1)
    track = TimelineTrack(
        id=uuid.uuid4().hex, project_id=project_id, track_index=req.track_index,
        track_type=req.track_type or "video",
        name=req.name or f"{req.track_type.title()} {req.track_index + 1}",
    )
    db.add(track)
    db.commit()
    db.refresh(track)
    return {"success": True, "track": serialize_track(track)}


@router.patch("/tracks/{track_id}")
def update_track(track_id: str, req: TrackUpdateRequest,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    track = owned_track(db, user, track_id)
    for key, value in req.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(track, key, value)
    db.commit()
    db.refresh(track)
    return {"success": True, "track": serialize_track(track)}


@router.delete("/tracks/{track_id}")
def delete_track(track_id: str, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    track = owned_track(db, user, track_id)
    db.delete(track)
    db.commit()
    return {"success": True, "message": "Track deleted."}


@router.post("/tracks/{track_id}/clips", status_code=status.HTTP_201_CREATED)
def add_clip(track_id: str, req: ClipCreateRequest, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    track = owned_track(db, user, track_id)
    if req.asset_id:
        from app.api.creative._helpers import owned_asset
        owned_asset(db, user, req.asset_id)
    clip = TimelineClip(
        id=uuid.uuid4().hex, track_id=track_id, asset_id=req.asset_id,
        clip_type=req.clip_type or "media", name=req.name, start_time=req.start_time,
        duration=req.duration, trim_start=req.trim_start, trim_end=req.trim_end,
        speed=req.speed, opacity=req.opacity, z_index=req.z_index, link_group=req.link_group,
        transform_json=req.transform_json or dict(_DEFAULT_TRANSFORM),
        effects_json=req.effects_json or {}, keyframes_json=req.keyframes_json or {},
        text_json=req.text_json or {},
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)
    return {"success": True, "clip": serialize_clip(clip)}


@router.patch("/clips/{clip_id}")
def update_clip(clip_id: str, req: ClipUpdateRequest, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    clip = owned_clip(db, user, clip_id)
    data = req.model_dump(exclude_unset=True)
    if data.get("duration") is not None and data["duration"] <= 0:
        raise BadRequestError("Clip duration must be positive.", "INVALID_DURATION")
    for key, value in data.items():
        if value is not None:
            setattr(clip, key, value)
    db.commit()
    db.refresh(clip)
    return {"success": True, "clip": serialize_clip(clip)}


@router.delete("/clips/{clip_id}")
def delete_clip(clip_id: str, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    clip = owned_clip(db, user, clip_id)
    db.delete(clip)
    db.commit()
    return {"success": True, "message": "Clip deleted."}


@router.put("/projects/{project_id}/timeline")
def save_timeline(project_id: str, req: TimelineSaveRequest,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Atomically replace a project's timeline with the posted tracks+clips.

    Existing tracks/clips are removed and re-created from the payload so the
    editor can persist an entire timeline state in one call.
    """
    project = owned_project(db, user, project_id)
    for track in list(project.tracks):
        db.delete(track)
    db.flush()
    created_tracks: dict[str, str] = {}
    for tdata in req.tracks:
        client_id = tdata.get("id")
        track = TimelineTrack(
            id=uuid.uuid4().hex, project_id=project_id,
            track_index=int(tdata.get("track_index", 0)),
            track_type=tdata.get("track_type", "video"),
            name=tdata.get("name", "Track"),
            is_muted=bool(tdata.get("is_muted", False)),
            is_locked=bool(tdata.get("is_locked", False)),
            is_hidden=bool(tdata.get("is_hidden", False)),
            is_solo=bool(tdata.get("is_solo", False)),
            volume=float(tdata.get("volume", 1.0)),
        )
        db.add(track)
        db.flush()
        created_tracks[client_id] = track.id
        for cdata in tdata.get("clips", []):
            db.add(_clip_from_payload(track.id, cdata))
    # clips posted at the top level (with track_id reference)
    for cdata in req.clips:
        track_id = created_tracks.get(cdata.get("track_id"), cdata.get("track_id"))
        if track_id and db.get(TimelineTrack, track_id):
            db.add(_clip_from_payload(track_id, cdata))
    db.commit()
    db.refresh(project)
    from app.api.creative._helpers import serialize_project
    return {"success": True, "project": serialize_project(project, detail=True)}


def _clip_from_payload(track_id: str, cdata: dict) -> TimelineClip:
    return TimelineClip(
        id=uuid.uuid4().hex, track_id=track_id, asset_id=cdata.get("asset_id"),
        clip_type=cdata.get("clip_type", "media"), name=cdata.get("name"),
        start_time=float(cdata.get("start_time", 0.0)),
        duration=float(cdata.get("duration", 5.0)),
        trim_start=float(cdata.get("trim_start", 0.0)),
        trim_end=float(cdata.get("trim_end", 5.0)),
        speed=float(cdata.get("speed", 1.0)), opacity=float(cdata.get("opacity", 1.0)),
        z_index=int(cdata.get("z_index", 0)), link_group=cdata.get("link_group"),
        transform_json=cdata.get("transform_json") or cdata.get("transform") or dict(_DEFAULT_TRANSFORM),
        effects_json=cdata.get("effects_json") or cdata.get("effects") or {},
        keyframes_json=cdata.get("keyframes_json") or cdata.get("keyframes") or {},
        text_json=cdata.get("text_json") or cdata.get("text") or {},
    )
