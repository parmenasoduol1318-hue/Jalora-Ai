"""Long-term memory: storage, semantic retrieval and user controls (spec §5, §12–13).

Rules enforced here:
  * Nothing sensitive is stored silently (explicit content filter).
  * Memory can be disabled per user; when disabled, retrieval returns nothing.
  * Users can list, correct (delete + re-add) and delete their memories.
  * kind="auto" is classified by rule-based heuristics (memory_classify).
  * Retrieval goes through the pluggable vector index (SQL cosine by default,
    ChromaDB when configured); SQL rows are always the source of truth.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.memory import Memory
from app.services import vector_store
from app.services.embeddings import embed_query, embed_texts
from app.services.memory_classify import classify_kind

logger = logging.getLogger("jalora.memory")

# Patterns that must never be remembered without explicit user handling.
_SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("password", re.compile(r"(password|passwd|passcode|pin\s*(code)?|otp)\s*[:=]?\s*\S+", re.I)),
    ("credit card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("secret/api key", re.compile(r"\b(sk|pk|ghp|gho|xox[bap])[-_][A-Za-z0-9]{10,}", re.I)),
]


def find_sensitive_content(text: str) -> str | None:
    """Return a reason string if the text looks sensitive, else None."""
    for label, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(text):
            return f"content resembles a {label}"
    return None


def is_enabled(user_prefs: dict, default: bool = True) -> bool:
    return bool(user_prefs.get("memory_enabled", default))


async def add_memory_async(
    db: Session,
    *,
    user_id: str,
    content: str,
    kind: str = "fact",
    source: str = "user",
    meta: dict | None = None,
) -> tuple[Memory | None, str | None]:
    reason = find_sensitive_content(content)
    if reason:
        return None, f"Refusing to store memory: {reason}. Sensitive information is not stored."
    if not kind or kind.strip().lower() in {"auto", "classify"}:
        kind = classify_kind(content)
    vector, _backend = await embed_texts([content])
    memory = Memory(
        user_id=user_id, kind=kind, content=content.strip(), source=source,
        embedding=vector[0], dims=len(vector[0]), meta=meta,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    vector_store.safe_index(memory)
    return memory, None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def search_memories(
    db: Session,
    *,
    user_id: str,
    query: str,
    k: int = 5,
    retention_days: int | None = None,
) -> list[tuple[Memory, float]]:
    purge_expired_memories(db, user_id, retention_days)
    rows = list(db.scalars(select(Memory).where(Memory.user_id == user_id)))
    if not rows:
        return []
    try:
        query_vec, _ = await embed_query(query)
    except httpx.HTTPError as exc:
        logger.warning(
            "Memory embeddings unavailable (%s); using lexical retrieval",
            type(exc).__name__,
        )
        tokens = query.lower().split()
        lexical_hits = [
            (
                row,
                min(
                    1.0,
                    sum(1 for token in tokens if token in row.content.lower())
                    / max(1, len(tokens)),
                ),
            )
            for row in rows
        ]
        lexical_hits.sort(key=lambda item: item[1], reverse=True)
        return lexical_hits[:k]
    by_id = {row.id: row for row in rows}
    try:
        hits = vector_store.get_vector_store().search(
            user_id, query_vec, k, rows, query_text=query
        )
    except Exception:
        hits = None
    if hits is None:
        # store failure -> plain SQL cosine over the same rows
        hits = []
        for row in rows:
            if row.embedding:
                hits.append((row.id, _cosine(query_vec, row.embedding)))
            else:
                lowered = query.lower()
                tokens = lowered.split()
                overlap = sum(1 for t in tokens if t in row.content.lower())
                hits.append((row.id, min(1.0, overlap / max(1, len(tokens)))))
        hits.sort(key=lambda pair: pair[1], reverse=True)
        hits = hits[:k]
    scored = [(by_id[mid], score) for mid, score in hits if mid in by_id]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def list_memories(
    db: Session,
    user_id: str,
    limit: int = 200,
    retention_days: int | None = None,
) -> list[Memory]:
    purge_expired_memories(db, user_id, retention_days)
    return list(
        db.scalars(
            select(Memory).where(Memory.user_id == user_id)
            .order_by(Memory.created_at.desc()).limit(limit)
        )
    )


def purge_expired_memories(
    db: Session, user_id: str, retention_days: int | None,
) -> int:
    if retention_days is None:
        return 0
    if not 1 <= retention_days <= 3650:
        raise ValueError("memory retention must be between 1 and 3650 days")
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    expired = list(db.scalars(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.created_at < cutoff,
        )
    ))
    if not expired:
        return 0
    ids = [row.id for row in expired]
    for row in expired:
        db.delete(row)
    db.commit()
    for memory_id in ids:
        vector_store.safe_remove(memory_id)
    return len(ids)


def delete_memory(db: Session, user_id: str, memory_id: str) -> bool:
    row = db.get(Memory, memory_id)
    if row is None or row.user_id != user_id:
        return False
    db.delete(row)
    db.commit()
    vector_store.safe_remove(memory_id)
    return True


async def correct_memory_async(
    db: Session,
    *,
    user_id: str,
    memory_id: str,
    content: str,
    kind: str | None = None,
) -> tuple[Memory | None, str | None]:
    memory = db.get(Memory, memory_id)
    if memory is None or memory.user_id != user_id:
        return None, "Memory not found."
    reason = find_sensitive_content(content)
    if reason:
        return None, f"Refusing to store memory: {reason}. Sensitive information is not stored."
    vector, _backend = await embed_texts([content])
    memory.content = content.strip()
    memory.kind = kind or memory.kind
    memory.source = "user"
    memory.embedding = vector[0]
    memory.dims = len(vector[0])
    memory.meta = {
        **(memory.meta or {}),
        "corrected_at": datetime.now(timezone.utc).isoformat(),
    }
    db.commit()
    db.refresh(memory)
    vector_store.safe_index(memory)
    return memory, None


def build_memory_context(memories: list[tuple[Memory, float]]) -> str:
    if not memories:
        return ""
    lines = [f"- ({m.kind}) {m.content}" for m, _score in memories if _score > 0.05]
    if not lines:
        return ""
    return (
        "User-approved long-term memories (use only when relevant, never invent):\n"
        + "\n".join(lines)
    )


# --------------------------------------------------------------------------- #
# preference reconciliation (Phase K §27, §72)                                 #
# --------------------------------------------------------------------------- #
# When a new preference memory is stored, any existing preference that
# *contradicts* it is removed first so the user never sees conflicting
# guidance.  Contradiction is detected by a small vocabulary of antonym
# pairs (e.g. "concise" vs "verbose").
_CONTRADICTIONS: list[tuple[str, str]] = [
    ("concise", "verbose"),
    ("concise", "detailed"),
    ("brief", "long"),
    ("formal", "casual"),
    ("professional", "friendly"),
    ("friendly", "formal"),
    ("direct", "elaborate"),
    ("short", "long"),
    ("simple", "complex"),
    ("english", "kiswahili"),
    ("auto", "manual"),
    ("automatic", "manual"),
]


def _token_set(text: str) -> set[str]:
    return {t.strip(".,;:!?\"'()[]{}").lower() for t in text.split()}


def _contradicts(new_content: str, existing_content: str) -> bool:
    """Return True when two preference strings are semantically opposed."""
    new_tokens = _token_set(new_content)
    ex_tokens = _token_set(existing_content)
    for a, b in _CONTRADICTIONS:
        if a in new_tokens and b in ex_tokens:
            return True
        if b in new_tokens and a in ex_tokens:
            return True
    return False


async def update_memory(
    db: Session,
    *,
    user_id: str,
    content: str,
    kind: str = "preference",
    source: str = "preference_extractor",
    scope: str = "preference",
) -> Memory | None:
    """Store ``content`` as a memory, reconciling contradictions for the given
    ``scope``.

    For ``kind="preference"`` any existing preference memory whose content
    contradicts the new one is deleted first (§72: no contradictions).
    A short ``scope`` string (e.g. "response_length", "tone") is used to
    narrow the contradiction check to same-scope preferences.
    """
    if find_sensitive_content(content):
        logger.info("Declined sensitive preference memory for user %s", user_id)
        return None
    # Delete contradictory existing preferences (same scope or global).
    if kind == "preference":
        rows = list(db.scalars(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.kind == "preference",
            )
        ))
        for row in rows:
            row_scope = (row.meta or {}).get("scope") if hasattr(row, "meta") else None
            if row_scope and row_scope != scope:
                continue
            # Skip storing a duplicate of an existing preference (§72: no
            # duplicates).  Return the existing row unchanged.
            if row.content.strip() == content.strip():
                return row
            if _contradicts(content, row.content):
                db.delete(row)
                vector_store.safe_remove(row.id)
    # Now store the new memory.
    memory, refusal = await add_memory_async(
        db, user_id=user_id, content=content, kind=kind, source=source,
        meta={"scope": scope} if kind == "preference" else None,
    )
    if memory is None:
        if refusal:
            logger.info("Declined preference memory for user %s: %s", user_id, refusal)
        return None
    return memory
