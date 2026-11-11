"""Coding tools backed by the per-user (optionally per-project) sandbox (§53-54).

Read-only helpers (list/read/inspect/search/analyze) run automatically;
anything that mutates files or executes code requires explicit approval
(PermissionLevel CONFIRM).

Every tool resolves its sandbox folder from the request-scoped tool context:
``user_id`` plus ``project_id`` (the conversation id of a ``project="workspace"``
conversation). When ``project_id`` is empty the legacy per-user workspace is
used, so chat-bound coding tools keep working unchanged.
"""
from __future__ import annotations

import re

from app.context import get_tool_context
from app.errors import ToolResult
from app.services import sandbox
from app.tools.base import PermissionLevel, registry


def _pid() -> str:
    return get_tool_context().project_id or ""


def _guard(fn, *args, **kwargs) -> ToolResult:
    try:
        return fn(*args, **kwargs)
    except sandbox.SandboxError as exc:
        return ToolResult.fail(exc.code, exc.detail, message=exc.detail)


async def list_files(subdir: str = "") -> ToolResult:
    """List the files in the user's coding workspace (or a subdirectory), with sizes and extensions. Use to see what exists before reading or editing."""
    user_id = get_tool_context().user_id
    result = _guard(sandbox.list_files, user_id, subdir, _pid())
    if isinstance(result, ToolResult):
        return result
    return ToolResult.ok(f"Found {len(result)} file(s).", {"files": result})


async def read_file(path: str) -> ToolResult:
    """Read the full text content of a file in the user's coding workspace. Use before editing to see the exact current content."""
    user_id = get_tool_context().user_id
    result = _guard(sandbox.read_file, user_id, path, project_id=_pid())
    if isinstance(result, ToolResult):
        return result
    return ToolResult.ok(f"Read {result['path']} ({result['size']} bytes).", result)


async def inspect_code(path: str) -> ToolResult:
    """Inspect a file or directory in the user's coding workspace: file size, imports, classes and functions. Use to understand code before editing it."""
    user_id = get_tool_context().user_id
    result = _guard(sandbox.inspect_code, user_id, path, _pid())
    if isinstance(result, ToolResult):
        return result
    kind = result.get("kind", "file")
    return ToolResult.ok(f"Inspected {result['path']} ({kind}).", result)


async def search_code(query: str, subdir: str = "", regex: bool = False) -> ToolResult:
    """Search text across files in the user's coding workspace and return matching lines with their paths and line numbers."""
    user_id = get_tool_context().user_id
    result = _guard(sandbox.search_code, user_id, query, subdir, regex=regex,
                    project_id=_pid())
    if isinstance(result, ToolResult):
        return result
    return ToolResult.ok(f"Found {len(result)} matches for '{query}'.", {"matches": result})


async def analyze_error(text: str) -> ToolResult:
    """Parse a traceback or error log into a structured summary (exception type, message, frames, suggested next step)."""
    result = sandbox.analyze_error(text)
    headline = result.get("exception_type") or "error"
    return ToolResult.ok(f"Analyzed error ({headline}).", result)


async def edit_code(path: str, content: str | None = None,
                    old_string: str | None = None,
                    new_string: str | None = None) -> ToolResult:
    """Edit or create a file in the user's coding workspace. Provide full `content`, or `old_string` to replace with `new_string`. The previous version is backed up for diff/rollback."""
    user_id = get_tool_context().user_id
    pid = _pid()
    if old_string is not None:
        try:
            current = sandbox.read_file(user_id, path, project_id=pid)["content"]
        except sandbox.SandboxError as exc:
            return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
        if old_string not in current:
            return ToolResult.fail(
                "OLD_STRING_NOT_FOUND",
                "The old_string was not found in the file.",
                message="Could not apply the edit: old_string not present.")
        if new_string is None:
            return ToolResult.fail("BAD_ARGUMENT", "new_string is required with old_string.",
                                   message="Provide new_string.")
        content = current.replace(old_string, new_string, 1)
    if content is None:
        return ToolResult.fail("BAD_ARGUMENT", "Provide content or old_string/new_string.",
                               message="Nothing to write.")
    result = _guard(sandbox.write_file, user_id, path, content, pid)
    if isinstance(result, ToolResult):
        return result
    verb = "Created" if result.get("created") else "Updated"
    return ToolResult.ok(f"{verb} {result['path']}.", result)


_PROJECT_TEMPLATES: dict[str, dict[str, str]] = {
    "python-cli": {
        "README.md": "# Project\n\nA Python command-line app.\n",
        "app.py": "def main():\n    print('hello')\n\n\nif __name__ == '__main__':\n    main()\n",
        "requirements.txt": "",
        ".gitignore": "__pycache__/\n*.pyc\n",
    },
    "fastapi-app": {
        "README.md": "# FastAPI project\n\n",
        "app/__init__.py": "",
        "app/main.py": (
            "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
            "@app.get('/')\ndef root():\n    return {'status': 'ok'}\n"
        ),
        "requirements.txt": "fastapi\nuvicorn\n",
        ".gitignore": "__pycache__/\n*.pyc\n",
    },
    "react-app": {
        "README.md": "# React project\n\n",
        "package.json": (
            "{\n  \"name\": \"app\",\n  \"version\": \"0.1.0\",\n  \"private\": true\n}\n"
        ),
        "index.html": (
            "<!doctype html>\n<html lang=\"en\">\n"
            "  <body>\n    <div id=\"root\"></div>\n  </body>\n</html>\n"
        ),
        "src/index.js": "console.log('hello')\n",
    },
}
_PROJECT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def create_project(name: str, template: str = "python-cli") -> ToolResult:
    """Scaffold a new project from a starter template into the workspace. `name`
    becomes a top-level folder; templates: python-cli, fastapi-app, react-app.
    The rest of the workspace is untouched (a new folder is created only)."""
    if not _PROJECT_NAME.fullmatch(name or ""):
        return ToolResult.fail(
            "INVALID_NAME",
            "Project name may only contain letters, digits, '-' and '_' (max 64).",
            message="Invalid project name.",
        )
    tmpl = _PROJECT_TEMPLATES.get(template)
    if tmpl is None:
        return ToolResult.fail(
            "INVALID_TEMPLATE", f"Unknown template '{template}'.",
            message="Unknown template.",
        )
    user_id = get_tool_context().user_id
    pid = _pid()
    created: list[str] = []
    for rel, content in tmpl.items():
        try:
            sandbox.create_file(user_id, f"{name}/{rel}", content, pid)
            created.append(rel)
        except sandbox.SandboxError as exc:
            return ToolResult.fail(exc.code, exc.detail, message=exc.detail)
    return ToolResult.ok(
        f"Scaffolded '{name}' ({template}): {len(created)} file(s).",
        {"name": name, "template": template, "files": created},
    )


async def edit_code_multi(edits: list[dict]) -> ToolResult:
    """Apply multiple file edits in a single tool call. Each item is
    ``{"path": ..., "old_string": ..., "new_string": ...}``. If ``old_string``
    is omitted, the file is (re)written with ``new_string`` as its full content.
    Previous content is backed up per file, so every edit still appears in the
    Diff tab and is rollback-able."""
    user_id = get_tool_context().user_id
    pid = _pid()
    applied: list[dict] = []
    failures: list[dict] = []
    total = len(edits or [])
    for item in edits or []:
        item = item or {}
        path = (str(item.get("path") or "")).strip()
        if not path:
            failures.append({"path": path, "error": "BAD_ARGUMENT",
                             "detail": "path is required"})
            continue
        old = item.get("old_string")
        new = item.get("new_string")
        try:
            if old is not None and new is not None:
                current = sandbox.read_file(user_id, path, project_id=pid)["content"]
                if old not in current:
                    failures.append({"path": path, "error": "OLD_STRING_NOT_FOUND",
                                     "detail": "old_string was not found in the file."})
                    continue
                content = current.replace(old, new, 1)
            elif new is not None:
                content = new
            else:
                failures.append({"path": path, "error": "BAD_ARGUMENT",
                                 "detail": "Provide old_string+new_string, or new_string content."})
                continue
            result = sandbox.write_file(user_id, path, content, pid)
            applied.append({"path": path, "created": result.get("created", False),
                            "diff": result.get("diff", "")})
        except sandbox.SandboxError as exc:
            failures.append({"path": path, "error": exc.code, "detail": exc.detail})
    if not applied:
        f = failures[0]
        return ToolResult.fail(
            f["error"], f["detail"],
            message=f"All {len(failures)} edit(s) failed (first: {f['path']} → {f['error']}).",
            data={"applied": [], "failures": failures},
        )
    msg = f"Applied {len(applied)}/{total} edit(s)."
    if failures:
        msg += f" {len(failures)} failed."
    return ToolResult.ok(msg, {"applied": applied, "failures": failures})


async def run_tests(target: str = "") -> ToolResult:
    """Run the workspace test suite (pytest) in the sandbox, optionally limited to a path or test id, and return captured output."""
    user_id = get_tool_context().user_id
    result = _guard(sandbox.run_tests, user_id, target, project_id=_pid())
    if isinstance(result, ToolResult):
        return result
    status = "passed" if result.get("ok") else "failed"
    message = f"Tests {status} (exit {result.get('exit_code')})."
    if result.get("timed_out"):
        message = "Tests timed out."
    return ToolResult.ok(message, result) if result.get("ok") else ToolResult.fail(
        "TESTS_FAILED", message, message=message, data=result)


async def run_command(argv: list[str], cwd: str = "") -> ToolResult:
    """Run an allowlisted command (python, pytest, read-only git; pip only if enabled) inside the coding workspace and return stdout, stderr and exit code."""
    user_id = get_tool_context().user_id
    result = _guard(sandbox.run_command, user_id, argv, cwd=cwd, project_id=_pid())
    if isinstance(result, ToolResult):
        return result
    status = "ok" if result.get("ok") else f"exit {result.get('exit_code')}"
    message = f"Ran `{result.get('command', '')}` ({status})."
    if result.get("timed_out"):
        message = "Command timed out."
    return ToolResult.ok(message, result) if result.get("ok") else ToolResult.fail(
        "COMMAND_FAILED", message, message=message, data=result)


async def run_linter(path: str = "") -> ToolResult:
    """Run a deterministic syntax check (Python compile) over the workspace, or a single file, and report diagnostics."""
    user_id = get_tool_context().user_id
    result = _guard(sandbox.run_linter, user_id, path, _pid())
    if isinstance(result, ToolResult):
        return result
    diags = result.get("diagnostics", [])
    if diags:
        return ToolResult.fail(
            "LINT_ERRORS", f"{len(diags)} file(s) with diagnostics.",
            message=f"{len(diags)} lint issue(s) found.", data=result)
    return ToolResult.ok(f"No syntax issues ({result.get('checked', 0)} file(s) checked).", result)


registry.register(list_files, permission=PermissionLevel.AUTO, category="coding",
                  tags=["coding", "read"])
registry.register(read_file, permission=PermissionLevel.AUTO, category="coding",
                  tags=["coding", "read"])
registry.register(inspect_code, permission=PermissionLevel.AUTO, category="coding",
                  tags=["coding", "read"])
registry.register(search_code, permission=PermissionLevel.AUTO, category="coding",
                  tags=["coding", "read"])
registry.register(analyze_error, permission=PermissionLevel.AUTO, category="coding",
                  tags=["coding", "read"])
registry.register(edit_code, permission=PermissionLevel.CONFIRM, category="coding",
                  tags=["coding", "write", "destructive", "file"])
registry.register(edit_code_multi, permission=PermissionLevel.CONFIRM, category="coding",
                  tags=["coding", "write", "destructive", "file"])
registry.register(create_project, permission=PermissionLevel.CONFIRM, category="coding",
                  tags=["coding", "scaffold", "write"])
registry.register(run_tests, permission=PermissionLevel.CONFIRM, category="coding",
                  tags=["coding", "exec"])
registry.register(run_command, permission=PermissionLevel.CONFIRM, category="coding",
                  tags=["coding", "exec"])
registry.register(run_linter, permission=PermissionLevel.CONFIRM, category="coding",
                  tags=["coding", "exec"])
