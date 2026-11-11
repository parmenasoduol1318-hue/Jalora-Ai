"""Render/export endpoints and render-job tracking."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.creative._helpers import (
    owned_project,
    require_creative,
    serialize_render_job,
)
from app.api.creative._schemas import RenderRequest
from app.api.deps import get_current_user
from app.database.session import get_db
from app.errors import NotFoundError
from app.models.creative import RenderJob
from app.models.user import User
from app.services.creative import jobs

router = APIRouter(dependencies=[Depends(require_creative)])

EXPORT_PRESETS = {
    "social": {
        "youtube_1080p": {"aspect_ratio": "16:9", "resolution": "1080p", "fps": 30, "kind": "video"},
        "youtube_4k": {"aspect_ratio": "16:9", "resolution": "4k", "fps": 30, "kind": "video"},
        "instagram_reel": {"aspect_ratio": "9:16", "resolution": "1080p", "fps": 30, "kind": "video"},
        "instagram_square": {"aspect_ratio": "1:1", "resolution": "1080p", "fps": 30, "kind": "video"},
        "tiktok": {"aspect_ratio": "9:16", "resolution": "1080p", "fps": 30, "kind": "video"},
        "twitter": {"aspect_ratio": "16:9", "resolution": "720p", "fps": 30, "kind": "video"},
    },
    "audio": {
        "audio_wav": {"kind": "audio", "format": "wav"},
        "audio_mp3": {"kind": "audio", "format": "mp3"},
    },
    "still": {
        "poster_png": {"kind": "image", "format": "png"},
        "thumbnail_jpg": {"kind": "image", "format": "jpg"},
    },
    "subtitles": {
        "srt": {"kind": "subtitle", "format": "srt"},
        "vtt": {"kind": "subtitle", "format": "vtt"},
    },
}


@router.get("/export-presets")
def export_presets():
    return {"success": True, "presets": EXPORT_PRESETS}


@router.post("/render", status_code=status.HTTP_202_ACCEPTED)
def create_render(req: RenderRequest, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    project = owned_project(db, user, req.project_id)
    options = dict(req.options or {})
    kind = req.kind or "video"
    job = RenderJob(
        id=uuid.uuid4().hex, user_id=user.id, project_id=project.id, kind=kind,
        status="queued", settings_json=options,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    jobs.submit_render(job.id)
    return {"success": True, "render_job": serialize_render_job(job)}


@router.get("/render-jobs")
def list_render_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                     project_id: str | None = None, limit: int = 100):
    query = db.query(RenderJob).filter(RenderJob.user_id == user.id)
    if project_id:
        query = query.filter(RenderJob.project_id == project_id)
    job_rows = query.order_by(RenderJob.created_at.desc()).limit(min(limit, 500)).all()
    return {"success": True, "render_jobs": [serialize_render_job(j) for j in job_rows]}


@router.get("/render-jobs/{job_id}")
def get_render_job(job_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    job = db.get(RenderJob, job_id)
    if job is None or job.user_id != user.id:
        raise NotFoundError("Render job not found.", "JOB_NOT_FOUND")
    return {"success": True, "render_job": serialize_render_job(job)}


@router.post("/render-jobs/{job_id}/cancel")
def cancel_render_job(job_id: str, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    job = db.get(RenderJob, job_id)
    if job is None or job.user_id != user.id:
        raise NotFoundError("Render job not found.", "JOB_NOT_FOUND")
    cancelled = jobs.cancel(job_id)
    if job.status in ("queued", "running"):
        job.status = "cancelled"
        job.message = "Cancellation requested."
        db.commit()
    return {"success": True, "cancelled": cancelled, "status": job.status}
