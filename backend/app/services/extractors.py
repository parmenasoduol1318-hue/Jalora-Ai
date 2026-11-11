"""File extraction helpers and magic-byte MIME sniffing (spec §39, §41-OCR).

Text extraction:
  * DOCX / XLSX / PPTX via python-docx / openpyxl / python-pptx
  * PDF via pypdf; a page with no embedded text is OCR'd through pytesseract
    when the optional tesseract binary is available (honest fallback otherwise).
  * Plain text/tabular formats use the raw text decoder in document_service.

Every optional dependency is imported lazily so the rest of the app works
offline; a missing package raises a clear, stable error message that the
document row records verbatim.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

_OPTIONAL_DEPS: dict[str, str] = {
    ".docx": "DOCX support requires the optional 'python-docx' package.",
    ".xlsx": "XLSX support requires the optional 'openpyxl' package.",
    ".pptx": "PPTX support requires the optional 'python-pptx' package.",
    ".pdf": "PDF support requires the optional 'pypdf' package.",
}


# --------------------------------------------------------------------------- #
# Text extraction
# --------------------------------------------------------------------------- #
def _require(ext: str):
    raise RuntimeError(_OPTIONAL_DEPS[ext])


def extract_docx(path: Path) -> str:
    try:
        import docx  # python-docx
    except ImportError:
        _require(".docx")
    document = docx.Document(str(path))
    lines: list[str] = []
    for para in document.paragraphs:
        text = (para.text or "").strip()
        if text:
            lines.append(text)
    for table in document.tables:
        lines.append("Table:")
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError:
        _require(".xlsx")
    workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    lines: list[str] = []
    for name in workbook.sheetnames:
        sheet = workbook[name]
        rows: list[str] = []
        for row in sheet.iter_rows(values_only=True):
            values = ["" if cell is None else str(cell).strip() for cell in row]
            if any(values):
                rows.append(" | ".join(values))
        lines.append(f"Sheet '{name}' ({len(rows)} rows)")
        lines.extend(rows)
    return "\n".join(lines)


def extract_pptx(path: Path) -> str:
    try:
        import pptx  # python-pptx
    except ImportError:
        _require(".pptx")
    presentation = pptx.Presentation(str(path))
    lines: list[str] = []
    for idx, slide in enumerate(presentation.slides, start=1):
        lines.append(f"--- Slide {idx} ---")
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                for para in shape.text_frame.paragraphs:
                    text = (para.text or "").strip()
                    if text:
                        lines.append(text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    lines.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(lines)


def extract_pdf(path: Path) -> tuple[str, int]:
    """Return (text, page_count). Pages without embedded text are OCR'd when
    tesseract is installed; otherwise the page contributes an empty marker."""
    try:
        from pypdf import PdfReader
    except ImportError:
        _require(".pdf")
    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if len(text) < 40:
            text = _ocr_page(path, reader.pages.index(page), text)
        pages.append(text)
    return "\n\n".join(pages), len(reader.pages)


def _ocr_page(path: Path, index: int, embedded: str) -> str:
    """OCR a single PDF page to image bytes and back to text."""
    ocr = _tesseract()
    if ocr is None:
        return embedded or "[scanned page - OCR not available]"
    try:
        from pdf2image import convert_from_path  # type: ignore[import-not-found]
    except ImportError:
        return embedded or "[scanned page - OCR requires 'pdf2image' + poppler]"
    try:
        images = convert_from_path(str(path), first_page=index + 1, last_page=index + 1)
        if not images:
            return embedded
        text = ocr.image_to_string(images[0]).strip()
        return text or embedded or "[scanned page - OCR found nothing]"
    except Exception:  # noqa: BLE001 - OCR must never break ingestion
        return embedded or "[scanned page - OCR unavailable]"


def _tesseract():
    """Return a pytesseract-like module or None when unusable."""
    try:
        import pytesseract  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        pytesseract.get_tesseract_version()
    except Exception:  # noqa: BLE001 - tesseract binary not installed
        return None
    return pytesseract


# --------------------------------------------------------------------------- #
# Magic-byte MIME sniffing (§39)
# --------------------------------------------------------------------------- #
def sniff_mime(content: bytes) -> str:
    """Detect the real MIME type from magic bytes, never from the filename."""
    if content.startswith(b"%PDF"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content[:4] == b"ftyp":
        return "video/mp4"
    if content.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if content.startswith(b"ID3") or (
        content.startswith(b"\xff\xfb") or content.startswith(b"\xff\xf3")
        or content.startswith(b"\xff\xf2")
    ):
        return "audio/mpeg"
    if content.startswith(b"OggS"):
        return "audio/ogg"
    if content.startswith(b"fLaC"):
        return "audio/flac"
    if content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        return "audio/wav"
    if content.startswith(b"PK\x03\x04"):
        return _sniff_office_zip(content)
    try:
        content.decode("utf-8")
    except (UnicodeDecodeError, UnicodeError):
        return "application/octet-stream"
    return "text/plain"


def _sniff_office_zip(content: bytes) -> str:
    """Distinguish DOCX/XLSX/PPTX (all zip containers) from any other zip."""
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(content)) as zf:
            names = set(zf.namelist())
    except Exception:  # noqa: BLE001 - corrupt zip falls back to octet-stream
        return "application/zip"
    if "word/document.xml" in names:
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if "xl/workbook.xml" in names:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if "ppt/presentation.xml" in names:
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return "application/zip"


# Canonical extension for every sniffable type (used to reject spoofed names).
MIME_EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/wav": ".wav",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt",
}