"""File generation tools (spec §55): create_pdf/docx/xlsx/csv.

Each writes to ``data/outputs/<user_id>/`` and returns an artifact descriptor
whose ``url`` points at the authenticated ``GET /api/files/...`` download
endpoint. The graph picks up every successful result that carries an
``artifact`` entry and renders it as a download chip in the chat.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from app.context import get_tool_context
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry
from app.services.content_validation import validate_artifact_file, validate_generated_text

_SAFE = re.compile(r"[^A-Za-z0-9._\- ]+")


def _resolve_output(filename: str, default_ext: str) -> Path:
    """Sanitize the requested name and pin the correct extension."""
    from app.api.media import outputs_dir

    name = _SAFE.sub("_", filename or "").strip()
    if not name:
        name = f"output_{int(__import__('time').time() * 1000)}"
    if Path(name).suffix.lower() != default_ext:
        name = name[:120].rstrip("._ ") + default_ext
    folder = outputs_dir(get_tool_context().user_id)
    return folder / name[:160]


def _artifact(path: Path, mime: str) -> dict:
    user_id = get_tool_context().user_id
    import mimetypes

    mt = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "kind": "file",
        "url": f"/api/files/{user_id}/{path.name}",
        "mime": mt,
        "title": path.name,
        "size_bytes": path.stat().st_size,
    }


def _as_table(rows: list) -> list[list[str]]:
    cells: list[list[str]] = []
    for row in rows or []:
        if isinstance(row, (dict,)):
            cells.append([str(key) for key in row.keys()])
            cells.append([str(value) for value in row.values()])
        else:
            values = row if isinstance(row, (list, tuple)) else [row]
            cells.append([str(v) for v in values])
    return cells


async def create_pdf(content: str = "", filename: str = "", title: str = "") -> ToolResult:
    """Generate a PDF document from plain/markdown-ish text and return a download link. content is the body text; title is an optional heading; filename defaults to a generated name."""
    content_errors = validate_generated_text(content, field="content")
    if content_errors:
        return ToolResult.fail("INVALID_ARTIFACT_CONTENT", "Generated content is incomplete or contains unresolved placeholders.", message=content_errors[0])
    try:
        import reportlab
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return ToolResult.fail("REPORTLAB_UNAVAILABLE",
                               "PDF generation requires 'reportlab'.",
                               message="PDF generation is not available.")
    path = _resolve_output(filename, ".pdf")
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    styles = getSampleStyleSheet()
    story: list = []
    if title:
        story.append(Paragraph(_xml_escape(str(title)), styles["Title"]))
        story.append(Spacer(1, 12))
    for line in (content or "").split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 6))
            continue
        story.append(Paragraph(_xml_escape(line), styles["BodyText"]))
    doc.build(story)
    artifact_errors = validate_artifact_file(path)
    if artifact_errors:
        return ToolResult.fail("INVALID_ARTIFACT", artifact_errors[0], message="The document could not be verified after generation.")
    info = _artifact(path, "application/pdf")
    return ToolResult.ok(f"Created PDF '{path.name}'.", {"artifact": info})


async def create_docx(content: str = "", filename: str = "", title: str = "") -> ToolResult:
    """Generate a Word (.docx) file from text and return a download link. Lines become paragraphs; blank lines create spacing."""
    content_errors = validate_generated_text(content, field="content")
    if content_errors:
        return ToolResult.fail("INVALID_ARTIFACT_CONTENT", "Generated content is incomplete or contains unresolved placeholders.", message=content_errors[0])
    try:
        import docx  # python-docx
    except ImportError:
        return ToolResult.fail("PYTHON_DOCX_UNAVAILABLE",
                               "DOCX generation requires 'python-docx'.",
                               message="DOCX generation is not available.")
    path = _resolve_output(filename, ".docx")
    document = docx.Document()
    if title:
        document.add_heading(str(title), level=1)

    lines = (content or "").splitlines()
    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and lines[index].strip().startswith("|")
            and _is_markdown_table_separator(lines[index + 1])
        ):
            table_rows = [_markdown_table_cells(lines[index])]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                if not _is_markdown_table_separator(lines[index]):
                    table_rows.append(_markdown_table_cells(lines[index]))
                index += 1

            column_count = max(map(len, table_rows))
            table = document.add_table(rows=0, cols=column_count)
            table.style = "Table Grid"
            for row_index, values in enumerate(table_rows):
                cells = table.add_row().cells
                for column_index, cell in enumerate(cells):
                    cell.text = (
                        values[column_index] if column_index < len(values) else ""
                    )
                    if row_index == 0:
                        for paragraph in cell.paragraphs:
                            for run in paragraph.runs:
                                run.bold = True
            continue

        line = lines[index]
        text = line.strip()
        if text:
            document.add_paragraph(text)
        index += 1

    document.save(str(path))
    artifact_errors = validate_artifact_file(path)
    if artifact_errors:
        return ToolResult.fail("INVALID_ARTIFACT", artifact_errors[0], message="The document could not be verified after generation.")
    fmt = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    info = _artifact(path, fmt)
    return ToolResult.ok(f"Created DOCX '{path.name}'.", {"artifact": info})


def _markdown_table_cells(line: str) -> list[str]:
    return [
        cell.strip().replace(r"\|", "|")
        for cell in line.strip().strip("|").split("|")
    ]


def _is_markdown_table_separator(line: str) -> bool:
    cells = _markdown_table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


async def create_xlsx(rows: list | None = None, filename: str = "", sheet_name: str = "Sheet1",
                      headers: list | None = None) -> ToolResult:
    """Generate an Excel (.xlsx) spreadsheet from a list of rows and return a download link. rows is a list of lists (first row is the header when headers is omitted). Also accepts a list of dicts (each dict becomes a header row + a value row)."""
    if not rows and not headers:
        return ToolResult.fail("INVALID_ARTIFACT_CONTENT", "At least one row or header is required.", message="The spreadsheet has no data.")
    try:
        import openpyxl
    except ImportError:
        return ToolResult.fail("OPENPYXL_UNAVAILABLE",
                               "XLSX generation requires 'openpyxl'.",
                               message="XLSX generation is not available.")
    path = _resolve_output(filename, ".xlsx")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = (sheet_name or "Sheet1")[:31]
    table = _as_table(rows or [])
    if headers:
        sheet.append([str(h) for h in headers])
    elif table:
        sheet.append(table[0])
        table = table[1:]
    for row in table:
        sheet.append(row)
    workbook.save(str(path))
    artifact_errors = validate_artifact_file(path)
    if artifact_errors:
        return ToolResult.fail("INVALID_ARTIFACT", artifact_errors[0], message="The spreadsheet could not be verified after generation.")
    info = _artifact(path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return ToolResult.ok(f"Created XLSX '{path.name}'.", {
        "artifact": info, "rows": 1 + len(table),
    })


async def create_csv(rows: list | None = None, filename: str = "", headers: list | None = None) -> ToolResult:
    """Generate a CSV file from a list of rows and return a download link. rows is a list of lists (or list of dicts)."""
    if not rows and not headers:
        return ToolResult.fail("INVALID_ARTIFACT_CONTENT", "At least one row or header is required.", message="The CSV has no data.")
    path = _resolve_output(filename, ".csv")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    table = _as_table(rows or [])
    if headers:
        writer.writerow([str(h) for h in headers])
    elif table:
        writer.writerow(table[0])
        table = table[1:]
    for row in table:
        writer.writerow(row)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    artifact_errors = validate_artifact_file(path)
    if artifact_errors:
        return ToolResult.fail("INVALID_ARTIFACT", artifact_errors[0], message="The CSV could not be verified after generation.")
    info = _artifact(path, "text/csv")
    return ToolResult.ok(f"Created CSV '{path.name}'.", {
        "artifact": info, "rows": 1 + len(table),
    })


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


registry.register(create_pdf, permission=PermissionLevel.AUTO, category="files",
                  tags=["generate", "pdf", "document", "report", "export"])
registry.register(create_docx, permission=PermissionLevel.AUTO, category="files",
                  tags=["generate", "docx", "document", "report", "export"])
registry.register(create_xlsx, permission=PermissionLevel.AUTO, category="files",
                  tags=["generate", "xlsx", "spreadsheet"])
registry.register(create_csv, permission=PermissionLevel.AUTO, category="files",
                  tags=["generate", "csv"])
