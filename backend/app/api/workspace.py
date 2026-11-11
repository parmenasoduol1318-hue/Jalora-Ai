"""Coding workspace API (§53-54): projects, files, editor, terminal, agent.

A *project* is a ``Conversation`` with ``project="workspace"``; its id scopes a
per-project sandbox folder (``data/workspaces/<user_id>/<conversation_id>``).
The API exposes:

  * sessions  — list/create/rename/delete workspace projects;
  * files     — tree, read, and direct IDE edits (save/create/delete/rename);
  * diff      — the agent's pending ``edit_code`` edits + rollback;
  * upload    — drop files into the project folder;
  * export    — download a zip of the whole project workspace;
  * agent     — ``POST /api/workspace/chat`` (SSE), the coding agent;
  * terminal  — ``POST /api/workspace/exec``, an allowlisted command runner.

Editor changes, uploads, imports, and terminal commands are registered
capabilities executed by the canonical task lifecycle. The agent changes files
through the CONFIRM-gated ``edit_code`` tool (journaled, so diff/rollback work).
"""
from __future__ import annotations

import json
import os
from io import BytesIO

from app.api.deps import get_current_user, get_db, get_router
from app.database.session import SessionLocal
from app.errors import BadRequestError, NotFoundError, ServiceUnavailableError
from app.models.conversation import Conversation
from app.models.task import TaskStep
from app.models.user import User
from app.schemas import (
    ConversationCreate,
    ConversationOut,
    WorkspaceChatRequest,
    WorkspaceCreateIn,
    WorkspaceDiffOut,
    WorkspaceExecIn,
    WorkspaceFileOut,
    WorkspaceRenameIn,
    WorkspaceSaveIn,
    WorkspaceSessionPatch,
    WorkspaceTree,
    WorkspaceUploadError,
    WorkspaceUploadOut,
    WorkspaceWriteResult,
)
from app.services import sandbox
from app.services.chat_service import stream_chat
from app.services.direct_action import execute_direct_action
from app.services.model_router import ModelRouter
from app.services.workspace_agents import provider_status
from fastapi import APIRouter, Body, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# Direct-upload cap (spec §53-54): uploads are for code/text files and reuse the
# coding sandbox's per-project workspace, so they are size-capped like other I/O.
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB per file

_WORKSPACE_PROJECT = "workspace"


def _http_error(exc: sandbox.SandboxError):
    if exc.code in ("NOT_FOUND", "IS_DIRECTORY"):
        return NotFoundError(exc.detail, exc.code)
    if exc.code == "SANDBOX_DISABLED":
        return ServiceUnavailableError(exc.detail, exc.code)
    return BadRequestError(exc.detail, exc.code)


def _pid(project_id: str | None) -> str:
    return (project_id or "").strip()


def _owned_workspace(db: Session, user_id: str, conversation_id: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if (
        conv is None
        or conv.user_id != user_id
        or conv.project != _WORKSPACE_PROJECT
        or conv.archived
    ):
        raise NotFoundError("Workspace project not found.")
    return conv


async def _run_workspace_management_action(
    *,
    user: User,
    model_router: ModelRouter,
    tool_name: str,
    arguments: dict,
    description: str,
    response: Response,
    project_id: str | None = None,
) -> dict:
    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name=tool_name,
        arguments=arguments,
        request_message=description,
        description=description,
        success_message=description,
        conversation_id=project_id,
        project=_WORKSPACE_PROJECT if project_id else None,
        workspace_project_id=project_id or "legacy",
        require_approval=False,
    )
    if outcome.status == "paused_approval":
        response.status_code = 202
        return outcome.as_response()
    with SessionLocal() as db:
        step = db.scalar(
            select(TaskStep)
            .where(
                TaskStep.task_id == outcome.task_id,
                TaskStep.tool_id == tool_name,
            )
            .order_by(TaskStep.idx.desc())
        )
        result_data = (
            step.result_data
            if step is not None and isinstance(step.result_data, dict)
            else {}
        )
    if outcome.status != "completed":
        error = outcome.error if isinstance(outcome.error, dict) else {}
        raise ServiceUnavailableError(
            error.get("details", outcome.reply or "The workspace action did not complete."),
            error.get("code", "WORKSPACE_ACTION_FAILED"),
        )
    execution_result = result_data.get("execution_result", {})
    output = (
        execution_result.get("output", {})
        if isinstance(execution_result, dict)
        else {}
    )
    if not isinstance(output, dict):
        raise ServiceUnavailableError(
            "The workspace action completed without a usable result.",
            "WORKSPACE_ACTION_RESULT_MISSING",
        )
    return {
        **output,
        "status": outcome.status,
        "task_id": outcome.task_id,
        "run_id": outcome.run_id,
    }


async def _run_workspace_action(
    *,
    user: User,
    model_router: ModelRouter,
    project_id: str,
    tool_name: str,
    arguments: dict,
    description: str,
    response,
) -> dict:
    if project_id:
        with SessionLocal() as db:
            _owned_workspace(db, user.id, project_id)
    outcome = await execute_direct_action(
        user_id=user.id,
        model_router=model_router,
        tool_name=tool_name,
        arguments=arguments,
        request_message=description,
        description=description,
        success_message=description,
        conversation_id=project_id or None,
        project=_WORKSPACE_PROJECT,
        workspace_project_id=project_id or "legacy",
        require_approval=False,
    )
    if outcome.status == "paused_approval":
        response.status_code = 202
        return outcome.as_response()
    with SessionLocal() as db:
        step = db.scalar(
            select(TaskStep)
            .where(
                TaskStep.task_id == outcome.task_id,
                TaskStep.tool_id == tool_name,
            )
            .order_by(TaskStep.idx.desc())
        )
        result_data = (
            step.result_data
            if step is not None and isinstance(step.result_data, dict)
            else {}
        )
    if outcome.status != "completed":
        error = outcome.error if isinstance(outcome.error, dict) else {}
        code = str(error.get("code") or "")
        details = error.get("details", outcome.reply or "The workspace action did not complete.")
        raw_result = result_data.get("raw")
        if isinstance(raw_result, str):
            try:
                tool_result = json.loads(raw_result)
            except json.JSONDecodeError:
                tool_result = {}
            tool_error = tool_result.get("error") if isinstance(tool_result, dict) else None
            if isinstance(tool_error, dict):
                code = str(tool_error.get("code") or code)
                details = str(tool_error.get("details") or details)
        if code:
            raise _http_error(sandbox.SandboxError(code, str(details)))
        raise ServiceUnavailableError(
            details,
            "WORKSPACE_ACTION_FAILED",
        )

    execution_result = result_data.get("execution_result", {})
    output = (
        execution_result.get("output", {})
        if isinstance(execution_result, dict)
        else {}
    )
    if not isinstance(output, dict):
        raise ServiceUnavailableError(
            "The workspace action completed without a usable result.",
            "WORKSPACE_ACTION_RESULT_MISSING",
        )
    if output.get("success") is False:
        error = output.get("error")
        raise BadRequestError(
            error.get("details", "The workspace action failed.")
            if isinstance(error, dict)
            else "The workspace action failed.",
            error.get("code", "WORKSPACE_ACTION_FAILED")
            if isinstance(error, dict)
            else "WORKSPACE_ACTION_FAILED",
        )
    if tool_name == "rename_workspace_file" and "from_path" not in output:
        output = {**output, "from_path": output.get("from", "")}
    return {
        **output,
        "status": outcome.status,
        "task_id": outcome.task_id,
        "run_id": outcome.run_id,
    }


# --------------------------------------------------------------------------- #
# sessions (projects)                                                         #
# --------------------------------------------------------------------------- #
@router.get("/sessions", response_model=list[ConversationOut])
def list_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id,
               Conversation.project == _WORKSPACE_PROJECT,
               Conversation.archived.is_(False))
        .order_by(Conversation.updated_at.desc())
    ).all()
    return [ConversationOut.model_validate(r) for r in rows]


@router.get("/legacy")
def list_legacy_files(user: User = Depends(get_current_user)):
    """Preview importable files from the user's former shared coding workspace."""
    with SessionLocal() as db:
        project_ids = set(db.scalars(
            select(Conversation.id).where(
                Conversation.user_id == user.id,
                Conversation.project == _WORKSPACE_PROJECT,
            )
        ).all())
    return {"files": sandbox.legacy_workspace_files(user.id, project_ids)}


@router.post("/sessions/{conversation_id}/import-legacy")
async def import_legacy_files(conversation_id: str,
                              response: Response,
                              user: User = Depends(get_current_user),
                              db: Session = Depends(get_db),
                              model_router: ModelRouter = Depends(get_router)):
    """Copy legacy workspace files into a project, never replacing project files."""
    _owned_workspace(db, user.id, conversation_id)
    return await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=conversation_id,
        tool_name="import_legacy_workspace_files",
        arguments={},
        description="Import eligible legacy files into this workspace project.",
        response=response,
    )


@router.post("/sessions", status_code=201)
async def create_session(payload: ConversationCreate,
                         response: Response,
                         user: User = Depends(get_current_user),
                         model_router: ModelRouter = Depends(get_router)):
    title = (payload.title or "New project").strip()[:255] or "New project"
    result = await _run_workspace_management_action(
        user=user,
        model_router=model_router,
        tool_name="create_workspace_project",
        arguments={"title": title},
        description=f"Create workspace project '{title}'.",
        response=response,
    )
    if result.get("status") == "paused_approval":
        return result
    with SessionLocal() as db:
        project = _owned_workspace(db, user.id, str(result.get("id") or ""))
        return ConversationOut.model_validate(project)


@router.patch("/sessions/{conversation_id}")
async def rename_session(conversation_id: str,
                         payload: WorkspaceSessionPatch,
                         response: Response,
                         user: User = Depends(get_current_user),
                         db: Session = Depends(get_db),
                         model_router: ModelRouter = Depends(get_router)):
    conv = _owned_workspace(db, user.id, conversation_id)
    title = payload.title.strip()[:255]
    result = await _run_workspace_management_action(
        user=user,
        model_router=model_router,
        tool_name="rename_workspace_project",
        arguments={"project_id": conversation_id, "title": title},
        description=f"Rename workspace project '{conv.title}' to '{title}'.",
        response=response,
        project_id=conversation_id,
    )
    if result.get("status") == "paused_approval":
        return result
    db.expire(conv)
    db.refresh(conv)
    return ConversationOut.model_validate(conv)


@router.delete("/sessions/{conversation_id}")
async def delete_session(conversation_id: str,
                         response: Response,
                         user: User = Depends(get_current_user),
                         db: Session = Depends(get_db),
                         model_router: ModelRouter = Depends(get_router)):
    conv = _owned_workspace(db, user.id, conversation_id)
    result = await _run_workspace_management_action(
        user=user,
        model_router=model_router,
        tool_name="archive_workspace_project",
        arguments={"project_id": conversation_id},
        description=f"Archive workspace project '{conv.title}'.",
        response=response,
        project_id=conversation_id,
    )
    if result.get("status") == "paused_approval":
        return result
    response.status_code = 204
    return None


@router.get("/agents")
def list_workspace_agents(user: User = Depends(get_current_user)):
    """Report external coding-agent readiness without exposing stored keys."""
    return {"providers": provider_status(user)}


# --------------------------------------------------------------------------- #
# agent (coding)                                                              #
# --------------------------------------------------------------------------- #
@router.post("/chat")
async def workspace_chat(payload: WorkspaceChatRequest,
                         user: User = Depends(get_current_user),
                         model_router: ModelRouter = Depends(get_router)):
    """Run the coding agent (SSE). ``conversation_id`` is the project session."""
    if payload.model_provider in {"copilot", "codex"}:
        if not payload.external_agent_confirmed:
            raise BadRequestError(
                "Confirm this external coding-agent turn before it can run.",
                "EXTERNAL_AGENT_CONFIRMATION_REQUIRED",
            )
        if not payload.stream:
            raise BadRequestError(
                "External coding-agent turns require streaming.",
                "EXTERNAL_AGENT_STREAM_REQUIRED",
            )
        if not payload.conversation_id:
            raise BadRequestError(
                "Select a workspace project before using an external agent.",
                "WORKSPACE_PROJECT_REQUIRED",
            )
        with SessionLocal() as db:
            _owned_workspace(db, user.id, payload.conversation_id)
        return StreamingResponse(
            stream_chat(
                user_id=user.id,
                request_message=payload.message,
                conversation_id=payload.conversation_id,
                router=model_router,
                mode="coding",
                project=_WORKSPACE_PROJECT,
                direct_tool_call={
                    "tool_name": "run_external_coding_agent",
                    "arguments": {
                        "provider": payload.model_provider,
                        "request_message": payload.message,
                    },
                    "description": (
                        f"Prepare a {payload.model_provider} coding proposal "
                        "for the selected workspace project."
                    ),
                    "workspace_project_id": payload.conversation_id,
                    "success_message": (
                        "Prepared an external coding-agent proposal. No project "
                        "files were changed; review the preview before applying edits."
                    ),
                },
                trigger_kind="external_agent",
                trigger_data={
                    "provider": payload.model_provider,
                    "workspace_project_id": payload.conversation_id,
                },
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    if payload.conversation_id:
        with SessionLocal() as db:
            _owned_workspace(db, user.id, payload.conversation_id)
    if payload.stream:
        return StreamingResponse(
            stream_chat(
                user_id=user.id,
                request_message=payload.message,
                conversation_id=payload.conversation_id,
                router=model_router,
                provider_override=payload.model_provider,
                model_override=payload.model_name,
                media_ids=payload.media_ids,
                document_ids=payload.document_ids,
                mode="coding",
                project=_WORKSPACE_PROJECT,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    from app.events import ListEmitter
    from app.services.chat_service import execute_chat

    outcome = await execute_chat(
        user_id=user.id,
        request_message=payload.message,
        conversation_id=payload.conversation_id,
        router=model_router,
        emitter=ListEmitter(),
        provider_override=payload.model_provider,
        model_override=payload.model_name,
        media_ids=payload.media_ids,
        document_ids=payload.document_ids,
        mode="coding",
        project=_WORKSPACE_PROJECT,
    )
    from app.schemas import ChatResponse

    return ChatResponse.from_outcome(outcome)


# --------------------------------------------------------------------------- #
# files (tree / read)                                                         #
# --------------------------------------------------------------------------- #
@router.get("/tree", response_model=WorkspaceTree)
def get_tree(subdir: str = Query(default=""), project_id: str = Query(default=""),
             user: User = Depends(get_current_user)):
    try:
        files = sandbox.list_files(user.id, subdir, _pid(project_id))
    except sandbox.SandboxError as exc:
        raise _http_error(exc)
    return WorkspaceTree(files=files)


@router.get("/file", response_model=WorkspaceFileOut)
def get_file(path: str = Query(...), project_id: str = Query(default=""),
             user: User = Depends(get_current_user)):
    try:
        return WorkspaceFileOut(**sandbox.read_file(user.id, path, project_id=_pid(project_id)))
    except sandbox.SandboxError as exc:
        raise _http_error(exc)


# --------------------------------------------------------------------------- #
# files (editor writes)                                                       #
# --------------------------------------------------------------------------- #
@router.put("/file")
async def save_file(
    payload: WorkspaceSaveIn,
    response: Response,
    project_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    return await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=_pid(project_id),
        tool_name="save_workspace_file",
        arguments={"path": payload.path, "content": payload.content},
        description=f"Save workspace file '{payload.path}'.",
        response=response,
    )


@router.post("/file", status_code=201)
async def create_file(
    payload: WorkspaceCreateIn,
    response: Response,
    project_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    return await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=_pid(project_id),
        tool_name="create_workspace_file",
        arguments={"path": payload.path, "content": payload.content},
        description=f"Create workspace file '{payload.path}'.",
        response=response,
    )


@router.delete("/file")
async def delete_file(
    response: Response,
    path: str = Query(...),
    project_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    return await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=_pid(project_id),
        tool_name="delete_workspace_file",
        arguments={"path": path},
        description=f"Delete workspace file or empty folder '{path}'.",
        response=response,
    )


@router.post("/rename")
async def rename_file(
    payload: WorkspaceRenameIn,
    response: Response,
    project_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    return await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=_pid(project_id),
        tool_name="rename_workspace_file",
        arguments={"path": payload.path, "new_path": payload.new_path},
        description=f"Rename workspace path '{payload.path}' to '{payload.new_path}'.",
        response=response,
    )


@router.post("/mkdir")
async def make_dir(
    response: Response,
    path: str = Body(..., embed=True),
    project_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    return await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=_pid(project_id),
        tool_name="create_workspace_directory",
        arguments={"path": path},
        description=f"Create workspace folder '{path}'.",
        response=response,
    )


# --------------------------------------------------------------------------- #
# diff / rollback                                                             #
# --------------------------------------------------------------------------- #
@router.get("/diff", response_model=WorkspaceDiffOut)
def get_diff(project_id: str = Query(default=""), user: User = Depends(get_current_user)):
    return WorkspaceDiffOut(edits=sandbox.pending_edits(user.id, _pid(project_id)))


@router.post("/rollback")
async def rollback(
    response: Response,
    path: str | None = Query(default=None),
    project_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    return await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=_pid(project_id),
        tool_name="rollback_workspace_edits",
        arguments={"path": path},
        description=(
            f"Roll back workspace edits{f' for {path}' if path else ''}."
        ),
        response=response,
    )


# --------------------------------------------------------------------------- #
# upload                                                                      #
# --------------------------------------------------------------------------- #
@router.post("/upload")
async def upload(response: Response,
                 files: list[UploadFile] = File(...),
                 project_id: str = Form(default=""),
                 user: User = Depends(get_current_user),
                 model_router: ModelRouter = Depends(get_router)):
    """Direct file upload into the per-project sandbox (Phase J, §53-54)."""
    pid = _pid(project_id)
    accepted: list[dict[str, str]] = []
    rejected: list[WorkspaceUploadError] = []
    for upload in files:
        name = upload.filename or "upload"
        try:
            raw = await upload.read()
        except OSError as exc:  # pragma: no cover - transport-level failure
            rejected.append(WorkspaceUploadError(filename=name, detail=str(exc)))
            continue
        if len(raw) > _MAX_UPLOAD_BYTES:
            rejected.append(WorkspaceUploadError(
                filename=name,
                detail=f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB workspace upload limit."))
            continue
        try:
            content = raw.decode("utf-8", errors="replace")
            accepted.append({"path": _safe_name(name), "content": content})
        except sandbox.SandboxError as exc:
            rejected.append(WorkspaceUploadError(filename=name, detail=exc.detail))
    if not accepted:
        return WorkspaceUploadOut(results=[], rejected=rejected)
    result = await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=pid,
        tool_name="upload_workspace_files",
        arguments={"files": accepted},
        description=f"Upload {len(accepted)} file(s) to the workspace.",
        response=response,
    )
    if result.get("status") == "paused_approval":
        return result
    rejected.extend(
        WorkspaceUploadError(**item)
        for item in result.get("rejected", [])
        if isinstance(item, dict)
    )
    return WorkspaceUploadOut(
        results=[
            WorkspaceWriteResult(**item)
            for item in result.get("results", [])
            if isinstance(item, dict)
        ],
        rejected=rejected,
    )


# --------------------------------------------------------------------------- #
# project export                                                              #
# --------------------------------------------------------------------------- #
@router.get("/export")
def export_project(project_id: str = Query(default=""),
                   user: User = Depends(get_current_user)):
    """Download a zip of this project workspace so the user's work is saved
    and portable (spec: independent workspace — save all its work)."""
    if not project_id:
        raise BadRequestError("project_id is required to export a workspace.")
    try:
        data = sandbox.zip_project(user.id, _pid(project_id))
    except sandbox.SandboxError as exc:
        raise _http_error(exc)
    return StreamingResponse(
        BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="workspace.zip"'},
    )


# --------------------------------------------------------------------------- #
# terminal (allowlisted command runner)                                       #
# --------------------------------------------------------------------------- #
@router.post("/exec")
async def exec_command(
    payload: WorkspaceExecIn,
    response: Response,
    project_id: str = Query(default=""),
    user: User = Depends(get_current_user),
    model_router: ModelRouter = Depends(get_router),
):
    """Run an allowlisted command (python, pytest, read-only git; pip if enabled)
    in the project folder and return its captured output."""
    result = await _run_workspace_action(
        user=user,
        model_router=model_router,
        project_id=_pid(project_id),
        tool_name="run_workspace_command",
        arguments={
            "argv": payload.argv,
            "cwd": payload.cwd,
            "timeout_ms": payload.timeout_ms,
        },
        description=f"Run workspace command: {' '.join(payload.argv)}",
        response=response,
    )
    return result


def _safe_name(filename: str) -> str:
    """Collapse a client-supplied filename to a single, safe workspace path."""
    cleaned = os.path.basename(filename.replace("\\", "/"))
    cleaned = cleaned.lstrip(".")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or cleaned.endswith("/"):
        raise sandbox.SandboxError("INVALID_PATH", "A file name is required.")
    return "/".join(parts)
