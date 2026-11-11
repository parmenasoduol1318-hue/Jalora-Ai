from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_router
from app.errors import (
    BadRequestError,
    ForbiddenError,
    JaloraError,
    NotFoundError,
)
from app.models.document import Document
from app.models.user import User
from app.schemas import DocumentOut, DocumentQueryResponse
from app.services.direct_action import execute_direct_action
from app.services.document_service import (
    MAX_UPLOAD_BYTES,
    prepare_document_upload,
    retrieve_for_document,
    search_files,
)
from app.services.model_router import ModelRouter
from app.services.policy import decide_for_user
from app.services.provider_state import normalize_disabled
from app.tools import registry

router = APIRouter(prefix="/api/documents", tags=["documents"])

DOC_SYSTEM = """You are Jalora AI answering questions about the user's uploaded document.
Answer ONLY from the retrieved passages. Cite pages as [p.N] when available.
If the passages do not contain the answer, say so clearly — never invent content."""


@router.get("", response_model=list[DocumentOut])
def list_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Document).where(Document.user_id == user.id)
        .order_by(Document.created_at.desc())
    ).all()
    return [DocumentOut.model_validate(r) for r in rows]


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(file: UploadFile = File(...),
                          user: User = Depends(get_current_user),
                          db: Session = Depends(get_db),
                          model_router: ModelRouter = Depends(get_router)):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise BadRequestError("File exceeds the 25 MB upload limit.", "FILE_TOO_LARGE")
    if not content:
        raise BadRequestError("The uploaded file is empty.", "EMPTY_FILE")
    doc = await prepare_document_upload(
        db,
        user_id=user.id,
        filename=file.filename or "upload",
        content=content,
    )
    try:
        outcome = await execute_direct_action(
            user_id=user.id,
            model_router=model_router,
            tool_name="process_uploaded_document",
            arguments={"document_id": doc.id},
            request_message=f"Process uploaded document '{doc.filename}'.",
            description=f"Process uploaded document '{doc.filename}'.",
            success_message=f"Processed uploaded document '{doc.filename}'.",
            require_approval=False,
        )
    except JaloraError as exc:
        db.expire_all()
        stored_doc = db.get(Document, doc.id)
        if stored_doc is not None and stored_doc.status == "queued":
            stored_doc.status = "failed"
            stored_doc.error = exc.message[:2000]
            db.commit()
        raise

    db.expire_all()
    stored_doc = db.get(Document, doc.id)
    if outcome.status != "completed" or stored_doc is None or stored_doc.status != "ready":
        details = (
            outcome.error.get("details")
            if isinstance(outcome.error, dict)
            else outcome.reply
        )
        if stored_doc is not None and stored_doc.status == "queued":
            stored_doc.status = "failed"
            stored_doc.error = str(details or "Document processing did not complete.")[:2000]
            db.commit()
        raise JaloraError(
            502,
            (
                outcome.error.get("code", "DOCUMENT_PROCESSING_FAILED")
                if isinstance(outcome.error, dict)
                else "DOCUMENT_PROCESSING_FAILED"
            ),
            str(details or "Document processing did not complete."),
        )
    return DocumentOut.model_validate(stored_doc).model_copy(
        update={"task_id": outcome.task_id}
    )


@router.get("/search", response_model=dict)
async def search_documents(q: str, user: User = Depends(get_current_user)):
    """§40: search across all the user's documents (filename + text)."""
    if not q.strip():
        raise BadRequestError("'q' is required.", "MISSING_FIELD")
    return {"query": q, "results": await search_files(user_id=user.id, query=q)}


@router.delete("/{document_id}", status_code=202)
async def delete_document(
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_router),
):
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user.id:
        raise NotFoundError("Document not found.")
    spec = registry.get("delete_document")
    if spec is None:
        raise BadRequestError(
            "The document deletion capability is unavailable.",
            "CAPABILITY_UNAVAILABLE",
        )
    decision = decide_for_user(
        permission=spec.permission,
        preferences=user.prefs or {},
        tool_name=spec.name,
        tags=spec.tags,
    )
    if not decision.allowed:
        raise ForbiddenError(
            decision.detail or "Document deletion is denied by user policy.",
            decision.code or "TOOL_DENIED",
        )
    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name=spec.name,
        arguments={"document_id": document_id},
        request_message=f"Delete uploaded document '{doc.filename}'.",
        description=f"Delete uploaded document '{doc.filename}'.",
        success_message=f"Deleted uploaded document '{doc.filename}'.",
    )
    if outcome.status == "error" and outcome.error:
        raise JaloraError(
            502,
            outcome.error.get("code", "DOCUMENT_DELETE_FAILED"),
            outcome.error.get("details", "The document could not be deleted."),
        )
    return outcome.as_response()


@router.post("/{document_id}/query", response_model=DocumentQueryResponse)
async def query_document(document_id: str, payload: dict,
                         user: User = Depends(get_current_user),
                         db: Session = Depends(get_db),
                         model_router: ModelRouter = Depends(get_router)):
    question = str(payload.get("question", "")).strip()
    if not question:
        raise BadRequestError("'question' is required.", "MISSING_FIELD")
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user.id:
        raise NotFoundError("Document not found.")
    if doc.status != "ready":
        raise BadRequestError(
            f"Document is not ready for querying (status: {doc.status}).", "DOCUMENT_NOT_READY"
        )

    hits = await retrieve_for_document(user_id=user.id, query=question, document_id=document_id)
    context = "\n\n".join(
        f"[chunk {h['chunk_index']}" + (f", p.{h['page']}" if h.get("page") else "") + f"] {h['text']}"
        for h in hits
    ) or "(no relevant passages found)"
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content=DOC_SYSTEM),
        HumanMessage(
            content=f"Document: {doc.filename}\n\nPassages:\n{context}\n\nQuestion: {question}"
        ),
    ]
    route = model_router.route(
        message=question, complexity="complex",
        disabled=normalize_disabled((user.prefs or {}).get("disabled_providers")),
    )
    model = model_router.get_model(route)
    result = await model.ainvoke(messages)
    answer = result.content if isinstance(result.content, str) else str(result.content)

    return DocumentQueryResponse(
        document_id=document_id, answer=answer, retrieved=hits,
    )


@router.websocket("/progress/{document_id}")
async def document_progress(
    websocket: WebSocket,
    document_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """WebSocket endpoint for real-time document processing progress.

    Streams progress updates (0-100%) and status changes during document
    upload and processing. Clients can use this to show progress indicators.
    """
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user.id:
        await websocket.close()
        return

    await websocket.accept()
    try:
        last_progress = -1
        last_status = ""
        while True:
            db.refresh(doc)
            if doc.progress != last_progress or doc.status != last_status:
                last_progress = doc.progress
                last_status = doc.status
                await websocket.send_json({
                    "document_id": document_id,
                    "progress": doc.progress,
                    "status": doc.status,
                    "filename": doc.filename,
                    "size_bytes": doc.size_bytes,
                })
            if doc.status in ("ready", "failed"):
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
