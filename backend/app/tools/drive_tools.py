"""Drive tools (§64): search/read/create/upload files.

Reads are automatic; writes surface in the activity feed (NOTIFY) and, when the
real Google Drive provider is connected, hit the Drive API.
"""
from __future__ import annotations

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import drive as drive_service
from app.tools.base import PermissionLevel, registry


def _user_id() -> str:
    return get_tool_context().user_id


async def search_drive(query: str = "", limit: int = 20) -> ToolResult:
    """Search the user's Drive files by name, type or content. Use for "find my file about X"."""
    files = drive_service.list_drive(user_id=_user_id(), query=query or "", limit=limit)
    if not files:
        return ToolResult.ok("No Drive files matched.", {"files": []})
    return ToolResult.ok(f"Found {len(files)} file(s).", {"files": files})


async def read_drive_file(file_id: str) -> ToolResult:
    """Read the text content of a Drive file by id (from search_drive)."""
    try:
        data = drive_service.read_drive(user_id=_user_id(), file_id=file_id)
    except drive_service.DriveError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(f"Read '{data['name']}'.", data)


async def create_drive_file(name: str, content: str = "",
                            mime_type: str = "text/plain") -> ToolResult:
    """Create a new text file in the user's Drive."""
    try:
        data = drive_service.create_drive(
            user_id=_user_id(), name=name, content=content, mime_type=mime_type)
    except drive_service.DriveError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(f"Created Drive file '{data['name']}'.", data)


async def upload_drive_file(name: str, content: str = "",
                            mime_type: str = "text/plain") -> ToolResult:
    """Upload content as a file into the user's Drive."""
    try:
        data = drive_service.upload_drive(
            user_id=_user_id(), name=name, content=content, mime_type=mime_type)
    except drive_service.DriveError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(f"Uploaded Drive file '{data['name']}'.", data)


registry.register(search_drive, permission=PermissionLevel.AUTO, category="drive")
registry.register(read_drive_file, permission=PermissionLevel.AUTO, category="drive")
registry.register(create_drive_file, permission=PermissionLevel.NOTIFY, category="drive",
                  tags=["drive", "external"])
registry.register(upload_drive_file, permission=PermissionLevel.NOTIFY, category="drive",
                  tags=["drive", "external"])
