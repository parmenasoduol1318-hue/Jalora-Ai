"""Jalora Creative Studio API.

A package of FastAPI routers covering the full creative workflow: projects and
versioned timelines, asset library, image/video/audio editing, AI generation,
transcription, AI auto-editing, rendering/export, templates, brand kits and
collaboration. Media files are served owner-only by :mod:`app.api.creative.files`
which accepts either a bearer header or a ``?token=`` query parameter (needed
for ``<img>``/``<video>`` previews).
"""
from fastapi import APIRouter

from app.api.creative import (
    assets,
    files,
    generate,
    media,
    projects,
    render,
    templates,
    timeline,
)

router = APIRouter(prefix="/api/creative", tags=["creative_studio"])

router.include_router(projects.router)
router.include_router(timeline.router)
router.include_router(assets.router)
router.include_router(media.router)
router.include_router(generate.router)
router.include_router(render.router)
router.include_router(templates.router)
router.include_router(files.router)

__all__ = ["router"]
