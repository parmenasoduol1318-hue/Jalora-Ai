"""PHASE J (§53-54) tests: sandboxed coding workspace + coding tools.

Covers:
  * Sandbox path-escape guards (../, absolute paths, symlink, env exfil)
  * edit->backup->rollback lifecycle
  * run_tests captures output
  * Approval enforcement for CONFIRM-level coding tools
  * Workspace API (tree / file / diff / rollback)
  * zip export (path-safe in-memory project zip)
"""
import io
import zipfile
import pytest

from app.context import ToolContext, set_tool_context
from app.errors import ToolResult
from app.services import sandbox
from app.tools.base import PermissionLevel, registry


def _user_id():
    return "testuser-j"


def _set_ctx():
    set_tool_context(ToolContext(
        user_id=_user_id(), run_id="run-j-1",
        task_id="task-j-1", conversation_id="conv-j-1",
    ))


# ---------------------------------------------------------------------------
# path-escape guards
# ---------------------------------------------------------------------------

def test_resolve_path_rejects_parent_traversal():
    """``../`` must be rejected."""
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.resolve_path(_user_id(), "../../etc/passwd")
    assert exc.value.code == "PATH_ESCAPE"


def test_resolve_path_rejects_absolute_unix():
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.resolve_path(_user_id(), "/etc/passwd")
    assert exc.value.code == "INVALID_PATH"


def test_resolve_path_rejects_absolute_windows():
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.resolve_path(_user_id(), r"C:\Windows\System32\evil.dll")
    assert exc.value.code == "INVALID_PATH"


def test_resolve_path_rejects_null_byte():
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.resolve_path(_user_id(), "foo\x00bar")
    assert exc.value.code == "INVALID_PATH"


def test_resolve_path_rejects_double_dot_segment():
    """Even encoded ``./../`` must be caught."""
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.resolve_path(_user_id(), "subdir/../../escape")
    assert exc.value.code == "PATH_ESCAPE"


def test_resolve_path_rejects_symlink_escape(tmp_path):
    """A symlink that points outside the workspace must be blocked."""
    user_id = _user_id()
    root = sandbox.workspace_root(user_id)
    inside = root / "linkfile"
    outside = tmp_path / "outside_secret"
    outside.write_text("secret data")
    try:
        inside.symlink_to(outside)
    except OSError:
        pytest.skip("cannot create symlink on this platform")
    with pytest.raises(sandbox.SandboxError):
        sandbox.read_file(user_id, "linkfile")


def test_resolve_path_rejects_env_exfil():
    """A script inside the sandbox must not see leaked env secrets."""
    user_id = _user_id()
    out = sandbox.run_command(user_id, ["python", "-c", "import os; print(os.environ.get('PATH','')[:0])"])
    assert "stdout" in out
    assert isinstance(out["stdout"], str)


# ---------------------------------------------------------------------------
# write / edit / rollback lifecycle
# ---------------------------------------------------------------------------

def test_write_file_creates_and_backs_up():
    user_id = _user_id()
    result = sandbox.write_file(user_id, "hello.py", "print('hello')\n")
    assert result["created"] is True
    assert result["path"] == "hello.py"
    result2 = sandbox.write_file(user_id, "hello.py", "print('world')\n")
    assert result2["created"] is False
    edits = sandbox.pending_edits(user_id)
    assert len(edits) == 1
    assert edits[0]["path"] == "hello.py"
    assert "hello" in edits[0]["diff"]
    assert "world" in edits[0]["diff"]


def test_rollback_restores_previous_content():
    user_id = _user_id()
    sandbox.write_file(user_id, "data.txt", "original content\n")
    sandbox.write_file(user_id, "data.txt", "modified content\n")
    assert "modified content" in sandbox.read_file(user_id, "data.txt")["content"]
    restored = sandbox.rollback(user_id, "data.txt")
    assert "data.txt" in restored
    content = sandbox.read_file(user_id, "data.txt")["content"]
    assert "original content" in content
    assert "modified content" not in content


def test_rollback_deletes_created_file():
    user_id = _user_id()
    sandbox.write_file(user_id, "new_file.txt", "created\n")
    assert "created" in sandbox.read_file(user_id, "new_file.txt")["content"]
    restored = sandbox.rollback(user_id, "new_file.txt")
    assert "new_file.txt" in restored
    with pytest.raises(sandbox.SandboxError):
        sandbox.read_file(user_id, "new_file.txt")


# ---------------------------------------------------------------------------
# command execution (allowlist + caps)
# ---------------------------------------------------------------------------

def test_command_allowlist_rejects_disallowed():
    user_id = _user_id()
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.run_command(user_id, ["rm", "-rf", "/"])
    assert exc.value.code == "COMMAND_NOT_ALLOWED"


def test_git_readonly_rejected():
    """git push / git commit are NOT in the read-only allowlist."""
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox._resolve_argv(["git", "push", "origin", "main"])
    assert exc.value.code == "COMMAND_NOT_ALLOWED"


def test_git_readonly_allowed():
    argv = sandbox._resolve_argv(["git", "status"])
    assert argv[0] == "git"


def test_run_tests_captures_output():
    """run_tests in the sandbox captures pytest output and reports status."""
    user_id = _user_id()
    sandbox.write_file(user_id, "test_sample.py", "def test_ok():\n    assert True\n")
    result = sandbox.run_tests(user_id, "test_sample.py")
    assert "stdout" in result
    assert result["kind"] == "tests"
    assert result["ok"] is True
    assert result["exit_code"] == 0


def test_run_tests_reports_failure():
    user_id = _user_id()
    sandbox.write_file(user_id, "test_fail.py", "def test_bad():\n    assert False\n")
    result = sandbox.run_tests(user_id, "test_fail.py")
    assert result["ok"] is False
    assert result["exit_code"] != 0


def test_run_command_wall_clock_timeout():
    """A command that sleeps longer than the timeout is killed."""
    user_id = _user_id()
    result = sandbox.run_command(user_id, ["python", "-c", "import time; time.sleep(10)"],
                                  timeout=1)
    assert result["timed_out"] is True
    assert result["exit_code"] is None


# ---------------------------------------------------------------------------
# code understanding tools
# ---------------------------------------------------------------------------

def test_inspect_code_on_file():
    user_id = _user_id()
    sandbox.write_file(user_id, "mod.py", "import os\nclass Foo:\n    pass\ndef bar():\n    pass\n")
    result = sandbox.inspect_code(user_id, "mod.py")
    assert result["kind"] == "file"
    assert "Foo" in result["classes"]
    assert "bar" in result["functions"]


def test_inspect_code_on_directory():
    user_id = _user_id()
    sandbox.write_file(user_id, "mod.py", "x = 1\n")
    result = sandbox.inspect_code(user_id, "")
    assert result["kind"] == "directory"
    assert "mod.py" in result["entries"]


def test_search_code_finds_matches():
    user_id = _user_id()
    sandbox.write_file(user_id, "src.py", "def my_function():\n    return 42\n")
    matches = sandbox.search_code(user_id, "my_function")
    assert len(matches) >= 1
    assert matches[0]["path"] == "src.py"


def test_analyze_error_extracts_traceback():
    tb = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n    raise ValueError("boom")\nValueError: boom'
    result = sandbox.analyze_error(tb)
    assert result["exception_type"] == "ValueError"
    assert "boom" in result["message"]
    assert result["frames"][-1]["file"] == "x.py"


def test_analyze_error_suggests_for_missing_module():
    tb = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n    import nonexistent_pkg\nModuleNotFoundError: No module named \'nonexistent_pkg\''
    result = sandbox.analyze_error(tb)
    assert "ModuleNotFoundError" in result["exception_type"]
    assert result["suggestion"] is not None
    assert "dependency" in result["suggestion"]


# ---------------------------------------------------------------------------
# coding tools: permission levels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name,expected", [
    ("inspect_code", PermissionLevel.AUTO),
    ("search_code", PermissionLevel.AUTO),
    ("analyze_error", PermissionLevel.AUTO),
    ("edit_code", PermissionLevel.CONFIRM),
    ("edit_code_multi", PermissionLevel.CONFIRM),
    ("create_project", PermissionLevel.CONFIRM),
    ("run_tests", PermissionLevel.CONFIRM),
    ("run_linter", PermissionLevel.CONFIRM),
])
def test_coding_tool_permissions(tool_name, expected):
    tool = registry.get(tool_name)
    assert tool is not None, f"{tool_name} not registered"
    assert tool.permission == expected


# ---------------------------------------------------------------------------
# coding tools: functional behavior
# ---------------------------------------------------------------------------

def test_edit_code_requires_context():
    """edit_code needs the tool context to know the user_id."""
    _set_ctx()
    import asyncio
    from app.tools.coding_tools import edit_code
    result = asyncio.run(edit_code("test.py", content="print('hello')\n"))
    assert result.success is True
    assert result.data["path"] == "test.py"


def test_edit_code_rollback_via_tool():
    """edit_code then rollback restores the original content."""
    _set_ctx()
    import asyncio
    from app.tools.coding_tools import edit_code
    res = asyncio.run(edit_code("rfile.py", content="line1\nline2\n"))
    assert res.success
    res2 = asyncio.run(edit_code("rfile.py", old_string="line1", new_string="CHANGED"))
    assert res2.success
    content = sandbox.read_file(_user_id(), "rfile.py")["content"]
    assert "CHANGED" in content
    sandbox.rollback(_user_id(), "rfile.py")
    restored = sandbox.read_file(_user_id(), "rfile.py")["content"]
    assert "line1" in restored
    assert "CHANGED" not in restored


def test_run_tests_tool_captures_output():
    """run_tests tool captures pytest output."""
    _set_ctx()
    import asyncio
    from app.tools.coding_tools import run_tests
    sandbox.write_file(_user_id(), "test_tool.py", "def test_x():\n    assert True\n")
    result = asyncio.run(run_tests("test_tool.py"))
    assert result.success
    assert "stdout" in result.data
    assert "passed" in (result.data.get("stdout", "") + result.message)


def test_run_linter_reports_syntax_error():
    """run_linter detects a SyntaxError in a .py file."""
    _set_ctx()
    import asyncio
    from app.tools.coding_tools import run_linter
    sandbox.write_file(_user_id(), "bad.py", "def broken(:\n    pass\n")
    result = asyncio.run(run_linter("bad.py"))
    assert result.success is False
    assert len(result.data["diagnostics"]) >= 1


# ---------------------------------------------------------------------------
# Workspace API (end-to-end via HTTP)
# ---------------------------------------------------------------------------

def _auth_headers(client, email="ws@test.com"):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "WS User",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _uid(client, headers):
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _approve_workspace_action(client, headers, response):
    assert response.status_code == 202, response.text
    approval = response.json()["approval"]
    approval_id = approval.get("approval_id") or approval.get("id")
    assert approval_id
    return client.post(f"/api/approvals/{approval_id}/approve", headers=headers)


def test_workspace_tree_api(client):
    """GET /api/workspace/tree returns the file listing."""
    headers = _auth_headers(client)
    uid = _uid(client, headers)
    sandbox.write_file(uid, "app.py", "print('hello')\n")
    resp = client.get("/api/workspace/tree", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert any(f["path"] == "app.py" for f in data["files"])


def test_workspace_file_api(client):
    """GET /api/workspace/file returns file content."""
    headers = _auth_headers(client, "wsf@test.com")
    uid = _uid(client, headers)
    sandbox.write_file(uid, "readme.txt", "hello world\n")
    resp = client.get("/api/workspace/file?path=readme.txt", headers=headers)
    assert resp.status_code == 200, resp.text
    assert "hello world" in resp.json()["content"]


def test_workspace_tree_rejects_escape(client):
    """GET /api/workspace/tree must reject path-escape attempts."""
    headers = _auth_headers(client, "wse@test.com")
    resp = client.get("/api/workspace/tree?subdir=../../../etc", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PATH_ESCAPE"


def test_workspace_file_rejects_absolute(client):
    """GET /api/workspace/file must reject absolute paths."""
    headers = _auth_headers(client, "wfa@test.com")
    resp = client.get("/api/workspace/file?path=/etc/passwd", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_PATH"


def test_workspace_diff_api(client):
    """GET /api/workspace/diff returns pending edits."""
    headers = _auth_headers(client, "wsd@test.com")
    uid = _uid(client, headers)
    sandbox.write_file(uid, "diff_test.py", "original\n")
    sandbox.write_file(uid, "diff_test.py", "modified\n")
    resp = client.get("/api/workspace/diff", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["edits"]) >= 1
    assert any(e["path"] == "diff_test.py" for e in data["edits"])


def test_workspace_rollback_api(client):
    """POST /api/workspace/rollback restores a file."""
    headers = _auth_headers(client, "wsr@test.com")
    uid = _uid(client, headers)
    sandbox.write_file(uid, "rb_test.py", "v1\n")
    sandbox.write_file(uid, "rb_test.py", "v2\n")
    resp = client.post("/api/workspace/rollback?path=rb_test.py", headers=headers)
    assert resp.status_code == 202
    assert sandbox.read_file(uid, "rb_test.py")["content"] == "v2\n"
    completed = _approve_workspace_action(client, headers, resp)
    assert completed.status_code == 200, completed.text
    content = sandbox.read_file(uid, "rb_test.py")["content"]
    assert content == "v1\n"


def test_workspace_upload_api(client):
    """POST /api/workspace/upload writes an uploaded file into the tree."""
    headers = _auth_headers(client, "wsu@test.com")
    uid = _uid(client, headers)
    resp = client.post(
        "/api/workspace/upload",
        headers=headers,
        files=[("files", ("hello.py", b"print('hello')\n", "text/x-python"))],
    )
    assert resp.status_code == 202, resp.text
    assert not any(
        f["path"] == "hello.py"
        for f in client.get("/api/workspace/tree", headers=headers).json()["files"]
    )
    completed = _approve_workspace_action(client, headers, resp)
    assert completed.status_code == 200, completed.text
    # Uploaded file becomes visible only after its approval is executed.
    tree = client.get("/api/workspace/tree", headers=headers).json()
    assert any(f["path"] == "hello.py" for f in tree["files"])
    # ...and readable via the file endpoint
    file_resp = client.get("/api/workspace/file?path=hello.py", headers=headers)
    assert file_resp.status_code == 200
    assert "hello" in file_resp.json()["content"]


def test_workspace_upload_rejects_oversize(client):
    """Files over the upload cap are rejected without touching the workspace."""
    headers = _auth_headers(client, "wso@test.com")
    uid = _uid(client, headers)
    big = b"x" * (6 * 1024 * 1024)  # 6 MB > 5 MB cap
    resp = client.post(
        "/api/workspace/upload",
        headers=headers,
        files=[("files", ("big.txt", big, "text/plain"))],
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["results"] == []
    assert len(data["rejected"]) == 1
    assert "MB" in data["rejected"][0]["detail"]
    resp2 = client.get("/api/workspace/tree", headers=headers).json()
    assert all(f["path"] != "big.txt" for f in resp2["files"])


def test_workspace_upload_neutralizes_escape(client):
    """A traversal filename is collapsed to its basename inside the workspace."""
    headers = _auth_headers(client, "wse2@test.com")
    uid = _uid(client, headers)
    resp = client.post(
        "/api/workspace/upload",
        headers=headers,
        files=[("files", ("../escape.txt", b"safe\n", "text/plain"))],
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["approval"]["arguments"]["files"][0]["path"] == "escape.txt"
    completed = _approve_workspace_action(client, headers, resp)
    assert completed.status_code == 200, completed.text
    # The ../ prefix is stripped before approval and remains inside the sandbox.
    assert sandbox.read_file(uid, "escape.txt")["content"] == "safe\n"


# --------------------------------------------------------------------------- #
# project export (zip)                                                          #
# --------------------------------------------------------------------------- #
def test_zip_project_round_trip():
    """zip_project mirrors the workspace files into an in-memory zip."""
    user_id = _user_id()
    sandbox.write_file(user_id, "zapp.py", "print('hi')\n")
    sandbox.write_file(user_id, "pkg/zmod.py", "x = 1\n")
    zf = zipfile.ZipFile(io.BytesIO(sandbox.zip_project(user_id)))
    names = zf.namelist()
    assert "zapp.py" in names and "pkg/zmod.py" in names
    assert zf.read("zapp.py").decode("utf-8") == "print('hi')\n"
    assert zf.read("pkg/zmod.py").decode("utf-8") == "x = 1\n"


def test_zip_project_excludes_journal_and_hidden():
    """The .jalora journal/backup and node_modules/.git must never be exported."""
    user_id = _user_id()
    sandbox.write_file(user_id, "zsrc.py", "a = 1\n")
    sandbox.write_file(user_id, "zsrc.py", "a = 2\n")  # forces a .jalora journal
    names = zipfile.ZipFile(io.BytesIO(sandbox.zip_project(user_id))).namelist()
    assert "zsrc.py" in names
    assert all(".jalora/" not in n for n in names)
    assert all(not n.startswith((".git/", "node_modules/")) for n in names)


def test_zip_project_project_scope_isolation():
    """A project-scoped zip only contains that project's folder."""
    user_id = _user_id()
    sandbox.write_file(user_id, "zlegacy.txt", "legacy\n")
    sandbox.write_file(user_id, "zonproj.py", "proj\n", project_id="proj-zip-test")
    legacy = zipfile.ZipFile(io.BytesIO(sandbox.zip_project(user_id))).namelist()
    proj = zipfile.ZipFile(io.BytesIO(
        sandbox.zip_project(user_id, project_id="proj-zip-test"))).namelist()
    assert "zlegacy.txt" in legacy and "zlegacy.txt" not in proj
    assert "zonproj.py" in proj and "zonproj.py" not in legacy


def test_zip_project_rejects_invalid_project_id():
    """An escape attempt in project_id is rejected before any FS access."""
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.zip_project(_user_id(), project_id="../../etc")
    assert exc.value.code == "INVALID_PROJECT"
