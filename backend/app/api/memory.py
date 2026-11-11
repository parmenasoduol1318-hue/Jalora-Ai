from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import get_settings
from app.database.session import SessionLocal
from app.errors import BadRequestError, NotFoundError
from app.models.user import User
from app.schemas import (
    MemoryCreate, MemoryOut, MemorySearchResult, MemorySettings, MemoryUpdate,
)
from app.services import memory_service
from app.services.embeddings import embed_query

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=list[MemoryOut])
def list_memories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    retention_days = (user.prefs or {}).get("memory_retention_days")
    return [
        MemoryOut.model_validate(m)
        for m in memory_service.list_memories(
            db, user.id, retention_days=retention_days,
        )
    ]


@router.get("/export", response_model=list[MemoryOut])
def export_memories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    retention_days = (user.prefs or {}).get("memory_retention_days")
    return [
        MemoryOut.model_validate(m)
        for m in memory_service.list_memories(
            db, user.id, retention_days=retention_days,
        )
    ]


@router.post("", response_model=MemoryOut, status_code=201)
async def create_memory(payload: MemoryCreate, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    if not memory_service.is_enabled(user.prefs or {}, get_settings().memory_enabled):
        raise BadRequestError(
            "Memory is disabled; enable it before adding memories.",
            "MEMORY_DISABLED",
        )
    memory, block = await memory_service.add_memory_async(
        db, user_id=user.id, content=payload.content, kind=payload.kind, source="user"
    )
    if memory is None:
        raise BadRequestError(block or "Refusing to store this content.", "SENSITIVE_CONTENT")
    return MemoryOut.model_validate(memory)


@router.put("/settings", response_model=MemorySettings)
def update_memory_settings(payload: MemorySettings, user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    prefs = dict(user.prefs or {})
    prefs["memory_enabled"] = payload.memory_enabled
    if "memory_retention_days" in payload.model_fields_set:
        prefs["memory_retention_days"] = payload.memory_retention_days
    user.prefs = prefs
    db.commit()
    return MemorySettings(
        memory_enabled=payload.memory_enabled,
        memory_retention_days=prefs.get("memory_retention_days"),
    )


@router.put("/{memory_id}", response_model=MemoryOut)
async def correct_memory(
    memory_id: str,
    payload: MemoryUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not memory_service.is_enabled(user.prefs or {}, get_settings().memory_enabled):
        raise BadRequestError(
            "Memory is disabled; enable it before correcting memories.",
            "MEMORY_DISABLED",
        )
    memory, block = await memory_service.correct_memory_async(
        db,
        user_id=user.id,
        memory_id=memory_id,
        content=payload.content,
        kind=payload.kind,
    )
    if memory is None:
        if block == "Memory not found.":
            raise NotFoundError(block)
        raise BadRequestError(block or "Refusing to store this content.", "SENSITIVE_CONTENT")
    return MemoryOut.model_validate(memory)


@router.post("/search", response_model=list[MemorySearchResult])
async def search_memory(payload: MemoryCreate, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    if not memory_service.is_enabled(user.prefs or {}, get_settings().memory_enabled):
        return []
    scored = await memory_service.search_memories(
        db,
        user_id=user.id,
        query=payload.content,
        k=10,
        retention_days=(user.prefs or {}).get("memory_retention_days"),
    )
    return [
        MemorySearchResult(memory=MemoryOut.model_validate(m), score=round(s, 3))
        for m, s in scored
    ]


@router.delete("/{memory_id}", status_code=204)
def delete_memory(memory_id: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    if not memory_service.delete_memory(db, user.id, memory_id):
        raise NotFoundError("Memory not found.")
    return None

