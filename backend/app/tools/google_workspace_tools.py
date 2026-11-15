"""Approval-aware Google Workspace tools backed by official APIs."""
from __future__ import annotations

from typing import Any

import httpx

from app.context import get_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.services.google_workspace import (
    GoogleWorkspaceConnector,
    GoogleWorkspaceError,
)
from app.tools.base import PermissionLevel, registry


async def _invoke(method: str, *args: Any, **kwargs: Any) -> ToolResult:
    user_id = get_tool_context().user_id
    try:
        with SessionLocal() as db:
            async with httpx.AsyncClient() as client:
                connector = GoogleWorkspaceConnector(
                    db, user_id=user_id, http_client=client,
                )
                result = await getattr(connector, method)(*args, **kwargs)
    except GoogleWorkspaceError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(f"Google Workspace {method.replace('_', ' ')} completed.", result)


async def google_drive_search(query: str = "", limit: int = 20) -> ToolResult:
    """Search files owned by the connected Google account."""
    return await _invoke("search_drive", query, limit=limit)


async def google_drive_read(file_id: str) -> ToolResult:
    """Read an owned Google Drive text file or native Workspace document."""
    return await _invoke("read_drive_file", file_id)


async def create_google_doc(title: str, text: str = "") -> ToolResult:
    """Create a Google Doc with optional initial text after approval."""
    return await _invoke("create_document", title, text=text)


async def read_google_doc(document_id: str) -> ToolResult:
    """Read a Google Doc owned by the connected account."""
    return await _invoke("get_document", document_id)


async def edit_google_doc(
    document_id: str, find_text: str, replace_text: str,
) -> ToolResult:
    """Replace matching text in an owned Google Doc after approval."""
    return await _invoke(
        "replace_document_text",
        document_id,
        find_text=find_text,
        replace_text=replace_text,
    )


async def create_google_sheet(title: str) -> ToolResult:
    """Create a Google Sheet after approval."""
    return await _invoke("create_spreadsheet", title)


async def read_google_sheet(spreadsheet_id: str, range_name: str) -> ToolResult:
    """Read a bounded cell range from an owned Google Sheet."""
    return await _invoke("read_sheet_values", spreadsheet_id, range_name)


async def edit_google_sheet(
    spreadsheet_id: str, range_name: str, values: list[list[Any]],
) -> ToolResult:
    """Write a bounded cell range in an owned Google Sheet after approval."""
    return await _invoke(
        "update_sheet_values",
        spreadsheet_id,
        range_name,
        values,
    )


async def create_google_slides(title: str) -> ToolResult:
    """Create a Google Slides presentation after approval."""
    return await _invoke("create_presentation", title)


async def read_google_slides(presentation_id: str) -> ToolResult:
    """Read an owned Google Slides presentation."""
    return await _invoke("get_presentation", presentation_id)


async def edit_google_slides(
    presentation_id: str, find_text: str, replace_text: str,
) -> ToolResult:
    """Replace matching text in an owned presentation after approval."""
    return await _invoke(
        "replace_presentation_text",
        presentation_id,
        find_text=find_text,
        replace_text=replace_text,
    )


registry.register(
    google_drive_search, permission=PermissionLevel.AUTO,
    category="google_drive", requires_internet=True,
    tags=["google", "drive", "search", "read", "files"],
    side_effect="read_only", idempotent=True, retryable=True,
)
registry.register(
    google_drive_read, permission=PermissionLevel.AUTO,
    category="google_drive", requires_internet=True,
    tags=["google", "drive", "read", "files"],
    side_effect="read_only", idempotent=True, retryable=True,
)

for _function, _category, _tags in (
    (create_google_doc, "google_docs", ["google", "docs", "create"]),
    (edit_google_doc, "google_docs", ["google", "docs", "edit"]),
    (create_google_sheet, "google_sheets", ["google", "sheets", "create"]),
    (edit_google_sheet, "google_sheets", ["google", "sheets", "edit"]),
    (create_google_slides, "google_slides", ["google", "slides", "create"]),
    (edit_google_slides, "google_slides", ["google", "slides", "edit"]),
):
    registry.register(
        _function,
        permission=PermissionLevel.CONFIRM,
        category=_category,
        requires_internet=True,
        tags=_tags + ["external"],
        side_effect="external",
        idempotent=False,
        retryable=False,
    )

for _function, _category, _tags in (
    (read_google_doc, "google_docs", ["google", "docs", "read"]),
    (read_google_sheet, "google_sheets", ["google", "sheets", "read"]),
    (read_google_slides, "google_slides", ["google", "slides", "read"]),
):
    registry.register(
        _function,
        permission=PermissionLevel.AUTO,
        category=_category,
        requires_internet=True,
        tags=_tags,
        side_effect="read_only",
        idempotent=True,
        retryable=True,
    )
