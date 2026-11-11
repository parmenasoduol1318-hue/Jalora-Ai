"""Sandboxed coding workspace (§53-54).

Rules enforced here:
  * Per-user workspace under ``data/workspaces/<user_id>`` (optionally nested
    one level per project, ``data/workspaces/<user_id>/<project_id>``); every
    path is resolved inside it so ``..``, absolute paths and escaping symlinks
    are rejected with PATH_ESCAPE/INVALID_PATH.
  * Commands are allowlisted (python, pytest, read-only git) and executed
    without a shell under a wall-clock timeout and an output cap. The child
    environment is minimal and carries no secrets.
  * Edits back up the previous content so the UI can diff and roll back.
"""
from __future__ import annotations

import ast
import difflib
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from app.config import get_settings

_IGNORE_PARTS = ("__pycache__", ".git", "node_modules", ".jalora")
_TEXT_EXTENSIONS = {
    ".py", ".txt", ".md", ".json", ".toml", ".cfg", ".ini", ".yaml", ".yml",
    ".csv", ".html", ".js", ".ts", ".tsx", ".jsx", ".css", ".sh", ".sql",
    ".xml", ".rb", ".go", ".rs", ".java", ".c", ".cpp", ".h",
}
_GIT_READONLY = {
    "status", "diff", "log", "show", "branch", "ls-files", "rev-parse",
    "describe", "blame", "shortlog", "show-ref", "config",
}
_JOURNAL = ".jalora/edits.json"
_BACKUP_DIR = ".jalora/backups"
_MAX_OUTPUT_FILES = 500
_MAX_IMPORT_FILE_BYTES = 2 * 1024 * 1024
_MAX_IMPORT_TOTAL_BYTES = 20 * 1024 * 1024


class SandboxError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
# paths                                                                       #
# --------------------------------------------------------------------------- #
# A per-project workspace nests one level under the user's folder: a workspace
# ``Conversation`` is created with ``project="workspace"`` and the sandbox is
# keyed by the conversation id, so each project is an independent folder.
_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def workspace_root(user_id: str, project_id: str = "") -> Path:
    """Resolve the workspace directory for a user, optionally scoped to a
    project (one conversation). Empty ``project_id`` keeps the legacy
    per-user folder so existing data and chats are unaffected."""
    base = get_settings().workspaces_dir / str(user_id)
    if project_id:
        if not _SAFE_SCOPE.match(str(project_id)):
            raise SandboxError("INVALID_PROJECT", "Invalid project id.")
        base = base / str(project_id)
    root = base.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_path(user_id: str, rel: str, *, must_exist: bool = False,
                 project_id: str = "") -> Path:
    root = workspace_root(user_id, project_id)
    raw = (rel or "").strip().replace("\\", "/")
    if "\x00" in raw:
        raise SandboxError("INVALID_PATH", "Path contains a null byte.")
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise SandboxError("INVALID_PATH", "Absolute paths are not allowed.")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise SandboxError("PATH_ESCAPE", "Parent-directory traversal is not allowed.")
    candidate = (root / "/".join(parts)).resolve() if parts else root
    if candidate != root and root not in candidate.parents:
        raise SandboxError("PATH_ESCAPE", "Path escapes the workspace.")
    if must_exist and not candidate.exists():
        raise SandboxError("NOT_FOUND", f"'{rel}' does not exist in the workspace.")
    return candidate


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_hidden(rel_parts: tuple[str, ...]) -> bool:
    return any(part in _IGNORE_PARTS for part in rel_parts)


def _entry(root: Path, path: Path) -> dict:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"path": _rel(root, path), "size": size, "ext": path.suffix.lower()}


def list_files(user_id: str, subdir: str = "", project_id: str = "") -> list[dict]:
    root = workspace_root(user_id, project_id)
    base = resolve_path(user_id, subdir, project_id=project_id)
    if base.is_file():
        return [_entry(root, base)]
    out: list[dict] = []
    for path in sorted(base.rglob("*")):
        if len(out) >= _MAX_OUTPUT_FILES:
            break
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if _is_hidden(rel_parts) or not path.is_file():
            continue
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            continue  # skip symlinks pointing outside the workspace
        out.append(_entry(root, path))
    return out


def legacy_workspace_files(
    user_id: str, excluded_project_ids: set[str] | None = None,
) -> list[dict]:
    """List safe text files in the user's former shared workspace."""
    root = workspace_root(user_id)
    excluded = excluded_project_ids or set()
    out: list[dict] = []
    for path in sorted(root.rglob("*")):
        if len(out) >= _MAX_OUTPUT_FILES:
            break
        try:
            relative = path.relative_to(root)
            resolved = path.resolve()
        except (OSError, ValueError):
            continue
        if (relative.parts[0] in excluded or _is_hidden(relative.parts) or not path.is_file()
                or (resolved != root and root not in resolved.parents)
                or path.suffix.lower() not in _TEXT_EXTENSIONS):
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise SandboxError("IMPORT_READ_FAILED", "A legacy workspace file could not be inspected.") from exc
        if size <= _MAX_IMPORT_FILE_BYTES:
            out.append({"path": _rel(root, path), "size": size, "ext": path.suffix.lower()})
    return out


def import_legacy_workspace(
    user_id: str, project_id: str, excluded_project_ids: set[str] | None = None,
) -> dict[str, list[str]]:
    """Copy eligible legacy files to a project without overwriting originals."""
    workspace_root(user_id, project_id)
    imported: list[str] = []
    skipped: list[str] = []
    total_bytes = 0
    for entry in legacy_workspace_files(user_id, excluded_project_ids):
        rel = str(entry["path"])
        size = int(entry["size"])
        if total_bytes + size > _MAX_IMPORT_TOTAL_BYTES:
            skipped.append(rel)
            continue
        source = resolve_path(user_id, rel, must_exist=True)
        destination = resolve_path(user_id, rel, project_id=project_id)
        if destination.exists():
            skipped.append(rel)
            continue
        try:
            content = _read_text(source)
            create_file(user_id, rel, content, project_id=project_id)
        except (OSError, UnicodeError, SandboxError) as exc:
            raise SandboxError("IMPORT_COPY_FAILED", f"Could not import '{rel}'.") from exc
        imported.append(rel)
        total_bytes += size
    return {"imported": imported, "skipped": skipped}


def _read_text(path: Path) -> str:
    """Read text without newline translation (byte-exact across platforms)."""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read_file(user_id: str, rel: str, *, max_bytes: int = 200_000,
              project_id: str = "") -> dict:
    root = workspace_root(user_id, project_id)
    path = resolve_path(user_id, rel, must_exist=True, project_id=project_id)
    if path.is_dir():
        raise SandboxError("IS_DIRECTORY", f"'{rel}' is a directory.")
    data = path.read_bytes()
    return {"path": _rel(root, path),
            "content": data[:max_bytes].decode("utf-8", errors="replace"),
            "size": len(data), "truncated": len(data) > max_bytes}


# --------------------------------------------------------------------------- #
# edits + rollback                                                            #
# --------------------------------------------------------------------------- #
def unified_diff(old: str, new: str, path: str) -> str:
    old_lines = old.splitlines(keepends=True) or [""]
    new_lines = new.splitlines(keepends=True) or [""]
    return "".join(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}"))


def _journal_path(root: Path) -> Path:
    return root / _JOURNAL


def _load_journal(root: Path) -> list[dict]:
    path = _journal_path(root)
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return []


def _save_journal(root: Path, entries: list[dict]) -> None:
    path = _journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


def write_file(user_id: str, rel: str, content: str, project_id: str = "") -> dict:
    root = workspace_root(user_id, project_id)
    path = resolve_path(user_id, rel, project_id=project_id)
    if path == root:
        raise SandboxError("INVALID_PATH", "A file path is required.")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    previous = _read_text(path) if existed else ""
    backup_rel = f"{_BACKUP_DIR}/{_rel(root, path)}"
    (root / backup_rel).parent.mkdir(parents=True, exist_ok=True)
    _write_text(root / backup_rel, previous)
    _write_text(path, content or "")
    entries = [e for e in _load_journal(root) if e.get("path") != rel]
    entries.append({"path": rel, "backup": backup_rel, "created": not existed,
                    "previous": previous})
    _save_journal(root, entries)
    return {"path": rel, "size": path.stat().st_size, "created": not existed,
            "diff": unified_diff(previous, content or "", rel)}


def create_file(user_id: str, rel: str, content: str, project_id: str = "") -> dict:
    """Direct, non-journaled file write for UI uploads/editor saves.

    Unlike ``write_file`` this does *not* push an entry to the edit journal, so
    a user-uploaded or user-saved file is not reported as a pending *agent*
    edit (it won't appear in the Diff tab or be rollback-able via ``rollback``).
    Path-escape guards are re-enforced by ``resolve_path`` so callers can never
    write outside the per-user workspace.
    """
    root = workspace_root(user_id, project_id)
    path = resolve_path(user_id, rel, project_id=project_id)
    if path == root:
        raise SandboxError("INVALID_PATH", "A file path is required.")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    _write_text(path, content or "")
    return {"path": rel, "size": path.stat().st_size, "created": not existed}


def save_file(user_id: str, rel: str, content: str, project_id: str = "") -> dict:
    """Save editor content directly (non-journaled, like ``create_file``).

    User edits through the IDE should not pollute the agent's pending-edit
    journal; the Diff tab stays reserved for ``edit_code`` (agent) changes."""
    return create_file(user_id, rel, content, project_id=project_id)


def delete_file(user_id: str, rel: str, project_id: str = "") -> dict:
    """Delete a file (or empty directory) inside the workspace."""
    root = workspace_root(user_id, project_id)
    path = resolve_path(user_id, rel, must_exist=True, project_id=project_id)
    if path == root:
        raise SandboxError("INVALID_PATH", "A file path is required.")
    if path.is_dir():
        try:
            path.rmdir()
        except OSError as exc:
            raise SandboxError("DIRECTORY_NOT_EMPTY",
                               "Only empty directories can be deleted.") from exc
        return {"path": _rel(root, path), "kind": "directory"}
    path.unlink()
    # Drop any journal entry so a deleted file is not resurrected by rollback.
    entries = [e for e in _load_journal(root) if e.get("path") != rel]
    _save_journal(root, entries)
    return {"path": rel, "kind": "file"}


def delete_file_journaled(user_id: str, rel: str, project_id: str = "") -> dict:
    """Delete an agent-managed file while preserving rollback history."""
    root = workspace_root(user_id, project_id)
    path = resolve_path(user_id, rel, must_exist=True, project_id=project_id)
    if path == root or path.is_dir():
        raise SandboxError("INVALID_PATH", "A file path is required.")
    current = _read_text(path)
    entries = _load_journal(root)
    prior = next((entry for entry in entries if entry.get("path") == rel), None)
    previous = str(prior.get("previous", "")) if prior else current
    created = bool(prior.get("created", False)) if prior else False
    backup_rel = f"{_BACKUP_DIR}/{_rel(root, path)}"
    (root / backup_rel).parent.mkdir(parents=True, exist_ok=True)
    _write_text(root / backup_rel, previous)
    path.unlink()
    entries = [entry for entry in entries if entry.get("path") != rel]
    entries.append({
        "path": rel, "backup": backup_rel, "created": created,
        "previous": previous,
    })
    _save_journal(root, entries)
    return {"path": rel, "kind": "file", "deleted": True}


def rename_file(user_id: str, rel: str, new_rel: str, project_id: str = "") -> dict:
    """Rename/move a file or directory inside the workspace."""
    root = workspace_root(user_id, project_id)
    src = resolve_path(user_id, rel, must_exist=True, project_id=project_id)
    if src == root:
        raise SandboxError("INVALID_PATH", "A source path is required.")
    dst = resolve_path(user_id, new_rel, project_id=project_id)
    if dst == root:
        raise SandboxError("INVALID_PATH", "A destination path is required.")
    if dst.exists():
        raise SandboxError("ALREADY_EXISTS", f"'{new_rel}' already exists.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"path": _rel(root, dst), "from": rel, "kind": "file"}


def make_dir(user_id: str, rel: str, project_id: str = "") -> dict:
    """Create a directory inside the workspace."""
    root = workspace_root(user_id, project_id)
    path = resolve_path(user_id, rel, project_id=project_id)
    if path == root:
        raise SandboxError("INVALID_PATH", "A directory path is required.")
    path.mkdir(parents=True, exist_ok=True)
    return {"path": _rel(root, path), "kind": "directory"}


def pending_edits(user_id: str, project_id: str = "") -> list[dict]:
    root = workspace_root(user_id, project_id)
    out = []
    for entry in _load_journal(root):
        path = root / entry["path"]
        current = _read_text(path) if path.exists() else ""
        out.append({"path": entry["path"], "created": entry.get("created", False),
                    "diff": unified_diff(entry.get("previous", ""), current,
                                         entry["path"])})
    return out


def rollback(user_id: str, rel: str | None = None, project_id: str = "") -> list[str]:
    root = workspace_root(user_id, project_id)
    restored: list[str] = []
    remaining: list[dict] = []
    for entry in _load_journal(root):
        if rel is not None and entry["path"] != rel:
            remaining.append(entry)
            continue
        target = root / entry["path"]
        if entry.get("created"):
            if target.exists():
                target.unlink()
        else:
            backup = root / entry["backup"]
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_text(target, _read_text(backup))
        restored.append(entry["path"])
    _save_journal(root, remaining)
    return restored


# --------------------------------------------------------------------------- #
# command execution                                                           #
# --------------------------------------------------------------------------- #
def _resolve_argv(argv: list[str]) -> list[str]:
    if not argv:
        raise SandboxError("EMPTY_COMMAND", "A command is required.")
    program = str(argv[0]).strip()
    rest = [str(a) for a in argv[1:]]
    if len(rest) > 64:
        raise SandboxError("TOO_MANY_ARGS", "Too many command arguments.")
    lowered = program.lower()
    if lowered == "pytest":
        return [sys.executable, "-m", "pytest", *rest]
    if lowered in ("python", "python3", "py"):
        return [sys.executable, *rest]
    if lowered in ("pip", "pip3"):
        if not get_settings().sandbox_allow_pip:
            raise SandboxError(
                "COMMAND_NOT_ALLOWED",
                "pip is disabled in the sandbox (enable it in settings to install packages).")
        return [sys.executable, "-m", "pip", *rest]
    if lowered == "git":
        sub = rest[0].lower() if rest else ""
        if sub not in _GIT_READONLY:
            raise SandboxError(
                "COMMAND_NOT_ALLOWED",
                f"git '{sub or '(none)'}' is not allowed (read-only git only).")
        return ["git", *rest]
    raise SandboxError(
        "COMMAND_NOT_ALLOWED",
        f"Program '{program}' is not allowed. Allowed: python, pytest, git (read-only).")


def _sandbox_env(root: Path) -> dict:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(root),
        "USERPROFILE": str(root),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    for key in ("TMP", "TEMP"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    if os.name == "nt":
        for key in ("SystemRoot", "SystemDrive", "WINDIR", "PATHEXT",
                    "COMSPEC", "NUMBER_OF_PROCESSORS"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def run_command(user_id: str, argv: list[str], *, timeout: float | None = None,
                cwd: str = "", project_id: str = "") -> dict:
    settings = get_settings()
    if not settings.sandbox_enabled:
        raise SandboxError("SANDBOX_DISABLED", "The sandbox is disabled.")
    resolved = _resolve_argv(argv)
    root = workspace_root(user_id, project_id)
    workdir = resolve_path(user_id, cwd, project_id=project_id)
    if not workdir.is_dir():
        raise SandboxError("INVALID_CWD", "Working directory is not a directory.")
    limit = timeout or settings.sandbox_timeout_seconds
    cap = settings.sandbox_max_output_chars
    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            resolved, cwd=str(workdir), capture_output=True, text=True,
            timeout=limit, env=_sandbox_env(root), shell=False,
            encoding="utf-8", errors="replace",
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = f"Command timed out after {limit:.0f}s."
    except FileNotFoundError:
        raise SandboxError("PROGRAM_NOT_FOUND",
                           f"'{resolved[0]}' is not available in the sandbox.")
    return {
        "command": " ".join(resolved),
        "exit_code": exit_code,
        "ok": exit_code == 0 and not timed_out,
        "timed_out": timed_out,
        "stdout": stdout[:cap],
        "stderr": stderr[:cap],
        "truncated": len(stdout) > cap or len(stderr) > cap,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def run_tests(user_id: str, target: str = "", *, timeout: float | None = None,
              project_id: str = "") -> dict:
    argv = ["pytest", "-q", *([target] if target else [])]
    result = run_command(user_id, argv, timeout=timeout, project_id=project_id)
    result["kind"] = "tests"
    return result


def run_linter(user_id: str, target: str = "", project_id: str = "") -> dict:
    """Deterministic syntax lint (compile) over the workspace .py files."""
    root = workspace_root(user_id, project_id)
    if target:
        path = resolve_path(user_id, target, must_exist=True, project_id=project_id)
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
    else:
        files = sorted(root.rglob("*.py"))
    diagnostics: list[dict] = []
    checked = 0
    for path in files:
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if _is_hidden(rel_parts):
            continue
        if root not in path.resolve().parents:
            continue
        checked += 1
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            compile(source, str(path), "exec")
        except SyntaxError as exc:
            diagnostics.append({"path": _rel(root, path), "line": exc.lineno or 0,
                                "column": exc.offset or 0,
                                "message": exc.msg or "syntax error"})
        except OSError:
            continue
    return {"kind": "lint", "ok": not diagnostics, "checked": checked,
            "diagnostics": diagnostics}


# --------------------------------------------------------------------------- #
# code understanding                                                          #
# --------------------------------------------------------------------------- #
def inspect_code(user_id: str, rel: str, project_id: str = "") -> dict:
    root = workspace_root(user_id, project_id)
    path = resolve_path(user_id, rel, must_exist=True, project_id=project_id)
    if path.is_dir():
        return {"path": _rel(root, path), "kind": "directory",
                "entries": sorted(p.name for p in path.iterdir())}
    text = path.read_text(encoding="utf-8", errors="replace")
    summary: dict = {"path": _rel(root, path), "kind": "file",
                     "lines": text.count("\n") + 1, "size": len(text.encode("utf-8"))}
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            summary["syntax_error"] = f"{exc.msg} (line {exc.lineno})"
            return summary
        imports, classes, functions = [], [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
        summary.update({"imports": sorted(set(imports)),
                        "classes": classes, "functions": functions})
    return summary


def analyze_error(text: str) -> dict:
    """Extract a structured summary from a Python traceback / error log."""
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    frames: list[dict] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('File "') and ", line " in stripped:
            try:
                file_part = stripped.split('"')[1]
            except IndexError:
                file_part = stripped
            line_no = stripped.split(", line ")[1].split(",")[0].strip()
            frames.append({"file": file_part, "line": line_no})
    exc_type = exc_message = ""
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith(("File ", "Traceback", "During handling",
                                "The above exception")):
            continue
        if ":" in stripped:
            exc_type, _, exc_message = stripped.partition(":")
            break
    lowered = (exc_type + " " + exc_message).lower()
    suggestion = None
    if "modulenotfound" in lowered or "no module named" in lowered:
        suggestion = "A dependency is missing; install it or fix the import."
    elif "assert" in lowered:
        suggestion = "A test assertion failed; inspect the expected vs actual values."
    elif "indentationerror" in lowered or "syntaxerror" in lowered:
        suggestion = "Fix the syntax near the reported line."
    elif "typeerror" in lowered or "attributeerror" in lowered:
        suggestion = "Check the value types at the failing call."
    elif "filenotfound" in lowered:
        suggestion = "The path does not exist; verify it inside the workspace."
    return {"exception_type": exc_type.strip(), "message": exc_message.strip(),
            "frames": frames[-5:], "last_frame": frames[-1] if frames else None,
            "suggestion": suggestion, "raw_lines": len(lines)}


def search_code(user_id: str, query: str, subdir: str = "",
                *, regex: bool = False, limit: int = 50,
                project_id: str = "") -> list[dict]:
    if not (query or "").strip():
        raise SandboxError("EMPTY_QUERY", "A search query is required.")
    pattern = re.compile(query if regex else re.escape(query),
                         0 if regex else re.IGNORECASE)
    root = workspace_root(user_id, project_id)
    base = resolve_path(user_id, subdir, project_id=project_id)
    matches: list[dict] = []
    for path in sorted(base.rglob("*")):
        if len(matches) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in _TEXT_EXTENSIONS:
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if _is_hidden(rel_parts) or root not in path.resolve().parents:
            continue
        try:
            for n, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern.search(line):
                    matches.append({"path": _rel(root, path), "line": n,
                                    "text": line.strip()[:200]})
                    if len(matches) >= limit:
                        break
        except OSError:
            continue
    return matches


# --------------------------------------------------------------------------- #
# project export                                                              #
# --------------------------------------------------------------------------- #
def zip_project(user_id: str, project_id: str = "") -> bytes:
    """Build an in-memory zip of the project workspace.

    Every member is resolved strictly under the per-user project root, so path
    escape is impossible here (it is already enforced on every write, and on
    reads we only walk ``root.rglob`` and resolve+verify each path stays below
    the root — escaping symlinks are skipped). The ``.jalora`` journal/backup
    folder, ``.git``, ``node_modules`` and ``__pycache__`` are excluded so the
    archive contains only the user's own work.
    """
    root = workspace_root(user_id, project_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if _is_hidden(rel_parts):
                continue
            resolved = path.resolve()
            # final guard against symlinks that point outside the workspace
            if resolved != root and root not in resolved.parents:
                continue
            arcname = _rel(root, resolved)
            if (arcname.startswith("..") or arcname.startswith("/")
                    or (len(arcname) > 1 and arcname[1] == ":")):
                continue
            try:
                zf.writestr(arcname, resolved.read_bytes())
            except OSError:
                continue
    return buf.getvalue()
