"""Document intelligence pipeline (spec §6, Phase D §39–41, §55, §69):
UPLOAD -> SNIFF -> EXTRACT -> CHUNK -> EMBED -> STORE -> RETRIEVE -> GENERATE.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import BadRequestError, NotFoundError
from app.models.document import Document, DocumentChunk
from app.models.user import new_id
from app.services.embeddings import embed_query, embed_texts
from app.services.extractors import (
    extract_docx,
    extract_pdf,
    extract_pptx,
    extract_xlsx,
    sniff_mime,
)

logger = logging.getLogger("jalora.documents")

SUPPORTED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".csv", ".json", ".log", ".py", ".html",
    ".docx", ".xlsx", ".pptx",
}
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
MAX_QUERY_HITS = 5

# §39: per-type size caps (bytes). Text/office stay at the general cap;
# PDFs may be larger; anything that sniffs as a different class is rejected.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_BYTES_BY_EXTENSION: dict[str, int] = {
    ".pdf": 60 * 1024 * 1024,
}

# Honest, specific messages for unsupported formats.
EXTENSION_ADVICE = {
    ".png": "Image understanding: attach it in chat and I will analyse it.",
    ".jpg": "Image understanding: attach it in chat and I will analyse it.",
    ".jpeg": "Image understanding: attach it in chat and I will analyse it.",
    ".gif": "Image understanding: attach it in chat and I will analyse it.",
    ".mp3": "Audio understanding: attach it in chat and I will transcribe it.",
    ".mp4": "Video understanding: attach it in chat and I will analyse it.",
}


def validate_upload(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext in SUPPORTED_EXTENSIONS:
        return ext
    if ext in EXTENSION_ADVICE:
        raise BadRequestError(EXTENSION_ADVICE[ext], "UNSUPPORTED_FORMAT")
    raise BadRequestError(
        f"Unsupported file type '{ext or 'unknown'}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        "UNSUPPORTED_FORMAT",
    )


def sniff_matches_extension(content: bytes, ext: str) -> bool:
    """§39: the real MIME type (magic bytes) must agree with the claimed
    extension. Plain text pages get a free pass because many handlers write
    them slightly off-spec."""
    sniffed = sniff_mime(content)
    if sniffed == "text/plain":
        return ext in {".txt", ".md", ".csv", ".json", ".log", ".py", ".html"}
    canonical = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    }.get(sniffed)
    return canonical == ext


def extract_text(path: Path, ext: str) -> tuple[str, int]:
    """Return (text, page_count)."""
    if ext == ".docx":
        return extract_docx(path), 1
    if ext == ".xlsx":
        return extract_xlsx(path), 1
    if ext == ".pptx":
        return extract_pptx(path), 1
    if ext == ".pdf":
        return extract_pdf(path)
    return _decode_text(path), 1


def _decode_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise BadRequestError("File could not be decoded as text.", "DECODE_FAILED")


def chunk_text(text: str) -> list[str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        chunks.append(cleaned[start:start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _validate_upload_content(filename: str, content: bytes) -> str:
    ext = validate_upload(filename)
    cap = MAX_BYTES_BY_EXTENSION.get(ext, MAX_UPLOAD_BYTES)
    if len(content) > cap:
        raise BadRequestError(
            f"File exceeds the {cap // (1024 * 1024)} MB upload limit for this type.",
            "FILE_TOO_LARGE",
        )
    if not content:
        raise BadRequestError("The uploaded file is empty.", "EMPTY_FILE")
    # §39: a DOCX/XLSX/PPTX/PDF must actually be that format, not a disguised
    # payload (spoofed MIME); other types get a text-sniff sanity check.
    if ext in {".pdf", ".docx", ".xlsx", ".pptx"} and not sniff_matches_extension(content, ext):
        real = sniff_mime(content)
        raise BadRequestError(
            f"File content does not match its '.{ext[1:]}' name (detected: {_friendly_mime(real)}).",
            "MIME_MISMATCH",
        )
    return ext


async def prepare_document_upload(
    db: Session, *, user_id: str, filename: str, content: bytes
) -> Document:
    ext = _validate_upload_content(filename, content)
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    doc = Document(
        id=new_id(), user_id=user_id, filename=filename,
        mime_type=_mime_for(ext), size_bytes=len(content), status="queued",
        progress=0,
    )
    db.add(doc)
    db.commit()

    path = settings.upload_dir / f"{doc.id}{ext}"
    try:
        path.write_bytes(content)
    except OSError as exc:
        doc.status = "failed"
        doc.error = f"Could not persist the uploaded file: {exc}"[:2000]
        db.commit()
        raise
    db.refresh(doc)
    return doc


async def process_stored_document(
    db: Session, *, user_id: str, document_id: str
) -> Document:
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user_id:
        raise NotFoundError("Document not found.")
    if doc.status == "ready":
        return doc

    ext = validate_upload(doc.filename)
    path = get_settings().upload_dir / f"{doc.id}{ext}"
    if not path.is_file():
        doc.status = "failed"
        doc.error = "The stored upload is unavailable for processing."
        db.commit()
        db.refresh(doc)
        return doc
    return await _process_stored_document(db, doc=doc, path=path, ext=ext)


async def ingest_document(
    db: Session, *, user_id: str, filename: str, content: bytes
) -> Document:
    doc = await prepare_document_upload(
        db, user_id=user_id, filename=filename, content=content
    )
    return await process_stored_document(db, user_id=user_id, document_id=doc.id)


async def _process_stored_document(
    db: Session, *, doc: Document, path: Path, ext: str
) -> Document:
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    doc.status = "processing"
    doc.progress = 5
    doc.error = None
    db.commit()

    try:
        doc.progress = 40
        db.commit()
        text, pages = extract_text(path, ext)
        doc.progress = 75
        db.commit()
        pieces = chunk_text(text)
        if not pieces:
            raise BadRequestError("The document contains no extractable text.", "EMPTY_DOCUMENT")
        vectors, _backend = await embed_texts(pieces)
        doc.page_count = pages
        for idx, (piece, vector) in enumerate(zip(pieces, vectors)):
            db.add(DocumentChunk(
                id=new_id(), document_id=doc.id, user_id=doc.user_id, idx=idx,
                text=piece, embedding=vector, dims=len(vector),
                page=None,
            ))
        doc.status = "ready"
        doc.progress = 100
        db.commit()
        db.refresh(doc)
        return doc
    except Exception as exc:  # noqa: BLE001 - mark failure honestly on the document
        db.rollback()
        doc = db.get(Document, doc.id)
        doc.status = "failed"
        doc.error = str(exc)[:2000]
        db.commit()
        db.refresh(doc)
        logger.warning("document %s failed: %s", doc.filename, exc)
        return doc


async def search_files(*, user_id: str, query: str, k: int = 5) -> list[dict]:
    """§40: filename + metadata + extracted-text keyword + embedding cosine
    across all of the user's documents. Mirrors retrieve_for_document but
    additionally rewards filename/metadata matches so a query like "the budget"
    surfaces the right spreadsheet even when its text is sparse."""
    with _session() as db:
        docs = list(db.scalars(
            select(Document).where(Document.user_id == user_id, Document.status == "ready")
        ))
        chunks = list(db.scalars(
            select(DocumentChunk).where(DocumentChunk.user_id == user_id)
        ))
    if not chunks:
        return []
    query_vec, _ = await embed_query(query)
    lowered = query.lower()
    tokens = set(w for w in lowered.split() if len(w) > 2)

    def _metadata_bonus(doc: Document) -> int:
        name = (doc.filename or "").lower()
        score = 0
        if lowered in name:
            score += 2
        for w in tokens:
            if w in name:
                score += 1
        return score

    scored: list[tuple[DocumentChunk, float]] = []
    by_doc = {d.id: d for d in docs}
    for chunk in chunks:
        if chunk.embedding and len(chunk.embedding) == len(query_vec):
            a, b = query_vec, chunk.embedding
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            score = dot / (na * nb) if na and nb else 0.0
        else:
            score = 0.0
        doc = by_doc.get(chunk.document_id)
        score = min(1.0, score + 0.05 * (doc.filename.lower().count(lowered) if doc else 0))
        scored.append((chunk, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = [c for c, s in scored[:k] if s > 0.05] or [c for c, _ in scored[:2]]
    return [
        {
            "document_id": c.document_id,
            "filename": (by_doc.get(c.document_id).filename if c.document_id in by_doc else ""),
            "text": c.text[:1000],
            "page": c.page,
            "chunk_index": c.idx,
            "score": round(next((s for ch, s in scored if ch.id == c.id), 0.0), 3),
        }
        for c in top
    ]


def _friendly_mime(sniffed: str) -> str:
    return {
        "application/pdf": "PDF",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "application/msword": "legacy DOC",
    }.get(sniffed, sniffed)


async def retrieve_for_document(
    *, user_id: str, query: str, document_id: str | None = None
) -> list[dict]:
    with _session() as db:
        stmt = select(DocumentChunk).where(DocumentChunk.user_id == user_id)
        if document_id:
            doc = db.get(Document, document_id)
            if doc is None or doc.user_id != user_id:
                raise NotFoundError("Document not found.")
            stmt = stmt.where(DocumentChunk.document_id == document_id)
        chunks = list(db.scalars(stmt))

    if not chunks:
        return []

    query_vec, _ = await embed_query(query)
    scored: list[tuple[DocumentChunk, float]] = []
    for chunk in chunks:
        if chunk.embedding and len(chunk.embedding) == len(query_vec):
            a, b = query_vec, chunk.embedding
            dot = sum(x * y for x, y in zip(a, b))
            na = sum(x * x for x in a) ** 0.5
            nb = sum(y * y for y in b) ** 0.5
            score = dot / (na * nb) if na and nb else 0.0
            scored.append((chunk, score))
        else:
            scored.append((chunk, 0.0))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = [c for c, s in scored[:MAX_QUERY_HITS] if s > 0.05] or [c for c, _ in scored[:2]]
    return [
        {
            "text": c.text[:1000],
            "page": c.page,
            "chunk_index": c.idx,
            "document_id": c.document_id,
            "score": round(next((s for ch, s in scored if ch.id == c.id), 0.0), 3),
        }
        for c in top
    ]


def _session():
    from app.database.session import SessionLocal

    return SessionLocal()


def _mime_for(ext: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".json": "application/json",
        ".log": "text/plain",
        ".py": "text/x-python",
        ".html": "text/html",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(ext, "application/octet-stream")
