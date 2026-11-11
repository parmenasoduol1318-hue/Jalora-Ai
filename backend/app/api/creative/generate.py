"""AI generation endpoints: capabilities, sync image/audio, async video/jobs."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.creative._helpers import (
    owned_asset,
    owned_project,
    persist_bytes,
    require_creative,
    serialize_asset,
    serialize_generation_job,
)
from app.api.creative._schemas import (
    AudioGenerationRequest,
    ImageGenerationRequest,
    VideoGenerationRequest,
)
from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.database.session import get_db
from app.errors import BadRequestError, NotFoundError, TooManyRequestsError
from app.models.creative import GenerationJob
from app.models.user import User
from app.services.creative import CreativeEngineError, image_engine, jobs, providers, storage

router = APIRouter(dependencies=[Depends(require_creative)])

# Error codes that trigger a fallback to the local engine.
# Imported from providers to keep a single source of truth.
from app.services.creative.providers import FALLBACK_CODES as _FALLBACK_CODES

# In-memory per-user rate-limit windows for image generation.
# (Production with multiple workers should back this with Redis.)
_image_rate_buckets: dict[str, list[float]] = defaultdict(list)


@router.get("/capabilities")
def capabilities(settings: Settings = Depends(get_settings)):
    registry = providers.capability_registry(settings)
    defaults = {
        "image": providers.resolve_provider("image", None, settings).name,
        "video": providers.resolve_provider("video", None, settings).name,
        "audio": providers.resolve_provider("audio", None, settings).name,
    }
    return {
        "success": True,
        "enabled": settings.creative_enabled,
        "providers": registry,
        "defaults": defaults,
        "limits": {
            "max_upload_mb": settings.creative_max_upload_mb,
            "max_render_seconds": settings.creative_max_render_seconds,
            "jobs_concurrency": settings.creative_jobs_concurrency,
            "stt_model": settings.creative_stt_model,
            "image_rate_limit_per_min": settings.image_rate_limit_per_min,
            "image_cost_cap_usd": settings.image_cost_cap_usd,
        },
        "filters": image_engine.list_filters(),
    }


def _resolve_ref(db, user, asset_id):
    if not asset_id:
        return None
    asset = owned_asset(db, user, asset_id)
    return storage.resolve(user.id, asset.file_path)


def _coerce_seed(seed_val) -> int | None:
    """Convert a seed from the API (str/int/None) to a proper int or None."""
    if seed_val is None:
        return None
    try:
        s = int(seed_val)
        if s == 0:
            return None  # 0 means "random" in most UIs
        return s
    except (ValueError, TypeError):
        return None


def _build_extra(req) -> dict:
    """Build the GenRequest.extra dict from schema fields not in GenRequest."""
    extra: dict = {}
    strength = getattr(req, "strength", None)
    if strength is not None:
        try:
            extra["strength"] = float(strength)
        except (ValueError, TypeError):
            pass
    image_type = getattr(req, "image_type", None)
    if image_type:
        extra["image_type"] = image_type
    if getattr(req, "remove_background", False):
        extra["remove_background"] = True
    return extra


def _sync_generate(kind: str, req, db, user, settings, *, project_id, reference=None, mask=None):
    """Run a provider synchronously with an honest local fallback on cloud errors."""
    gen_request = providers.GenRequest(
        kind=kind, prompt=str(getattr(req, "prompt") or ""),
        negative_prompt=str(getattr(req, "negative_prompt") or "") if getattr(req, "negative_prompt") else None,
        aspect_ratio=str(getattr(req, "aspect_ratio") or "16:9"),
        duration=float(getattr(req, "duration") or 5.0) if kind in ("video", "audio") else 5.0,
        seed=_coerce_seed(getattr(req, "seed", None)),
        num_variations=int(getattr(req, "num_variations") or 1),
        reference_image=reference, mask_image=mask, voice=getattr(req, "voice", None),
        model=getattr(req, "model", None),
        extra=_build_extra(req),
    )
    if kind == "image":
        try:
            outputs, provider, fallback = providers.generate_ai_image(
                gen_request, getattr(req, "provider", "auto"), settings
            )
        except CreativeEngineError as exc:
            from app.api.creative._helpers import raise_http
            raise_http(exc)
    else:
        provider = providers.resolve_provider(
            kind, getattr(req, "provider", "auto"), settings
        )
        fallback = None
        try:
            outputs = provider.generate(gen_request)
        except CreativeEngineError as exc:
            if exc.code not in _FALLBACK_CODES:
                from app.api.creative._helpers import raise_http
                raise_http(exc)
            local = providers.resolve_provider(kind, "local", settings)
            outputs = local.generate(gen_request)
            provider = local
            fallback = {
                "reason": exc.code,
                "message": str(exc),
                "from": exc.provider,
            }

    # Post-process: background removal if requested
    if kind == "image" and getattr(req, "remove_background", False):
        from app.services.creative import model_registry
        if model_registry.can_load_rembg():
            local_prov = providers.LocalImageProvider()
            for i, out in enumerate(outputs):
                removed = local_prov.remove_background(out.data)
                if removed is not None:
                    outputs[i] = providers.GenOutput(
                        removed, ".png", "image/png",
                        dict(out.meta, transparent_bg=True),
                    )

    assets = []
    for i, out in enumerate(outputs):
        meta = dict(out.meta)
        if fallback:
            meta["fallback"] = fallback
        asset = persist_bytes(
            db, user.id, out.data,
            f"gen_{kind}_{uuid.uuid4().hex[:8]}_{i}{out.extension}",
            file_type="image" if kind == "image" else ("video" if kind == "video" else "audio"),
            source="generated", subdir="generated", project_id=project_id, metadata=meta,
        )
        assets.append(serialize_asset(asset))
    return {"success": True, "provider": provider.name, "ai": provider.ai,
            "fallback": fallback, "assets": assets, "asset": assets[0] if assets else None}


def _check_image_rate_limit(user: User, settings: Settings):
    """Throttle image-generation calls per user to prevent abuse.

    Uses a simple sliding-window counter. Production deployments with
    multiple workers should replace this with a Redis-backed limiter.
    """
    if settings.image_rate_limit_per_min <= 0:
        return
    now = time.time()
    window = 60.0
    user_id = user.email or user.id or "unknown"
    bucket = _image_rate_buckets[user_id]
    # Prune timestamps older than the window
    cutoff = now - window
    bucket[:] = [ts for ts in bucket if ts > cutoff]
    if len(bucket) >= settings.image_rate_limit_per_min:
        raise TooManyRequestsError(
            f"Rate limit exceeded: {settings.image_rate_limit_per_min} image "
            f"generations per minute. Please reduce the number of variations "
            f"or try again later.",
            "RATE_LIMITED",
        )
    bucket.append(now)


@router.post("/generate/image")
def generate_image(req: ImageGenerationRequest, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user),
                   settings: Settings = Depends(get_settings)):
    _check_image_rate_limit(user, settings)
    if req.project_id:
        owned_project(db, user, req.project_id)
    ref = _resolve_ref(db, user, req.reference_asset_id)
    mask = _resolve_ref(db, user, req.mask_asset_id)
    return _sync_generate("image", req, db, user, settings,
                          project_id=req.project_id, reference=ref, mask=mask)


@router.post("/generate/audio")
def generate_audio(req: AudioGenerationRequest, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user),
                   settings: Settings = Depends(get_settings)):
    if req.project_id:
        owned_project(db, user, req.project_id)
    return _sync_generate("audio", req, db, user, settings, project_id=req.project_id)


@router.post("/generate/video", status_code=status.HTTP_202_ACCEPTED)
def generate_video(req: VideoGenerationRequest, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user),
                   settings: Settings = Depends(get_settings)):
    if req.project_id:
        owned_project(db, user, req.project_id)
    ref = _resolve_ref(db, user, req.reference_asset_id)
    provider = providers.resolve_provider("video", req.provider, settings)
    gen_request = providers.GenRequest(
        kind="video", prompt=req.prompt, negative_prompt=req.negative_prompt,
        aspect_ratio=req.aspect_ratio, duration=req.duration, seed=req.seed,
        reference_image=ref, model=req.model,
    )
    cost = None
    try:
        cost = provider.estimate_cost(gen_request)
    except Exception:
        pass
    job = GenerationJob(
        id=uuid.uuid4().hex, user_id=user.id, project_id=req.project_id, kind="video",
        provider=provider.name, model=req.model, prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        params_json={"aspect_ratio": req.aspect_ratio, "duration": req.duration,
                     "seed": req.seed, "reference_asset_id": req.reference_asset_id},
        cost_estimate_usd=cost, status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    jobs.submit_generation(job.id, {
        "aspect_ratio": req.aspect_ratio, "duration": req.duration, "seed": req.seed,
        "provider": req.provider, "model": req.model, "reference_image": ref,
    })
    return {"success": True, "job": serialize_generation_job(job)}


@router.post("/generate/estimate")
def estimate(payload: dict, settings: Settings = Depends(get_settings)):
    kind = payload.get("kind", "image")
    gen_request = providers.GenRequest(
        kind=kind, prompt=payload.get("prompt", ""),
        aspect_ratio=payload.get("aspect_ratio", "16:9"),
        duration=float(payload.get("duration", 5.0)),
        num_variations=int(payload.get("num_variations", 1)),
    )
    provider = providers.resolve_provider(kind, payload.get("provider", "auto"), settings)
    try:
        cost = provider.estimate_cost(gen_request)
    except Exception:
        cost = None
    return {"success": True, "provider": provider.name, "ai": provider.ai,
            "cost_estimate_usd": cost}


@router.get("/generation-jobs")
def list_generation_jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                         project_id: str | None = None, limit: int = 100):
    query = db.query(GenerationJob).filter(GenerationJob.user_id == user.id)
    if project_id:
        query = query.filter(GenerationJob.project_id == project_id)
    job_rows = query.order_by(GenerationJob.created_at.desc()).limit(min(limit, 500)).all()
    return {"success": True, "jobs": [serialize_generation_job(j) for j in job_rows]}


@router.get("/generation-jobs/{job_id}")
def get_generation_job(job_id: str, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    job = db.get(GenerationJob, job_id)
    if job is None or job.user_id != user.id:
        raise NotFoundError("Generation job not found.", "JOB_NOT_FOUND")
    return {"success": True, "job": serialize_generation_job(job)}


@router.post("/generation-jobs/{job_id}/cancel")
def cancel_generation_job(job_id: str, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    job = db.get(GenerationJob, job_id)
    if job is None or job.user_id != user.id:
        raise NotFoundError("Generation job not found.", "JOB_NOT_FOUND")
    cancelled = jobs.cancel(job_id)
    if job.status in ("queued", "running"):
        job.status = "cancelled"
        job.message = "Cancellation requested."
        db.commit()
    return {"success": True, "cancelled": cancelled, "status": job.status}
