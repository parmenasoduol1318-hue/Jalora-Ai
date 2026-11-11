"""Unified knowledge-base ingest (spec §69): conversations, notes and
documents are normalized to embedded chunks in one shared index, so a single
retrieval path serves memory recall, document Q&A and file search.

Concretely:
  * notes and conversation excerpts -> Memory rows (kind="note"), embedded and
    indexed exactly like user-approved memories (shared embed + vector store);
  * documents -> DocumentChunk rows (embedded in document_service);
  * search_knowledge() queries both surfaces so nothing is siloed.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.document import Document, DocumentChunk
from app.models.memory import Memory
from app.services import memory_service


async def ingest_source(
    db: Session,
    *,
    user_id: str,
    source_type: str,
    content: str,
    source: str = "note",
) -> dict[str, str]:
    """Store free-form knowledge ("note") or a conversation excerpt and return
    {kind, id} which the caller can cite. Sensitive content is refused exactly
    like memories (find_sensitive_content)."""
    source_type = (source_type or "note").strip().lower()
    if source_type == "conversation":
        source = "conversation"
    kind = "note" if source_type == "note" else "fact"
    if not content or not content.strip():
        return {"kind": "", "id": ""}
    if memory_service.find_sensitive_content(content):
        return {"kind": "blocked", "id": "", "reason":
                "Sensitive content is not indexed."}
    memory, _err = await memory_service.add_memory_async(
        db, user_id=user_id, content=content.strip(), kind=kind, source=source,
    )
    if memory is None:
        return {"kind": "blocked", "id": "", "reason": _err or "blocked"}
    return {"kind": memory.kind, "id": memory.id}


async def search_knowledge(*, user_id: str, query: str, k: int = 5) -> list[dict]:
    """Search memories/notes AND the user's document chunks in one ranked list.
    Results are tagged by source so the agent can cite them accurately."""
    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.services.embeddings import embed_query

    query_vec, _ = await embed_query(query)

    def _cos(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    scored: list[tuple[dict, float]] = []
    with SessionLocal() as db:
        for m in db.scalars(select(Memory).where(Memory.user_id == user_id)):
            score = _cos(query_vec, m.embedding) if m.embedding else 0.0
            scored.append(({
                "source": "memory", "id": m.id, "kind": m.kind,
                "content": m.content, "text": m.content,
            }, score))
        docs = {
            d.id: d
            for d in db.scalars(select(Document).where(Document.user_id == user_id))
        }
        for c in db.scalars(select(DocumentChunk).where(DocumentChunk.user_id == user_id)):
            score = _cos(query_vec, c.embedding) if c.embedding else 0.0
            doc = docs.get(c.document_id)
            scored.append(({
                "source": "document", "id": c.id, "document_id": c.document_id,
                "filename": doc.filename if doc else "", "text": c.text,
                "page": c.page,
            }, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    out: list[dict] = []
    for item, score in scored[:k]:
        if score <= 0.05 and out:
            continue
        item["score"] = round(score, 3)
        out.append(item)
    return out or [
        {**item, "score": round(score, 3)}
        for item, score in scored[:1]
        if item.get("content") or item.get("text")
    ]