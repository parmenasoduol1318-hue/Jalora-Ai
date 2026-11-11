"""Workspace file and terminal capabilities used by the central executor."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.context import get_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.conversation import Conversation
from app.services import sandbox
from app.tools.base import PermissionLevel, registry


def _owned_project(db, user_id: str, project_id: str) -> Conversation | None:
    project = db.get(Conversation, project_id)
    if (
        project is None
        or project.user_id != user_id
        or project.project != "workspace"
        or project.archived
    ):
        return None
    return project


async def create_workspace_project(title: str) -> ToolResult:
    """Create one workspace project after the central executor's policy check."""
    normalized_title = title.strip()[:255]
    if not normalized_title:
        return ToolResult.fail("INVALID_PROJECT_TITLE", "A project title is required.")
    context = get_tool_context()
    with SessionLocal() as db:
        project = db.get(Conversation, context.conversation_id)
        if project is None or project.user_id != context.user_id:
            return ToolResult.fail(
                "WORKSPACE_PROJECT_CREATE_FAILED",
                "The canonical task conversation is unavailable.",
            )
        project.title = normalized_title
        project.project = "workspace"
        project.archived = False
        db.commit()
        db.refresh(project)
        return ToolResult.ok(
            "Created a workspace project.",
            {"id": project.id, "title": project.title, "project": project.project},
        )


async def rename_workspace_project(project_id: str, title: str) -> ToolResult:
    """Rename an owned, active workspace project."""
    normalized_title = title.strip()[:255]
    if not normalized_title:
        return ToolResult.fail("INVALID_PROJECT_TITLE", "A project title is required.")
    context = get_tool_context()
    with SessionLocal() as db:
        project = _owned_project(db, context.user_id, project_id)
        if project is None:
            return ToolResult.fail("WORKSPACE_PROJECT_NOT_FOUND", "Workspace project not found.")
        project.title = normalized_title
        db.commit()
        return ToolResult.ok(
            "Renamed the workspace project.",
            {"id": project.id, "title": project.title, "project": project.project},
        )


async def archive_workspace_project(project_id: str) -> ToolResult:
    """Hide a project without deleting its conversation or canonical task history."""
    context = get_tool_context()
    with SessionLocal() as db:
        project = _owned_project(db, context.user_id, project_id)
        if project is None:
            return ToolResult.fail("WORKSPACE_PROJECT_NOT_FOUND", "Workspace project not found.")
        project.archived = True
        db.commit()
        return ToolResult.ok(
            "Archived the workspace project. Its task history and files were preserved.",
            {"id": project.id, "archived": project.archived},
        )


def _scope() -> tuple[str, str]:
    context = get_tool_context()
    if not context.project_id:
        raise ValueError("A workspace project must be selected.")
    return (
        context.user_id,
        "" if context.project_id == "legacy" else context.project_id,
    )


def _sandbox_action(callable_, *args, **kwargs) -> ToolResult:
    try:
        user_id, project_id = _scope()
        result = callable_(
            user_id, *args, project_id=project_id, **kwargs
        )
    except ValueError as exc:
        return ToolResult.fail("WORKSPACE_PROJECT_REQUIRED", str(exc))
    except sandbox.SandboxError as exc:
        return ToolResult.fail(exc.code, exc.detail)
    return ToolResult.ok("Completed the workspace action.", result)


async def save_workspace_file(path: str, content: str) -> ToolResult:
    """Save content to an existing or new file inside the selected project."""
    return _sandbox_action(sandbox.save_file, path, content)


async def create_workspace_file(path: str, content: str = "") -> ToolResult:
    """Create a file inside the selected project."""
    return _sandbox_action(sandbox.create_file, path, content)


async def delete_workspace_file(path: str) -> ToolResult:
    """Delete a file or empty directory inside the selected project."""
    return _sandbox_action(sandbox.delete_file, path)


async def rename_workspace_file(path: str, new_path: str) -> ToolResult:
    """Move or rename an item inside the selected project."""
    return _sandbox_action(sandbox.rename_file, path, new_path)


async def create_workspace_directory(path: str) -> ToolResult:
    """Create a directory inside the selected project."""
    return _sandbox_action(sandbox.make_dir, path)


async def rollback_workspace_edits(path: str | None = None) -> ToolResult:
    """Restore journaled workspace edits in the selected project."""
    try:
        user_id, project_id = _scope()
        restored = sandbox.rollback(user_id, path, project_id=project_id)
    except ValueError as exc:
        return ToolResult.fail("WORKSPACE_PROJECT_REQUIRED", str(exc))
    except sandbox.SandboxError as exc:
        return ToolResult.fail(exc.code, exc.detail)
    return ToolResult.ok(
        "Restored journaled workspace edits.",
        {"restored": restored},
    )


async def upload_workspace_files(files: list[dict[str, Any]]) -> ToolResult:
    """Create uploaded text files inside the selected project."""
    if not files:
        return ToolResult.fail("EMPTY_UPLOAD", "Select at least one file to upload.")
    if len(files) > 100:
        return ToolResult.fail(
            "TOO_MANY_UPLOADS", "Upload at most 100 files in one action."
        )
    results: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            rejected.append({"filename": "", "detail": "Invalid upload entry."})
            continue
        path = str(item.get("path") or "")
        content = str(item.get("content") or "")
        try:
            user_id, project_id = _scope()
            result = sandbox.create_file(
                user_id, path, content, project_id=project_id
            )
        except ValueError as exc:
            return ToolResult.fail("WORKSPACE_PROJECT_REQUIRED", str(exc))
        except sandbox.SandboxError as exc:
            rejected.append({"filename": path, "detail": exc.detail})
        else:
            results.append(result)
    return ToolResult.ok(
        "Workspace upload action finished.",
        {"results": results, "rejected": rejected},
    )


async def run_workspace_command(
    argv: list[str],
    cwd: str | None = None,
    timeout_ms: int | None = None,
) -> ToolResult:
    """Run one sandbox-allowlisted command in the selected project."""
    try:
        user_id, project_id = _scope()
        result = sandbox.run_command(
            user_id,
            argv,
            cwd=cwd,
            timeout=(timeout_ms / 1000 if timeout_ms is not None else None),
            project_id=project_id,
        )
    except ValueError as exc:
        return ToolResult.fail("WORKSPACE_PROJECT_REQUIRED", str(exc))
    except sandbox.SandboxError as exc:
        return ToolResult.fail(exc.code, exc.detail)
    return ToolResult.ok(
        "Workspace command completed." if result["ok"] else "Workspace command failed.",
        result,
    )


async def import_legacy_workspace_files() -> ToolResult:
    """Import eligible legacy files into the selected project without overwrites."""
    try:
        user_id, project_id = _scope()
    except ValueError as exc:
        return ToolResult.fail("WORKSPACE_PROJECT_REQUIRED", str(exc))
    with SessionLocal() as db:
        project_ids = set(db.scalars(
            select(Conversation.id).where(
                Conversation.user_id == user_id,
                Conversation.project == "workspace",
            )
        ).all())
    try:
        result = sandbox.import_legacy_workspace(
            user_id, project_id, project_ids
        )
    except sandbox.SandboxError as exc:
        return ToolResult.fail(exc.code, exc.detail)
    return ToolResult.ok("Imported eligible legacy workspace files.", result)


for _tool in (
    create_workspace_project,
    rename_workspace_project,
    archive_workspace_project,
    save_workspace_file,
    create_workspace_file,
    delete_workspace_file,
    rename_workspace_file,
    create_workspace_directory,
    rollback_workspace_edits,
    upload_workspace_files,
    run_workspace_command,
    import_legacy_workspace_files,
):
    registry.register(
        _tool,
        permission=(
            PermissionLevel.STRICT
            if _tool in {
                archive_workspace_project,
                delete_workspace_file,
                rollback_workspace_edits,
            }
            else PermissionLevel.CONFIRM
        ),
        category="workspace",
        tags=["workspace", "file", "external"],
        side_effect="external",
    )
