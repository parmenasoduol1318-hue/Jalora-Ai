"""Official coding-agent SDK adapters with project-scoped execution."""
from __future__ import annotations

import asyncio
import difflib
import importlib.util
import logging
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.context import ToolContext, bind_tool_execution, get_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.models.user import User
from app.services import credential_store, sandbox
from app.services.provider_state import normalize_disabled
from app.services.tool_execution import invoke_registered_tool
from app.services.tool_preflight import validate_tool_arguments
from app.tools import coding_tools as _coding_tools
from app.tools.base import registry

logger = logging.getLogger("jalora.workspace_agents")

_MAX_AGENT_FILES = 500
_MAX_AGENT_FILE_BYTES = 2 * 1024 * 1024
_MAX_AGENT_TOTAL_BYTES = 20 * 1024 * 1024
_IGNORED_STAGE_PARTS = {".git", ".jalora", "__pycache__", "node_modules"}


def _user_keys(user: User) -> dict[str, str]:
    encrypted = (user.prefs or {}).get("provider_keys") or {}
    out: dict[str, str] = {}
    for name in ("copilot", "codex", "openai"):
        token = encrypted.get(name)
        if token:
            out[name] = credential_store.fernet_decrypt(str(token))
    return out


def provider_status(user: User) -> dict[str, dict[str, Any]]:
    """Return runtime/key availability without exposing credentials."""
    try:
        keys = _user_keys(user)
    except credential_store.CredentialStoreError:
        logger.warning("Could not decrypt a coding-agent credential for user %s", user.id)
        keys = {}
    settings = get_settings()
    copilot_installed = importlib.util.find_spec("copilot") is not None
    codex_installed = importlib.util.find_spec("openai_codex") is not None
    codex_key_available = bool(keys.get("codex") or keys.get("openai") or settings.openai_api_key)
    disabled = set(normalize_disabled((user.prefs or {}).get("disabled_providers")))
    return {
        "copilot": {
            "sdk_installed": copilot_installed,
            "configured": bool(keys.get("copilot")),
            "available": copilot_installed and bool(keys.get("copilot")) and "copilot" not in disabled,
        },
        "codex": {
            "sdk_installed": codex_installed,
            "configured": codex_key_available,
            "available": codex_installed and codex_key_available and "codex" not in disabled,
        },
    }


def _agent_key(user_id: str, provider: str) -> str | None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            return None
        if provider in normalize_disabled((user.prefs or {}).get("disabled_providers")):
            return None
        keys = _user_keys(user)
    if provider == "copilot":
        return keys.get("copilot")
    return keys.get("codex") or keys.get("openai") or get_settings().openai_api_key


def _coding_tool_specs():
    _ = _coding_tools
    return [spec for spec in registry.all() if spec.category == "coding"]


async def _run_copilot(
    user_id: str,
    project_id: str,
    request_message: str,
    github_token: str,
    changes_out: list[dict[str, Any]],
    allowed_tool_names: set[str],
) -> str:
    from copilot import CopilotClient
    from copilot.rpc import PermissionDecisionReject
    from copilot.tools import Tool
    from copilot.tools import ToolResult as CopilotToolResult

    staging_project_id = uuid.uuid4().hex
    stage_root = sandbox.workspace_root(user_id, staging_project_id)
    workdir, baseline = _stage_project(
        user_id, project_id, staging_project_id, stage_root=stage_root,
    )
    try:
        tool_defs: list[Tool] = []
        for spec in _coding_tool_specs():
            if spec.name not in allowed_tool_names:
                continue
            schema = spec.tool.args_schema
            parameters = schema.model_json_schema() if schema is not None else {}

            async def handle(invocation, selected=spec):
                raw_args = invocation.arguments
                if not isinstance(raw_args, dict):
                    result = ToolResult.fail(
                        "INVALID_TOOL_ARGUMENTS",
                        "Workspace tool arguments must be an object.",
                        message=f"Workspace capability '{selected.name}' received invalid arguments.",
                    )
                else:
                    args, validation_errors = validate_tool_arguments(
                        selected, raw_args,
                    )
                    if validation_errors is not None:
                        result = ToolResult.fail(
                            "INVALID_TOOL_ARGUMENTS",
                            "Workspace tool arguments do not match the capability schema.",
                            message=f"Correct the arguments for '{selected.name}' before retrying.",
                            data={"errors": validation_errors},
                        )
                    else:
                        try:
                            parent_context = get_tool_context()
                        except RuntimeError:
                            parent_context = ToolContext(user_id=user_id)
                        with bind_tool_execution(replace(
                            parent_context,
                            user_id=user_id,
                            conversation_id=project_id,
                            project_id=staging_project_id,
                        )):
                            result = await invoke_registered_tool(
                                selected,
                                args,
                                label="Staged workspace capability",
                            )
                return CopilotToolResult(
                    text_result_for_llm=result.model_dump_json(),
                    result_type="success" if result.success else "failure",
                    error=None if result.success else result.message,
                )

            tool_defs.append(Tool(
                name=spec.name,
                description=spec.description,
                parameters=parameters,
                handler=handle,
                skip_permission=True,
            ))

        def deny_unregistered_permissions(_request, _invocation):
            return PermissionDecisionReject(feedback="Only Jalora workspace tools are enabled.")

        home = get_settings().data_dir / "coding_agents" / user_id / "copilot"
        home.mkdir(parents=True, exist_ok=True)
        async with CopilotClient(
            mode="empty",
            base_directory=str(home),
            github_token=github_token,
            working_directory=str(workdir),
            env={"COPILOT_GITHUB_TELEMETRY_DISABLED": "true"},
        ) as client, await client.create_session(
            working_directory=str(workdir),
            available_tools=[],
            tools=tool_defs,
            on_permission_request=deny_unregistered_permissions,
            system_message={
                "mode": "append",
                "content": (
                    "You are Jalora's coding agent. Operate only through the "
                    "provided Jalora workspace tools in this isolated staged "
                    "copy. Do not ask for or attempt shell, terminal, network, "
                    "or host-file access. This turn is preview-only: do not "
                    "claim that project files were changed. Jalora will show "
                    "the staged diff and apply accepted edits through its "
                    "normal approval flow."
                ),
            },
        ) as session:
            reply = await session.send_and_wait(request_message, timeout=600.0)
        changes_out.extend(_stage_preview(baseline, workdir))
        data = getattr(reply, "data", None)
        return str(getattr(data, "content", "") or "")
    finally:
        try:
            shutil.rmtree(workdir)
        except OSError:
            logger.warning("Could not remove temporary Copilot staging workspace", exc_info=True)


def _stage_project(
    user_id: str,
    project_id: str,
    run_id: str,
    *,
    stage_root: Path | None = None,
) -> tuple[Path, dict[str, str]]:
    sandbox.workspace_root(user_id, project_id)
    stage = (
        stage_root.resolve()
        if stage_root is not None
        else (
            get_settings().data_dir / "workspace_agent_staging"
            / user_id / project_id / run_id
        ).resolve()
    )
    if stage_root is not None:
        if stage.exists() and any(stage.iterdir()):
            raise FileExistsError("The temporary workspace staging directory is not empty.")
        stage.mkdir(parents=True, exist_ok=True)
    else:
        stage.mkdir(parents=True, exist_ok=False)
    baseline: dict[str, str] = {}
    total = 0
    try:
        for entry in sandbox.list_files(user_id, project_id=project_id):
            rel = str(entry["path"])
            if Path(rel).suffix.lower() not in sandbox._TEXT_EXTENSIONS:
                continue
            size = int(entry.get("size", 0))
            if size > _MAX_AGENT_FILE_BYTES or total + size > _MAX_AGENT_TOTAL_BYTES:
                continue
            content = sandbox.read_file(
                user_id, rel, max_bytes=_MAX_AGENT_FILE_BYTES, project_id=project_id,
            )
            if content.get("truncated"):
                continue
            target = (stage / Path(rel)).resolve()
            if stage not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content["content"], encoding="utf-8", newline="")
            baseline[rel] = content["content"]
            total += size
            if len(baseline) >= _MAX_AGENT_FILES:
                break
    except Exception:
        try:
            shutil.rmtree(stage)
        except OSError:
            logger.warning("Could not remove incomplete Codex staging workspace", exc_info=True)
        raise
    return stage, baseline


def _read_stage(stage: Path) -> tuple[dict[str, str], set[str]]:
    out: dict[str, str] = {}
    present: set[str] = set()
    total = 0
    for path in sorted(stage.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        try:
            relative = path.relative_to(stage)
        except ValueError:
            continue
        if _IGNORED_STAGE_PARTS.intersection(relative.parts):
            continue
        rel = relative.as_posix()
        if path.suffix.lower() not in sandbox._TEXT_EXTENSIONS:
            continue
        present.add(rel)
        if path.is_symlink():
            continue
        try:
            resolved = path.resolve()
            size = path.stat().st_size
        except OSError:
            continue
        if resolved == stage or stage not in resolved.parents:
            continue
        if (
            len(out) >= _MAX_AGENT_FILES
            or size > _MAX_AGENT_FILE_BYTES
            or total + size > _MAX_AGENT_TOTAL_BYTES
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        out[rel] = content
        total += size
    return out, present


def _stage_preview(
    baseline: dict[str, str], stage: Path, *, max_patch_chars: int = 80_000,
) -> list[dict[str, Any]]:
    """Describe staged text changes without writing anything to the project."""
    current, present = _read_stage(stage)
    paths = sorted(set(baseline) | set(current) | (set(baseline) - present))
    changes: list[dict[str, Any]] = []
    remaining = max_patch_chars
    for path in paths:
        before = baseline.get(path, "")
        after = current.get(path, "")
        if path in baseline and path not in present:
            status = "deleted"
            after = ""
        elif path not in baseline and path in current:
            status = "created"
        elif path in baseline and path in current and before != after:
            status = "updated"
        else:
            continue
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}" if before else "/dev/null",
            tofile=f"b/{path}" if after else "/dev/null",
        ))
        truncated = len(diff) > remaining
        patch = diff[:remaining]
        remaining -= len(patch)
        changes.append({
            "path": path,
            "status": status,
            "diff": patch,
            "diff_truncated": truncated,
        })
        if remaining <= 0:
            break
    return changes


async def _run_codex(
    user_id: str,
    project_id: str,
    request_message: str,
    api_key: str,
    changes_out: list[dict[str, Any]],
) -> str:
    from openai_codex import Thread, TurnHandle
    from openai_codex.client import CodexClient, CodexConfig
    from openai_codex.generated.v2_all import (
        ApiKeyLoginAccountParams,
        ApprovalsReviewer,
        AskForApproval,
        AskForApprovalValue,
        LoginAccountParams,
        SandboxMode,
        ThreadStartParams,
    )
    stage, baseline = _stage_project(user_id, project_id, uuid.uuid4().hex)
    home = get_settings().data_dir / "coding_agents" / user_id / "codex"

    def approve_staged_file_changes(method: str, _params: dict[str, Any] | None):
        if method == "item/fileChange/requestApproval":
            return {"decision": "accept"}
        return {"decision": "decline"}

    client: CodexClient | None = None
    thread: Thread | None = None
    run_task: asyncio.Task | None = None
    try:
        home.mkdir(parents=True, exist_ok=True)
        config = CodexConfig(
            cwd=str(stage),
            env={"CODEX_HOME": str(home), "OPENAI_API_KEY": api_key, "NO_COLOR": "1"},
        )
        client = CodexClient(config, approval_handler=approve_staged_file_changes)
        await asyncio.to_thread(client.start)
        await asyncio.to_thread(client.initialize)
        await asyncio.to_thread(
            client.account_login_start,
            LoginAccountParams(root=ApiKeyLoginAccountParams(apiKey=api_key, type="apiKey")),
        )
        started = await asyncio.to_thread(
            client.thread_start,
            ThreadStartParams(
                approvalPolicy=AskForApproval(root=AskForApprovalValue.on_request),
                approvalsReviewer=ApprovalsReviewer.auto_review,
                baseInstructions=(
                    "Work only in this isolated staged copy of the selected project. "
                    "Do not run shell commands or access the network. The operating-system "
                    "workspace sandbox is the authority boundary; never attempt to escape it."
                ),
                cwd=str(stage),
                sandbox=SandboxMode.workspace_write,
            ),
        )
        thread = Thread(client, started.thread.id)
        turn_start = asyncio.create_task(asyncio.to_thread(thread.turn, request_message))
        try:
            turn_handle: TurnHandle = await asyncio.shield(turn_start)
        except asyncio.CancelledError:
            try:
                turn_handle = await asyncio.wait_for(asyncio.shield(turn_start), timeout=10)
                await asyncio.to_thread(turn_handle.interrupt)
                await asyncio.wait_for(
                    asyncio.to_thread(turn_handle.run), timeout=10,
                )
            except Exception:
                logger.exception("Could not stop Codex while starting its turn")
            raise
        run_task = asyncio.create_task(asyncio.to_thread(turn_handle.run))
        try:
            while not run_task.done():
                await asyncio.sleep(0.5)
            result = await run_task
        finally:
            if not run_task.done():
                try:
                    await asyncio.to_thread(turn_handle.interrupt)
                except Exception:
                    logger.exception("Could not interrupt the Codex turn")
                try:
                    await asyncio.wait_for(asyncio.shield(run_task), timeout=10)
                except TimeoutError:
                    logger.warning("Codex turn did not stop after interruption; closing its client")
                    if client is not None:
                        await asyncio.to_thread(client.close)
                    await asyncio.gather(run_task, return_exceptions=True)
        changes_out.extend(_stage_preview(baseline, stage))
        return str(result.final_response or "")
    finally:
        try:
            if client is not None:
                await asyncio.to_thread(client.close)
        finally:
            try:
                shutil.rmtree(stage)
            except OSError:
                logger.warning("Could not remove temporary Codex staging workspace", exc_info=True)
