"""Document (RAG) tools and a small time tool."""
from __future__ import annotations

from datetime import datetime, timezone

from app.context import get_tool_context
from app.database.session import SessionLocal
from app.errors import JaloraError, ToolResult
from app.tools.base import PermissionLevel, registry


async def current_datetime() -> ToolResult:
    """Get the current date and time (UTC). Use before scheduling, reminders or time-sensitive answers."""
    now = datetime.now(timezone.utc)
    return ToolResult.ok(
        now.strftime("Current UTC time: %Y-%m-%d %H:%M:%S (%A)"),
        {"iso": now.isoformat(), "weekday": now.strftime("%A")},
    )


async def list_documents() -> ToolResult:
    """List the user's uploaded documents with their ids and processing status."""
    user_id = get_tool_context().user_id
    from app.models.document import Document
    from sqlalchemy import select

    with SessionLocal() as db:
        docs = list(db.scalars(
            select(Document).where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
        ))
    return ToolResult.ok(
        f"{len(docs)} documents.",
        {"documents": [{"id": d.id, "filename": d.filename, "status": d.status} for d in docs]},
    )


async def read_document(query: str, document_id: str = "") -> ToolResult:
    """Retrieve the passages of an uploaded document most relevant to the query. Leave document_id empty to search all of the user's documents. Returns text with page references for citations."""
    user_id = get_tool_context().user_id
    from app.services.document_service import retrieve_for_document

    hits = await retrieve_for_document(user_id=user_id, query=query, document_id=document_id or None)
    if not hits:
        return ToolResult.ok(
            "No relevant passages found.",
            {"results": [], "hint": "Confirm the document is uploaded and status is 'ready'."},
        )
    return ToolResult.ok(
        f"Retrieved {len(hits)} passages.",
        {"results": hits},
    )


async def search_files(query: str) -> ToolResult:
    """Search every uploaded document by filename and content (inside text, so it finds matches a plain search misses). Returns the most relevant passages with their titles. Use when the user asks where or whether some information appears in their documents."""
    user_id = get_tool_context().user_id
    from app.services.document_service import search_files as _search_files

    hits = await _search_files(user_id=user_id, query=query)
    if not hits:
        return ToolResult.ok(
            "No documents matched.",
            {"results": [], "query": query},
        )
    return ToolResult.ok(
        f"Found {len(hits)} matching passages.",
        {"query": query, "results": hits},
    )


async def delete_document(document_id: str) -> ToolResult:
    """Delete one of the user's uploaded documents. Requires explicit approval."""
    user_id = get_tool_context().user_id
    from app.models.document import Document

    with SessionLocal() as db:
        doc = db.get(Document, document_id)
        if doc is None or doc.user_id != user_id:
            return ToolResult.fail(
                "DOCUMENT_NOT_FOUND", "Document not found.", message="Document not found."
            )
        filename = doc.filename
        db.delete(doc)
        db.commit()
    return ToolResult.ok(
        f"Deleted document '{filename}'.",
        {"document_id": document_id, "filename": filename, "status": "deleted"},
    )


async def process_uploaded_document(document_id: str) -> ToolResult:
    """Extract and index a durably stored upload as a canonical task step."""
    user_id = get_tool_context().user_id
    from app.services.document_service import process_stored_document

    with SessionLocal() as db:
        try:
            doc = await process_stored_document(
                db, user_id=user_id, document_id=document_id
            )
        except JaloraError as exc:
            return ToolResult.fail(exc.code, exc.message)
        if doc.status != "ready":
            return ToolResult.fail(
                "DOCUMENT_PROCESSING_FAILED",
                doc.error or "The uploaded document could not be processed.",
            )
        return ToolResult.ok(
            f"Processed uploaded document '{doc.filename}'.",
            {
                "document_id": doc.id,
                "filename": doc.filename,
                "status": doc.status,
                "progress": doc.progress,
                "page_count": doc.page_count,
            },
        )


registry.register(current_datetime, permission=PermissionLevel.AUTO, category="utility",
                  tags=["time"], always_available=True)
registry.register(list_documents, permission=PermissionLevel.AUTO, category="documents",
                  tags=["rag"])
registry.register(read_document, permission=PermissionLevel.AUTO, category="documents",
                  tags=["rag"])
registry.register(search_files, permission=PermissionLevel.AUTO, category="documents",
                  tags=["rag", "search"])
registry.register(
    delete_document,
    permission=PermissionLevel.STRICT,
    category="documents",
    tags=["documents", "delete", "destructive"],
)
registry.register(
    process_uploaded_document,
    permission=PermissionLevel.AUTO,
    category="documents",
    tags=["documents", "upload", "processing"],
)
