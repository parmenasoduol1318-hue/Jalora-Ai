"""Jalora AI — FastAPI application factory."""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.config import get_settings, validate_settings
from app.errors import JaloraError, ForbiddenError, api_error_handler, unhandled_error_handler
from app.services import metrics as metrics_service
from app.services.rate_limiter import check_rate_limit, get_rate_limiter
from app.services.tracing import init_tracing
# Import provider extensions early — this monkey-patches build_provider_specs
# and AUTO_PRIORITY in model_router.py (restricted) to register LMStudio,
# Jan.ai, Cohere, Cerebras, and Perplexity as routing candidates. Must happen
# before any ModelRouter singleton is created.
from app.services import provider_extensions  # noqa: F401
from app.startup import startup_warnings

logging.basicConfig(
    level=os.environ.get("JALORA_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("jalora")

# ---- static frontend: serve the React build from frontend-react/dist ------
REACT_DIST = Path(__file__).resolve().parents[2] / "frontend-react" / "dist"


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        from app.database.session import init_db
        from app.tools import registry  # noqa: F401 - registers built-in tools
        from app.services import scheduler as scheduler_service
        from app.services.ollama_memory import get_monitor as get_ollama_monitor

        init_db()

        scheduler_service.start_scheduler()
        # Memory-aware Ollama monitor: refreshes the model/RAM snapshot and
        # unloads heavy local models when free RAM is running low (skipped in
        # tests where local Ollama is normally absent).
        if settings.env.lower() != "test":
            get_ollama_monitor().start()
        init_tracing(settings)
        warnings = validate_settings(settings)
        startup_warnings.clear()
        startup_warnings.extend(warnings)
        logger.info(
            "Jalora started | env=%s | tools=%d | scheduler=%s | warnings=%d",
            settings.env, len(registry.all()),
            scheduler_service.is_running(), len(warnings),
        )
        yield
        await get_ollama_monitor().stop()
        scheduler_service.stop_scheduler()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Multimodal, memory-enabled, tool-using AI assistant.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(JaloraError, api_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(api_router)

    # ---- global rate-limit middleware (Phase L, sec 74) ---------------------
    # Applies a per-IP budget to all /api/ routes.  Uses Redis when
    # JALORA_REDIS_URL is set (multi-worker safe); falls back to in-memory.
    _rate_limiter = get_rate_limiter(settings.redis_url)

    @app.middleware("http")
    async def _global_rate_limit_middleware(request: Request, call_next):
        # Read-only list/read endpoints the UI polls on load are cheap (DB list
        # queries) and must never be starved by the shared per-IP budget — this
        # is the root cause of the "sometimes blank" sidebar / schedules /
        # workspace symptoms (a 429 was rendered as an empty state on a shared
        # IP). Only GET reads are whitelisted; writes stay rate-limited.
        _READ_LIST_PREFIXES = (
            "/api/conversations", "/api/schedules", "/api/tasks",
            "/api/workspace", "/api/memory",
            "/api/notifications/unread-count", "/api/approvals",
            "/api/devices/actions", "/api/devices/remote-commands/target",
            "/api/health",
        )
        if (request.method == "GET"
                and request.url.path.startswith(_READ_LIST_PREFIXES)):
            return await call_next(request)
        if settings.rate_limit_global_per_min > 0 and request.url.path.startswith("/api/"):
            client_ip = request.client.host if request.client else "unknown"
            # Also factor in authenticated user ID if available.
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                try:
                    from app.auth.security import decode_access_token
                    payload = decode_access_token(auth_header.removeprefix("Bearer ").strip())
                    client_ip = f"{client_ip}:{payload.get('sub', '')}"
                except Exception:
                    pass
            key = f"global:{client_ip}"
            allowed = check_rate_limit(
                _rate_limiter, key, settings.rate_limit_global_per_min
            )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": "60"},
                    content={
                        "success": False,
                        "message": "Too many requests.",
                        "error": {"code": "RATE_LIMITED", "details": "Global rate limit exceeded."},
                    },
                )
        return await call_next(request)

    # ---- metrics middleware (Phase L, sec 58) -------------------------------
    # Records per-request latency and status code in the in-memory metrics store.
    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        response: Response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000.0
        route = request.scope.get("route")
        metric_path = getattr(route, "path", None)
        if not isinstance(metric_path, str) or metric_path == "/{path:path}":
            metric_path = "/__unmatched__"
        metrics_service.record_request(
            metric_path, response.status_code, latency_ms
        )
        return response

    # ---- static frontend: serve from the React build (frontend-react/dist) --
    root = REACT_DIST
    digest_for_cache_bust = {"Cache-Control": "no-cache, no-store, must-revalidate"}

    @app.get("/", include_in_schema=False)
    def index():
        index_file = root / "index.html"
        if index_file.is_file():
            return FileResponse(
                index_file,
                headers=digest_for_cache_bust,
            )
        return JSONResponse({
            "service": settings.app_name,
            "status": "running",
            "health": "/api/health",
            "api_docs": "/api/docs",
        })

    if root.is_dir() and (root / "index.html").is_file():
        assets = root / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        # The SPA shell must never be cached: assets/ files are content-hashed
        # (immutable), but index.html must be revalidated so users always get the
        # latest bundle after a rebuild (Phase J stale-build bug).

        @app.get("/{path:path}", include_in_schema=False)
        def spa_fallback(path: str):
            # Never hijack API routes; serve static files if they exist.
            candidate = root / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            api_prefixes = ("api", "docs", "openapi.json", "redoc")
            if path.startswith(api_prefixes):
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "message": "Not found",
                             "error": {"code": "NOT_FOUND", "details": path}},
                )
            return FileResponse(
                root / "index.html",
                headers=digest_for_cache_bust,
            )

    return app


app = create_app()
