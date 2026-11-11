"""Phase J coding-agent tests (§53-54).

Acceptance flow: *"Clone/inspect this folder, fix the failing test, run the
tests, show me the diff"* — inspect the workspace, run the suite (fails), edit
the code through the CONFIRM-gated tool, re-run (passes), then show/roll back
the diff. Also verifies sandbox escape protection and the command allowlist.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.context import ToolContext, set_tool_context
from app.services import sandbox
from app.tools import registry
from app.tools.base import PermissionLevel


def _user(client, email="coder@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Coder",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _invoke(name: str, user_id: str, args: dict) -> dict:
    set_tool_context(ToolContext(user_id=user_id, run_id="r-j"))
    try:
        spec = registry.get(name)
        return json.loads(asyncio.run(spec.tool.ainvoke(args)))
    finally:
        set_tool_context(None)


def _workspace(user_id: str) -> Path:
    return sandbox.workspace_root(user_id)


# --- sandbox escape protection --------------------------------------------- #
def test_parent_traversal_rejected(client):
    _, user_id = _user(client, "esc1@test.com")
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.resolve_path(user_id, "../secrets.txt")
    assert exc.value.code == "PATH_ESCAPE"


def test_absolute_path_rejected(client):
    _, user_id = _user(client, "esc2@test.com")
    for raw in ("/etc/passwd", "C:/Windows/system32", "\\windows\\system32"):
        with pytest.raises(sandbox.SandboxError):
            sandbox.resolve_path(user_id, raw)


def test_null_byte_rejected(client):
    _, user_id = _user(client, "esc3@test.com")
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.resolve_path(user_id, "a\x00b")
    assert exc.value.code == "INVALID_PATH"


def test_symlink_escape_rejected(client, tmp_path):
    _, user_id = _user(client, "esc4@test.com")
    root = _workspace(user_id)
    outside = tmp_path / "outside.txt"
    outside.write_text("top secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.read_file(user_id, "link.txt")
    assert exc.value.code == "PATH_ESCAPE"


# --- command allowlist ----------------------------------------------------- #
def test_command_allowlist_blocks_shells(client):
    _, user_id = _user(client, "cmd1@test.com")
    for argv in (["bash", "-c", "echo hi"], ["rm", "-rf", "/"],
                 ["curl", "http://x"], ["git", "commit", "-m", "x"],
                 ["git", "push"]):
        with pytest.raises(sandbox.SandboxError) as exc:
            sandbox.run_command(user_id, argv)
        assert exc.value.code == "COMMAND_NOT_ALLOWED"


def test_python_runs_without_shell(client):
    _, user_id = _user(client, "cmd2@test.com")
    sandbox.write_file(user_id, "hello.py", "print('sandbox-ok')\n")
    result = sandbox.run_command(user_id, ["python", "hello.py"])
    assert result["ok"] is True
    assert "sandbox-ok" in result["stdout"]


def test_sandbox_env_has_no_secrets(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    _, user_id = _user(client, "cmd3@test.com")
    env = sandbox._sandbox_env(_workspace(user_id))
    assert "OPENAI_API_KEY" not in env


# --- edits, diff, rollback -------------------------------------------------- #
def test_write_diff_and_rollback(client):
    _, user_id = _user(client, "edit1@test.com")
    sandbox.write_file(user_id, "module.py", "x = 1\n")
    sandbox.write_file(user_id, "module.py", "x = 2\n")
    edits = sandbox.pending_edits(user_id)
    assert [e["path"] for e in edits] == ["module.py"]
    assert "-x = 1" in edits[0]["diff"] and "+x = 2" in edits[0]["diff"]
    restored = sandbox.rollback(user_id)
    assert restored == ["module.py"]
    assert sandbox.read_file(user_id, "module.py")["content"] == "x = 1\n"
    assert sandbox.pending_edits(user_id) == []


def test_rollback_deletes_created_file(client):
    _, user_id = _user(client, "edit2@test.com")
    sandbox.write_file(user_id, "new.txt", "hi")
    assert sandbox.rollback(user_id) == ["new.txt"]
    with pytest.raises(sandbox.SandboxError):
        sandbox.read_file(user_id, "new.txt")


# --- lint / inspect / search / analyze ------------------------------------- #
def test_run_linter_detects_syntax_error(client):
    _, user_id = _user(client, "lint1@test.com")
    sandbox.write_file(user_id, "good.py", "def f():\n    return 1\n")
    assert sandbox.run_linter(user_id)["ok"] is True
    sandbox.write_file(user_id, "bad.py", "def broken(:\n    pass\n")
    result = sandbox.run_linter(user_id)
    assert result["ok"] is False
    assert result["diagnostics"][0]["path"] == "bad.py"


def test_inspect_and_search_code(client):
    _, user_id = _user(client, "insp1@test.com")
    sandbox.write_file(user_id, "svc.py",
                       "import os\n\nclass A:\n    pass\n\ndef go():\n    return 'needle'\n")
    info = sandbox.inspect_code(user_id, "svc.py")
    assert "A" in info["classes"] and "go" in info["functions"]
    assert "os" in info["imports"]
    matches = sandbox.search_code(user_id, "needle")
    assert matches and matches[0]["path"] == "svc.py"


def test_analyze_error_structured():
    tb = (
        'Traceback (most recent call last):\n'
        '  File "app.py", line 3, in <module>\n'
        '    import missing_mod\n'
        "ModuleNotFoundError: No module named 'missing_mod'\n"
    )
    result = sandbox.analyze_error(tb)
    assert result["exception_type"].startswith("ModuleNotFoundError")
    assert result["last_frame"]["file"] == "app.py"
    assert "dependency" in (result["suggestion"] or "").lower()


# --- tool permissions ------------------------------------------------------- #
def test_tool_permission_levels():
    assert registry.get("inspect_code").permission == PermissionLevel.AUTO
    assert registry.get("search_code").permission == PermissionLevel.AUTO
    assert registry.get("analyze_error").permission == PermissionLevel.AUTO
    assert registry.get("edit_code").permission == PermissionLevel.CONFIRM
    assert registry.get("edit_code_multi").permission == PermissionLevel.CONFIRM
    assert registry.get("create_project").permission == PermissionLevel.CONFIRM
    assert registry.get("run_tests").permission == PermissionLevel.CONFIRM
    assert registry.get("run_linter").permission == PermissionLevel.CONFIRM


def test_edit_code_tool_replaces_substring(client):
    _, user_id = _user(client, "tool1@test.com")
    _invoke("edit_code", user_id, {"path": "calc.py", "content": "def add(a, b):\n    return a - b\n"})
    parsed = _invoke("edit_code", user_id, {
        "path": "calc.py", "old_string": "a - b", "new_string": "a + b",
    })
    assert parsed["success"] is True
    assert sandbox.read_file(user_id, "calc.py")["content"] == "def add(a, b):\n    return a + b\n"


def test_edit_code_missing_old_string_fails(client):
    _, user_id = _user(client, "tool2@test.com")
    _invoke("edit_code", user_id, {"path": "f.py", "content": "x = 1\n"})
    parsed = _invoke("edit_code", user_id, {
        "path": "f.py", "old_string": "nope", "new_string": "y",
    })
    assert parsed["success"] is False
    assert parsed["error"]["code"] == "OLD_STRING_NOT_FOUND"


def test_escape_via_tool_returns_structured_error(client):
    _, user_id = _user(client, "tool3@test.com")
    parsed = _invoke("edit_code", user_id, {"path": "../escape.txt", "content": "x"})
    assert parsed["success"] is False
    assert parsed["error"]["code"] == "PATH_ESCAPE"


# --- API -------------------------------------------------------------------- #
def test_workspace_api_tree_file_diff_rollback(client):
    headers, user_id = _user(client, "api1@test.com")
    sandbox.write_file(user_id, "notes.md", "v1")
    sandbox.write_file(user_id, "notes.md", "v2")

    tree = client.get("/api/workspace/tree", headers=headers)
    assert tree.status_code == 200
    assert [f["path"] for f in tree.json()["files"]] == ["notes.md"]

    file_resp = client.get("/api/workspace/file", headers=headers,
                           params={"path": "notes.md"})
    assert file_resp.status_code == 200
    assert file_resp.json()["content"] == "v2"

    diff = client.get("/api/workspace/diff", headers=headers)
    assert diff.status_code == 200
    assert "+v2" in diff.json()["edits"][0]["diff"]

    rolled = client.post("/api/workspace/rollback", headers=headers)
    assert rolled.status_code == 202
    approval = rolled.json()["approval"]
    approval_id = approval.get("approval_id") or approval.get("id")
    assert approval_id
    from app.database.session import SessionLocal
    from app.models.agent import AgentRun

    with SessionLocal() as db:
        run = db.get(AgentRun, rolled.json()["run_id"])
        assert run is not None
        assert run.state_json["tool_context_project_id"] == "legacy"
    assert sandbox.read_file(user_id, "notes.md")["content"] == "v2"

    completed = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert sandbox.read_file(user_id, "notes.md")["content"] == "v1"
    assert sandbox.pending_edits(user_id) == []


def test_workspace_api_blocks_escape(client):
    headers, _ = _user(client, "api2@test.com")
    assert client.get("/api/workspace/file",
                      params={"path": "../../etc/passwd"}, headers=headers).status_code == 400


def test_workspace_is_per_user(client):
    headers_a, user_a = _user(client, "own_a@test.com")
    sandbox.write_file(user_a, "secret.txt", "a-only")
    resp = client.get("/api/workspace/tree", headers=headers_a)
    paths = [f["path"] for f in resp.json()["files"]]
    assert "secret.txt" in paths
    # a different user never sees the first user's files
    headers_b, _ = _user(client, "own_b@test.com")
    other = client.get("/api/workspace/tree", headers=headers_b).json()["files"]
    assert all("secret.txt" not in f["path"] for f in other)


# --- acceptance: fix the failing test, show the diff ------------------------ #
def test_acceptance_fix_failing_test(client):
    _, user_id = _user(client, "accept@test.com")
    sandbox.write_file(user_id, "calc.py", "def add(a, b):\n    return a - b\n")
    sandbox.write_file(user_id, "test_calc.py", "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")

    before = sandbox.run_tests(user_id, "test_calc.py")
    assert before["ok"] is False
    analysis = sandbox.analyze_error(before["stdout"] + before["stderr"])
    assert analysis["exception_type"]

    _invoke("edit_code", user_id, {
        "path": "calc.py", "old_string": "a - b", "new_string": "a + b",
    })
    after = sandbox.run_tests(user_id, "test_calc.py")
    assert after["ok"] is True, after["stdout"] + after["stderr"]

    diff = sandbox.pending_edits(user_id)
    assert any("+    return a + b" in e["diff"] for e in diff)


# --- create_project scaffolding ---------------------------------------------- #
def test_create_project_scaffolds_tree(client):
    _, user_id = _user(client, "scaffold@test.com")
    parsed = _invoke("create_project", user_id,
                     {"name": "myproj", "template": "python-cli"})
    assert parsed["success"] is True, parsed
    files = parsed["data"]["files"]
    assert "app.py" in files and "README.md" in files
    root = _workspace(user_id)
    assert (root / "myproj" / "app.py").read_text().startswith("def main():")
    # the agent can immediately edit a scaffolded file
    parsed2 = _invoke("edit_code_multi", user_id, {"edits": [
        {"path": "myproj/app.py", "old_string": "print('hello')",
         "new_string": "print('hello, world')"},
    ]})
    assert parsed2["success"] is True, parsed2
    assert "world" in sandbox.read_file(user_id, "myproj/app.py")["content"]


def test_create_project_rejects_bad_name(client):
    _, user_id = _user(client, "sbad@test.com")
    parsed = _invoke("create_project", user_id, {"name": "../escape", "template": "python-cli"})
    assert parsed["success"] is False
    assert parsed["error"]["code"] == "INVALID_NAME"


def test_create_project_rejects_unknown_template(client):
    _, user_id = _user(client, "stpl@test.com")
    parsed = _invoke("create_project", user_id, {"name": "p", "template": "nope"})
    assert parsed["success"] is False
    assert parsed["error"]["code"] == "INVALID_TEMPLATE"


# --- edit_code_multi (multi-file edits) ------------------------------------- #
def test_edit_code_multi_applies_multiple(client):
    _, user_id = _user(client, "multi@test.com")
    sandbox.write_file(user_id, "a.py", "a = 1\n")
    sandbox.write_file(user_id, "b.py", "b = 1\n")
    parsed = _invoke("edit_code_multi", user_id, {"edits": [
        {"path": "a.py", "old_string": "a = 1", "new_string": "a = 2"},
        {"path": "b.py", "new_string": "b = 2\n"},  # full (re)write
    ]})
    assert parsed["success"] is True, parsed
    assert sandbox.read_file(user_id, "a.py")["content"] == "a = 2\n"
    assert sandbox.read_file(user_id, "b.py")["content"] == "b = 2\n"


def test_edit_code_multi_missing_old_string_fails(client):
    _, user_id = _user(client, "miss@test.com")
    sandbox.write_file(user_id, "m.py", "x = 1\n")
    parsed = _invoke("edit_code_multi", user_id, {"edits": [
        {"path": "m.py", "old_string": "nope", "new_string": "y"},
    ]})
    assert parsed["success"] is False
    assert parsed["error"]["code"] == "OLD_STRING_NOT_FOUND"


def test_edit_code_multi_rejects_path_escape(client):
    _, user_id = _user(client, "escm@test.com")
    parsed = _invoke("edit_code_multi", user_id, {"edits": [
        {"path": "../escape.txt", "new_string": "x"},
    ]})
    assert parsed["success"] is False
    assert parsed["error"]["code"] == "PATH_ESCAPE"
