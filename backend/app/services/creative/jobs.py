"""Background job runner for the Creative Studio.

A bounded thread pool executes generation and render work off the request thread.
Job progress, result and failure state are persisted on the ``GenerationJob`` and
``RenderJob`` rows so the UI can poll them; an in-memory registry tracks live
tasks for cancellation. Each task uses its own DB session (SQLAlchemy sessions
are not thread-safe).
"""
from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import Settings, get_settings
from app.database.session import SessionLocal
from app.services.creative import CreativeEngineError, providers
from app.services.creative.providers import FALLBACK_CODES as _FALLBACK_CODES

logger = logging.getLogger("jalora.creative.jobs")


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.cancel_event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancel(self) -> None:
        if self.cancelled:
            raise CreativeEngineError("Job cancelled.", "JOB_CANCELLED")


_REGISTRY: dict[str, JobContext] = {}
_REGISTRY_LOCK = threading.Lock()
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            concurrency = max(1, int(get_settings().creative_jobs_concurrency))
            _EXECUTOR = ThreadPoolExecutor(max_workers=concurrency,
                                           thread_name_prefix="jalora-creative")
        return _EXECUTOR


def cancel(job_id: str) -> bool:
    ctx = _REGISTRY.get(job_id)
    if ctx:
        ctx.cancel_event.set()
        return True
    return False


def is_running(job_id: str) -> bool:
    return job_id in _REGISTRY


def active_jobs() -> list[str]:
    return list(_REGISTRY.keys())


def _update(db, model_cls, job_id: str, **fields) -> None:
    row = db.get(model_cls, job_id)
    if row is None:
        return
    for k, v in fields.items():
        setattr(row, k, v)
    db.commit()


def _wrap(job_id: str, model_cls, target) -> None:
    ctx = JobContext(job_id)
    with _REGISTRY_LOCK:
        _REGISTRY[job_id] = ctx
    db = SessionLocal()
    try:
        _update(db, model_cls, job_id, status="running", progress_pct=0.0)
        target(ctx, db)
        if not ctx.cancelled:
            _update(db, model_cls, job_id, status="completed", progress_pct=100.0,
                       message="Complete")
    except CreativeEngineError as exc:
        status = "cancelled" if exc.code == "JOB_CANCELLED" else "failed"
        _update(db, model_cls, job_id, status=status, error_message=str(exc), message=str(exc), progress_pct=100.0)
    except Exception as exc:
        logger.exception("creative job %s failed", job_id)
        _update(db, model_cls, job_id, status="failed", error_message=str(exc), message=str(exc))
    finally:
        with _REGISTRY_LOCK:
            _REGISTRY.pop(job_id, None)
        db.close()


def _progress_callback(job_id: str, model):
    state = {"last": 0.0}

    def cb(pct: float, message: str | None = None) -> None:
        pct = max(0.0, min(100.0, pct * 100.0 if pct <= 1.0 else pct))
        if pct - state["last"] < 2.0 and pct < 100.0:
            return
        state["last"] = pct
        db = SessionLocal()
        try:
            _update(db, model, job_id, progress_pct=pct, message=message)
        finally:
            db.close()

    return cb


# --------------------------------------------------------------- generation

def submit_generation(generation_job_id: str, request: dict) -> None:
    from app.models.creative import GenerationJob
    from app.services.creative import storage

    def target(ctx: JobContext, db) -> None:
        job = db.get(GenerationJob, generation_job_id)
        if job is None:
            raise CreativeEngineError("Generation job not found.", "JOB_MISSING")
        settings = get_settings()
        req = providers.GenRequest(
            kind=job.kind,
            prompt=job.prompt or "",
            negative_prompt=job.negative_prompt,
            aspect_ratio=request.get("aspect_ratio", "16:9"),
            duration=float(request.get("duration", 5.0)),
            seed=request.get("seed"),
            num_variations=int(request.get("num_variations", 1)),
            reference_image=request.get("reference_image"),
            mask_image=request.get("mask_image"),
            voice=request.get("voice"),
            model=job.model,
        )
        provider = providers.resolve_provider(job.kind, request.get("provider"), settings)
        _update(db, GenerationJob, generation_job_id, provider=provider.name,
                model=job.model or request.get("model"))
        progress = _progress_callback(generation_job_id, GenerationJob)

        def provider_progress(p):
            ctx.check_cancel()
            progress(p, "generating")

        if hasattr(provider, "ai") and provider.ai is False:
            progress(0.1, "running local engine (not AI)")
        try:
            if job.kind == "video":
                outputs = provider.generate(req, progress=provider_progress)
            else:
                outputs = provider.generate(req)
        except TypeError:
            outputs = provider.generate(req)
        except CreativeEngineError as exc:
            # Mirror the fallback logic in _sync_generate / creative_tools:
            # if the cloud provider failed with a recoverable error code,
            # retry on the local (offline) engine instead of failing the job.
            if exc.code not in _FALLBACK_CODES:
                logger.warning("Generation job %s failed irrecoverably: %s [%s]",
                               generation_job_id, exc, exc.code)
                raise
            progress(0.15, f"cloud provider failed ({exc.code}), falling back to local engine")
            logger.info("Generation job %s: falling back to local provider after %s [%s]",
                        generation_job_id, exc.provider, exc.code)
            local = providers.resolve_provider(job.kind, "local", settings)
            if job.kind == "video":
                outputs = local.generate(req, progress=provider_progress)
            else:
                outputs = local.generate(req)
            _update(db, GenerationJob, generation_job_id, provider=local.name)
        ctx.check_cancel()

        records = []
        out_dir = storage.creative_dir(job.user_id)
        from app.models.creative import MediaAsset
        for i, out in enumerate(outputs):
            name = f"{job.kind}_{generation_job_id[:8]}_{i}{out.extension}"
            path = Path(out_dir) / "generated" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(out.data)
            meta = dict(out.meta)
            meta["generation_job_id"] = generation_job_id
            rel = str(Path("creative") / "generated" / name).replace("\\", "/")
            asset = storage.create_asset(
                db, job.user_id,
                filename=name,
                rel_path=rel,
                file_type=job.kind,
                source="generated",
                size_bytes=len(out.data),
                metadata_json=meta,
                project_id=job.project_id,
            )
            records.append({"asset_id": asset.id, "url": asset.file_path, "meta": meta})
        _update(db, GenerationJob, generation_job_id, outputs_json=records,
                progress_pct=100.0, status="completed")

    _executor().submit(_wrap, generation_job_id, GenerationJob, target)


# ------------------------------------------------------------------- render

def submit_render(render_job_id: str) -> None:
    from app.models.creative import RenderJob, MediaAsset
    from app.services.creative import render as render_mod, storage

    def target(ctx: JobContext, db) -> None:
        job = db.get(RenderJob, render_job_id)
        if job is None:
            raise CreativeEngineError("Render job not found.", "JOB_MISSING")
        settings = get_settings()
        progress = _progress_callback(render_job_id, RenderJob)

        def report(p, msg=None):
            ctx.check_cancel()
            progress(p, msg or "rendering")

        out_path = render_mod.render_project(
            db, user_id=job.user_id, project_id=job.project_id,
            render_job_id=render_job_id, settings=settings, progress=report,
            kind=job.kind, options=job.settings_json or {},
        )
        ctx.check_cancel()
        size = out_path.stat().st_size if Path(out_path).exists() else 0
        rel = str(Path("creative") / "renders" / Path(out_path).name).replace("\\", "/")
        asset = storage.create_asset(
            db, job.user_id,
            filename=Path(out_path).name,
            rel_path=rel,
            file_type="video" if job.kind == "video" else job.kind,
            source="render",
            size_bytes=size,
            metadata_json={"render_job_id": render_job_id},
            project_id=job.project_id,
        )
        _update(db, RenderJob, render_job_id, output_asset_id=asset.id,
                output_url=asset.file_path, progress_pct=100.0, status="completed")

    _executor().submit(_wrap, render_job_id, RenderJob, target)


def new_id() -> str:
    return str(uuid.uuid4())
