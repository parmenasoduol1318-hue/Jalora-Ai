"""Memory tools exposed to the agent (search/store/list/delete)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.context import get_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.services import memory_service
from app.tools.base import PermissionLevel, registry


async def search_memory(query: str) -> ToolResult:
    """Semantically search the user's long-term memory for previously stored facts, preferences and context relevant to the query."""
    settings = get_settings()
    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        user = _get_user_prefs(db, user_id)
        if not memory_service.is_enabled(user, settings.memory_enabled):
            return ToolResult.ok(
                "Memory is currently disabled for this user.",
                {"enabled": False, "results": []},
            )
        scored = await memory_service.search_memories(
            db,
            user_id=user_id,
            query=query,
            k=5,
            retention_days=user.get("memory_retention_days"),
        )
    return ToolResult.ok(
        f"Found {len(scored)} relevant memories.",
        {
            "enabled": True,
            "results": [
                {"id": m.id, "kind": m.kind, "content": m.content, "score": round(s, 3)}
                for m, s in scored
            ],
        },
    )


async def store_memory(content: str, kind: str = "auto") -> ToolResult:
    """Store a durable long-term memory about the user (preferences, contacts, recurring tasks, approved facts). Only store what the user explicitly asked to remember or clearly confirmed. kind is classified automatically (fact/preference/contact/person/project) unless given explicitly."""
    settings = get_settings()
    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        user = _get_user_prefs(db, user_id)
        if not memory_service.is_enabled(user, settings.memory_enabled):
            return ToolResult.fail(
                "MEMORY_DISABLED",
                "User has memory turned off",
                message="Memory is disabled; nothing was stored.",
            )
        memory, block_reason = await memory_service.add_memory_async(
            db, user_id=user_id, content=content, kind=kind, source="agent"
        )
        if memory is None:
            return ToolResult.fail("SENSITIVE_CONTENT", block_reason or "blocked",
                                   message=block_reason or "Blocked.")
    return ToolResult.ok("Memory stored.", {"id": memory.id, "kind": memory.kind})


async def list_memories() -> ToolResult:
    """List the user's stored long-term memories (most recent first)."""
    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        rows = memory_service.list_memories(
            db,
            user_id,
            retention_days=_get_user_prefs(db, user_id).get("memory_retention_days"),
        )
    return ToolResult.ok(
        f"{len(rows)} memories stored.",
        {"memories": [{"id": r.id, "kind": r.kind, "content": r.content,
                       "created_at": r.created_at.isoformat()} for r in rows]},
    )


async def delete_memory(memory_id: str) -> ToolResult:
    """Delete a stored memory by its id. Use when the user asks to forget something."""
    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        removed = memory_service.delete_memory(db, user_id, memory_id)
    if not removed:
        return ToolResult.fail("NOT_FOUND", f"No memory with id {memory_id}",
                               message="That memory does not exist.")
    return ToolResult.ok("Memory deleted.", {"id": memory_id})


def _get_user_prefs(db: Session, user_id: str) -> dict:
    from app.models.user import User

    user = db.get(User, user_id)
    return (user.prefs or {}) if user else {}


registry.register(search_memory, permission=PermissionLevel.AUTO, category="memory",
                  tags=["memory"])
registry.register(store_memory, permission=PermissionLevel.NOTIFY, category="memory",
                  tags=["memory", "remember"], always_available=True)
registry.register(list_memories, permission=PermissionLevel.AUTO, category="memory",
                  tags=["memory"])
registry.register(delete_memory, permission=PermissionLevel.CONFIRM, category="memory",
                  tags=["memory", "destructive"], always_available=True)
