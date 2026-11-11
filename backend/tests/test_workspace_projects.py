"""Workspace *project* tests: multi-project isolation on top of the coding
sandbox.

A workspace project is a Conversation with ``project="workspace"``; its id
scopes a per-project folder under ``data/workspaces/<user>/<project_id>``.
Covers:
  * sessions CRUD (create / list / rename / delete) + ownership checks
  * per-project sandbox isolation (files, exec, diff, rollback, upload)
  * editor endpoints (create / save / rename / delete / mkdir) scoped to a
    project
  * invalid project ids rejected
  * the coding agent's edits landing in the project folder (end-to-end)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import threading
import zipfile

import pytest
from app.database.session import SessionLocal
from app.models.conversation import Conversation
from app.models.task import Task, TaskStep
from app.models.user import User
from app.services import sandbox
from langchain_core.messages import AIMessage
from sqlalchemy import select


_WORKSPACE_ACTIONS = (
    "create_workspace_project",
    "rename_workspace_project",
    "archive_workspace_project",
    "save_workspace_file",
    "create_workspace_file",
    "delete_workspace_file",
    "rename_workspace_file",
    "create_workspace_directory",
    "rollback_workspace_edits",
    "upload_workspace_files",
    "run_workspace_command",
    "import_legacy_workspace_files",
)


def _user(client, email="proj@test.com", auto_workspace=True):
    resp = client.post("/api/auth/register", json={
        "email": email, "password": "longenough1", "name": "Proj User",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    if auto_workspace:
        with SessionLocal() as db:
            user = db.get(User, body["user"]["id"])
            user.prefs = {
                **(user.prefs or {}),
                "autonomy": 3,
                "tool_policies": {
                    **(user.prefs or {}).get("tool_policies", {}),
                    **{name: "automatic" for name in _WORKSPACE_ACTIONS},
                },
            }
            db.commit()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _approve_workspace_action(client, headers, response):
    assert response.status_code == 202, response.text
    approval = response.json().get("approval")
    assert approval, response.text
    approval_id = approval.get("approval_id") or approval.get("id")
    assert approval_id
    return client.post(f"/api/approvals/{approval_id}/approve", headers=headers)


def _session(client, headers, title="My project"):
    resp = client.post("/api/workspace/sessions", json={"title": title},
                       headers=headers)
    if resp.status_code == 202:
        completed = _approve_workspace_action(client, headers, resp)
        assert completed.status_code == 200, completed.text
        sessions = client.get("/api/workspace/sessions", headers=headers)
        assert sessions.status_code == 200, sessions.text
        matching = next(
            (session for session in sessions.json() if session["title"] == title),
            None,
        )
        assert matching is not None, sessions.text
        return matching["id"]
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["project"] == "workspace"
    return body["id"]


def _tree_paths(client, headers, project_id=""):
    resp = client.get("/api/workspace/tree", headers=headers,
                      params={"project_id": project_id})
    assert resp.status_code == 200, resp.text
    return [f["path"] for f in resp.json()["files"]]


# --------------------------------------------------------------------------- #
# sessions CRUD
# --------------------------------------------------------------------------- #
def test_session_create_list_rename_delete(client):
    headers, _ = _user(client, "ws_crud@test.com")
    pid = _session(client, headers, "Alpha")

    listed = client.get("/api/workspace/sessions", headers=headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [pid]
    assert listed.json()[0]["title"] == "Alpha"

    renamed = client.patch(f"/api/workspace/sessions/{pid}",
                           json={"title": "Beta"}, headers=headers)
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Beta"

    gone = client.delete(f"/api/workspace/sessions/{pid}", headers=headers)
    gone = _approve_workspace_action(client, headers, gone)
    assert gone.status_code == 200
    assert gone.json()["status"] == "completed"
    with SessionLocal() as db:
        archived = db.get(Conversation, pid)
        assert archived is not None
        assert archived.archived is True
        retained_tasks = db.scalars(
            select(Task).where(Task.conversation_id == pid)
        ).all()
        assert retained_tasks
    assert client.get("/api/workspace/sessions", headers=headers).json() == []


def test_workspace_project_creation_uses_shared_approval_and_task(client):
    headers, user_id = _user(
        client, "ws_project_approval@test.com", auto_workspace=False
    )
    pending = client.post(
        "/api/workspace/sessions",
        json={"title": "Approval required"},
        headers=headers,
    )
    assert pending.status_code == 202, pending.text
    approval = pending.json()["approval"]
    assert approval["task_id"] == pending.json()["task_id"]
    with SessionLocal() as db:
        assert db.scalar(
            select(Conversation.id).where(
                Conversation.user_id == user_id,
                Conversation.project == "workspace",
            )
        ) is None

    completed = _approve_workspace_action(client, headers, pending)
    assert completed.status_code == 200, completed.text
    with SessionLocal() as db:
        task = db.get(Task, pending.json()["task_id"])
        assert task is not None
        assert task.status == "completed"
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.tool_id == "create_workspace_project",
            )
        )
        assert step is not None
        project_id = step.result_data["execution_result"]["output"]["id"]
        project = db.get(Conversation, project_id)
        assert project is not None
        assert project.title == "Approval required"
        assert task.conversation_id == project_id


def test_sessions_excludes_normal_chat_conversations(client, fake_router):
    """Only ``project="workspace"`` conversations surface as sessions."""
    fake_router.script.append(AIMessage(content="hi"))
    headers, _ = _user(client, "ws_sep@test.com")
    resp = client.post("/api/chat", json={"message": "hello"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["conversation_id"]
    assert client.get("/api/workspace/sessions", headers=headers).json() == []


def test_session_requires_ownership(client):
    h_a, _ = _user(client, "ws_own_a@test.com")
    h_b, _ = _user(client, "ws_own_b@test.com")
    pid = _session(client, h_a)

    resp = client.patch(f"/api/workspace/sessions/{pid}",
                        json={"title": "hijacked"}, headers=h_b)
    assert resp.status_code == 404
    resp = client.delete(f"/api/workspace/sessions/{pid}", headers=h_b)
    assert resp.status_code == 404
    # the owner's session is untouched
    listed = client.get("/api/workspace/sessions", headers=h_a).json()
    assert [c["id"] for c in listed] == [pid]


def test_legacy_workspace_preview_and_import_are_safe(client):
    headers, uid = _user(client, "ws_legacy@test.com")
    project_id = _session(client, headers, "Import target")
    other_project_id = _session(client, headers, "Existing project")

    sandbox.write_file(uid, "legacy.py", "print('legacy')\n")
    sandbox.write_file(uid, "collision.txt", "legacy original")
    sandbox.write_file(uid, "collision.txt", "keep this project copy", project_id=project_id)
    sandbox.write_file(uid, "private.py", "other project data", project_id=other_project_id)

    preview = client.get("/api/workspace/legacy", headers=headers)
    assert preview.status_code == 200, preview.text
    preview_paths = {entry["path"] for entry in preview.json()["files"]}
    assert {"legacy.py", "collision.txt"} <= preview_paths
    assert "private.py" not in preview_paths

    imported = client.post(
        f"/api/workspace/sessions/{project_id}/import-legacy", headers=headers,
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported"] == ["legacy.py"]
    assert imported.json()["skipped"] == ["collision.txt"]
    assert "legacy.py" in imported.json()["imported"]
    assert "collision.txt" in imported.json()["skipped"]
    assert sandbox.read_file(uid, "legacy.py", project_id=project_id)["content"] == "print('legacy')\n"
    assert sandbox.read_file(uid, "collision.txt", project_id=project_id)["content"] == "keep this project copy"
    assert sandbox.read_file(uid, "legacy.py")["content"] == "print('legacy')\n"

    repeated = client.post(
        f"/api/workspace/sessions/{project_id}/import-legacy", headers=headers,
    )
    assert repeated.status_code == 200, repeated.text
    assert "legacy.py" in repeated.json()["skipped"]


def test_legacy_import_requires_project_ownership(client):
    owner_headers, owner_id = _user(client, "ws_legacy_owner@test.com")
    other_headers, _ = _user(client, "ws_legacy_other@test.com")
    project_id = _session(client, owner_headers)
    sandbox.write_file(owner_id, "legacy.txt", "owner data")

    preview = client.get("/api/workspace/legacy", headers=other_headers)
    assert preview.status_code == 200
    assert preview.json()["files"] == []
    imported = client.post(
        f"/api/workspace/sessions/{project_id}/import-legacy",
        headers=other_headers,
    )
    assert imported.status_code == 404


def test_workspace_direct_action_uses_shared_approval_before_mutation(client):
    headers, uid = _user(client, "ws_approval@test.com", auto_workspace=False)
    project_id = _session(client, headers)

    pending = client.post(
        "/api/workspace/file",
        json={"path": "approved.txt", "content": "approved content"},
        params={"project_id": project_id},
        headers=headers,
    )
    assert pending.status_code == 202, pending.text
    approval = pending.json()["approval"]
    assert approval["task_id"] == pending.json()["task_id"]
    assert approval["run_id"] == pending.json()["run_id"]
    with pytest.raises(sandbox.SandboxError):
        sandbox.read_file(uid, "approved.txt", project_id=project_id)

    completed = _approve_workspace_action(client, headers, pending)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert sandbox.read_file(
        uid, "approved.txt", project_id=project_id
    )["content"] == "approved content"


# --------------------------------------------------------------------------- #
# per-project isolation
# --------------------------------------------------------------------------- #
def test_projects_have_separate_folders(client):
    headers, uid = _user(client, "ws_proj@test.com")
    a = _session(client, headers, "A")
    b = _session(client, headers, "B")

    created = client.post("/api/workspace/file",
                          json={"path": "only_a.txt", "content": "secret-a"},
                          params={"project_id": a}, headers=headers)
    assert created.status_code == 201
    assert created.json()["created"] is True

    assert "only_a.txt" in _tree_paths(client, headers, a)
    assert "only_a.txt" not in _tree_paths(client, headers, b)
    assert "only_a.txt" not in _tree_paths(client, headers)  # legacy folder
    assert sandbox.read_file(uid, "only_a.txt", project_id=a)["content"] == "secret-a"
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.read_file(uid, "only_a.txt", project_id=b)
    assert exc.value.code == "NOT_FOUND"


def test_exec_scoped_to_project(client):
    headers, _ = _user(client, "ws_exec@test.com")
    a = _session(client, headers)
    client.post("/api/workspace/file",
                json={"path": "runme.py", "content": "print('proj-output')\n"},
                params={"project_id": a}, headers=headers)

    ok = client.post("/api/workspace/exec",
                     json={"argv": ["python", "runme.py"]},
                     params={"project_id": a}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert "proj-output" in ok.json()["stdout"]

    # the same command in another project cannot see the file
    miss = client.post("/api/workspace/exec",
                       json={"argv": ["python", "runme.py"]},
                       params={"project_id": _session(client, headers)}, headers=headers)
    assert miss.status_code == 200
    assert miss.json()["ok"] is False
    assert "runme.py" in miss.json()["stderr"]


def test_exec_allowlist_still_enforced(client):
    headers, _ = _user(client, "ws_deny@test.com")
    resp = client.post("/api/workspace/exec",
                       json={"argv": ["bash", "-c", "echo hi"]},
                       headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "COMMAND_NOT_ALLOWED"


def test_diff_and_rollback_scoped(client):
    headers, uid = _user(client, "ws_diff@test.com")
    a = _session(client, headers)
    b = _session(client, headers)

    sandbox.write_file(uid, "notes.md", "v1", project_id=a)
    sandbox.write_file(uid, "notes.md", "v2", project_id=a)

    diff_a = client.get("/api/workspace/diff", headers=headers,
                        params={"project_id": a}).json()["edits"]
    assert [e["path"] for e in diff_a] == ["notes.md"]
    assert "+v2" in diff_a[0]["diff"]

    # the other project (and the legacy folder) have no pending edits
    assert client.get("/api/workspace/diff", headers=headers,
                      params={"project_id": b}).json()["edits"] == []
    assert client.get("/api/workspace/diff", headers=headers).json()["edits"] == []

    rolled = client.post("/api/workspace/rollback", headers=headers,
                         params={"project_id": a})
    rolled = _approve_workspace_action(client, headers, rolled)
    assert rolled.status_code == 200
    assert rolled.json()["status"] == "completed"
    assert sandbox.read_file(uid, "notes.md", project_id=a)["content"] == "v1"


def test_upload_scoped_to_project(client):
    headers, _ = _user(client, "ws_up@test.com")
    a = _session(client, headers)
    resp = client.post(
        "/api/workspace/upload",
        headers=headers,
        files=[("files", ("tool.py", b"print('tool')\n", "text/x-python"))],
        data={"project_id": a},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["path"] == "tool.py"
    assert "tool.py" in _tree_paths(client, headers, a)
    assert "tool.py" not in _tree_paths(client, headers)


# --------------------------------------------------------------------------- #
# editor endpoints (project-scoped)
# --------------------------------------------------------------------------- #
def test_editor_crud_with_project(client):
    headers, _ = _user(client, "ws_edit@test.com")
    a = _session(client, headers)

    created = client.post("/api/workspace/file",
                          json={"path": "app.py", "content": "x = 1\n"},
                          params={"project_id": a}, headers=headers)
    assert created.status_code == 201
    assert created.json()["created"] is True

    saved = client.put("/api/workspace/file",
                       json={"path": "app.py", "content": "x = 2\n"},
                       params={"project_id": a}, headers=headers)
    assert saved.status_code == 200
    assert saved.json()["created"] is False

    read = client.get("/api/workspace/file", params={"path": "app.py",
                                                     "project_id": a},
                      headers=headers)
    assert read.status_code == 200
    assert read.json()["content"] == "x = 2\n"

    renamed = client.post("/api/workspace/rename",
                          json={"path": "app.py", "new_path": "main.py"},
                          params={"project_id": a}, headers=headers)
    assert renamed.status_code == 200
    assert renamed.json()["path"] == "main.py"
    assert renamed.json()["from_path"] == "app.py"
    assert "main.py" in _tree_paths(client, headers, a)

    mkdir = client.post("/api/workspace/mkdir", json={"path": "pkg"},
                        params={"project_id": a}, headers=headers)
    assert mkdir.status_code == 200
    assert mkdir.json()["kind"] == "directory"

    deleted = client.delete("/api/workspace/file",
                            params={"path": "main.py", "project_id": a},
                            headers=headers)
    deleted = _approve_workspace_action(client, headers, deleted)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "completed"
    assert "main.py" not in _tree_paths(client, headers, a)


def test_rename_into_existing_rejected(client):
    headers, _ = _user(client, "ws_dup@test.com")
    a = _session(client, headers)
    for name in ("one.txt", "two.txt"):
        client.post("/api/workspace/file", json={"path": name, "content": ""},
                    params={"project_id": a}, headers=headers)
    resp = client.post("/api/workspace/rename",
                       json={"path": "one.txt", "new_path": "two.txt"},
                       params={"project_id": a}, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "ALREADY_EXISTS"


def test_bad_project_id_rejected(client):
    """Arbitrary attempt to escape the project scope is rejected cleanly."""
    headers, _ = _user(client, "ws_scope@test.com")
    for project_id in ("../../etc", "a/b", "a b", "a.b"):
        resp = client.get("/api/workspace/tree", headers=headers,
                          params={"project_id": project_id})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_PROJECT"


# --------------------------------------------------------------------------- #
# the coding agent is bound to its project folder
# --------------------------------------------------------------------------- #
def test_coding_chat_rejects_non_workspace_conversation(client, fake_router):
    fake_router.script.append(AIMessage(content="hi"))
    headers, _ = _user(client, "ws_conv@test.com")
    normal = client.post("/api/chat", json={"message": "hi"}, headers=headers)
    assert normal.status_code == 200
    conv_id = normal.json()["conversation_id"]

    resp = client.post("/api/workspace/chat",
                       json={"message": "hi", "conversation_id": conv_id,
                             "stream": False},
                       headers=headers)
    assert resp.status_code == 404
    external = client.post(
        "/api/workspace/chat",
        json={
            "message": "hi",
            "conversation_id": conv_id,
            "stream": True,
            "model_provider": "codex",
            "external_agent_confirmed": True,
        },
        headers=headers,
    )
    assert external.status_code == 404


@pytest.mark.parametrize("provider", ["copilot", "codex"])
def test_external_coding_agents_use_canonical_approval_and_preview(
    client, fake_router, monkeypatch,
    provider,
):
    headers, user_id = _user(client, f"ws_external_{provider}@test.com")
    project_id = _session(client, headers)
    sandbox.create_file(
        user_id, "baseline.py", "print('unchanged')\n", project_id=project_id,
    )

    missing_confirmation = client.post(
        "/api/workspace/chat",
        json={
            "message": "Update the app",
            "conversation_id": project_id,
            "stream": True,
            "model_provider": "codex",
        },
        headers=headers,
    )
    assert missing_confirmation.status_code == 400
    assert missing_confirmation.json()["error"]["code"] == "EXTERNAL_AGENT_CONFIRMATION_REQUIRED"

    from app.services import workspace_agents

    async def fake_codex(_user_id, _project_id, _request, _key, changes_out):
        changes_out.append({
            "path": "baseline.py",
            "status": "updated",
            "diff": "-print('unchanged')\n+print('proposal')\n",
            "diff_truncated": False,
        })
        return "mock proposal"

    async def fake_copilot(
        _user_id, _project_id, _request, _key, changes_out, allowed_tool_names,
    ):
        assert "read_file" in allowed_tool_names
        assert "edit_code" in allowed_tool_names
        assert "run_command" not in allowed_tool_names
        changes_out.append({
            "path": "baseline.py",
            "status": "updated",
            "diff": "-print('unchanged')\n+print('proposal')\n",
            "diff_truncated": False,
        })
        return "mock proposal"

    monkeypatch.setattr(workspace_agents, "_agent_key", lambda *_args: "test-key")
    monkeypatch.setattr(workspace_agents, "_run_codex", fake_codex)
    monkeypatch.setattr(workspace_agents, "_run_copilot", fake_copilot)
    confirmed = client.post(
        "/api/workspace/chat",
        json={
            "message": "Update the app",
            "conversation_id": project_id,
            "stream": True,
            "model_provider": provider,
            "external_agent_confirmed": True,
        },
        headers=headers,
    )
    assert confirmed.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in confirmed.text.splitlines()
        if line.startswith("data: ")
    ]
    approval_event = next(event for event in events if event["type"] == "approval")
    approval_id = approval_event["approval_id"]
    with SessionLocal() as db:
        from app.models.agent import Approval

        approval = db.get(Approval, approval_id)
        assert approval is not None
        task_id = approval.task_id
        assert task_id
        task = db.get(Task, task_id)
        assert task is not None
        assert task.trigger_data["kind"] == "external_agent"
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.tool_id == "run_external_coding_agent",
            )
        )
        assert step is not None
        assert step.outcome_status == "awaiting_approval"

    completed = client.post(
        f"/api/approvals/{approval_id}/approve", headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert "No project files were changed." in completed.json()["reply"]
    assert "baseline.py" in completed.json()["reply"]
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        assert task is not None and task.status == "completed"
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.tool_id == "run_external_coding_agent",
            )
        )
        assert step is not None
        output = step.result_data["execution_result"]["output"]
        assert output["preview_only"] is True
        assert output["provider"] == provider
        if provider == "codex":
            assert output["file_changes"][0]["path"] == "baseline.py"
        else:
            assert output["reply"] == "mock proposal"
    assert sandbox.read_file(
        user_id, "baseline.py", project_id=project_id,
    )["content"] == "print('unchanged')\n"

    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None
        prefs = dict(user.prefs or {})
        tool_policies = dict(prefs.get("tool_policies") or {})
        tool_policies["save_workspace_file"] = "approval"
        prefs["tool_policies"] = tool_policies
        user.prefs = prefs
        db.commit()

    apply_pending = client.put(
        "/api/workspace/file",
        headers=headers,
        params={"project_id": project_id},
        json={"path": "baseline.py", "content": "print('proposal')\n"},
    )
    assert apply_pending.status_code == 202, apply_pending.text
    pending_data = apply_pending.json()
    assert pending_data["approval"]["tool_name"] == "save_workspace_file"
    assert sandbox.read_file(
        user_id, "baseline.py", project_id=project_id,
    )["content"] == "print('unchanged')\n"
    applied = client.post(
        f"/api/approvals/{pending_data['approval']['id']}/approve",
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "completed"
    assert sandbox.read_file(
        user_id, "baseline.py", project_id=project_id,
    )["content"] == "print('proposal')\n"


def test_external_agent_approval_rejection_recovers_after_backend_restart(
    client, fake_router, monkeypatch,
):
    headers, user_id = _user(client, "ws_external_restart@test.com")
    project_id = _session(client, headers)
    from app.services import workspace_agents

    monkeypatch.setattr(
        workspace_agents, "_agent_key",
        lambda *_args: pytest.fail("A rejected provider turn must not run."),
    )
    response = client.post(
        "/api/workspace/chat",
        headers=headers,
        json={
            "message": "Prepare a proposal",
            "conversation_id": project_id,
            "stream": True,
            "model_provider": "codex",
            "external_agent_confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    approval_event = next(event for event in events if event["type"] == "approval")
    approval_id = approval_event["approval_id"]

    script = """
import json
import os
import sys
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_router
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from conftest import FakeRouter
from langchain_core.messages import AIMessage

request = json.load(sys.stdin)
app.dependency_overrides[get_router] = lambda: FakeRouter([
    AIMessage(content="The external coding-agent request was rejected.")
])
with TestClient(app) as client:
    response = client.post(
        f"/api/approvals/{request['approval_id']}/reject",
        headers=request["headers"],
    )
    print("JALORA_RESTART_RESULT=" + json.dumps({
        "status_code": response.status_code,
        "body": response.json(),
    }))
"""
    restarted = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps({"approval_id": approval_id, "headers": headers}),
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
        cwd=os.getcwd(),
        env=os.environ.copy(),
    )
    assert restarted.returncode == 0, restarted.stderr
    result_line = next(
        (
            line.removeprefix("JALORA_RESTART_RESULT=")
            for line in reversed(restarted.stdout.splitlines())
            if line.startswith("JALORA_RESTART_RESULT=")
        ),
        None,
    )
    assert result_line is not None, restarted.stdout
    recovered = json.loads(result_line)
    assert recovered["status_code"] == 200, recovered
    assert recovered["body"]["status"] == "error", recovered
    assert "rejected" in recovered["body"]["reply"].lower()
    with SessionLocal() as db:
        from app.models.agent import Approval
        from app.models.task import TaskStepAttempt

        approval = db.get(Approval, approval_id)
        assert approval is not None and approval.decision_status == "rejected"
        task = db.get(Task, approval.task_id)
        assert task is not None and task.status == "failed"
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.tool_id == "run_external_coding_agent",
            )
        )
        assert step is not None
        assert db.scalar(
            select(TaskStepAttempt.id).where(TaskStepAttempt.step_id == step.id)
        ) is None
        assert task.user_id == user_id


def test_external_coding_agent_obeys_assisted_autonomy():
    from app.services.policy import decide
    from app.tools.base import registry

    spec = registry.get("run_external_coding_agent")
    assert spec is not None
    assert spec.side_effect == "external"
    assert spec.idempotent is False
    assert spec.retryable is False
    decision = decide(
        permission=spec.permission,
        autonomy=1,
        policy="",
        tags=spec.tags,
    )
    assert decision.allowed is False
    assert decision.code == "AUTONOMY_TOO_LOW"


def test_external_agent_preview_is_available_in_workspace_tool_details():
    from app.agents.graph import _live_tool_detail
    from app.errors import ToolResult

    detail = _live_tool_detail(
        "run_external_coding_agent",
        ToolResult.ok("preview", {
            "provider": "codex",
            "preview_only": True,
            "file_changes": [{
                "path": "app.py",
                "status": "updated",
                "diff": "-old\n+new\n",
                "diff_truncated": False,
            }],
            "reply": "proposal",
        }),
    )
    assert detail is not None
    assert detail["preview_only"] is True
    assert detail["file_changes"][0]["diff"] == "-old\n+new\n"
    assert detail["reply"] == "proposal"


def test_workspace_agent_status_is_secret_free(client):
    headers, _ = _user(client, "ws_external_status@test.com")
    response = client.get("/api/workspace/agents", headers=headers)
    assert response.status_code == 200
    providers = response.json()["providers"]
    assert set(providers) == {"copilot", "codex"}
    assert all({"sdk_installed", "configured", "available"} <= set(item) for item in providers.values())


def test_codex_stage_preview_tracks_changes_without_mutating_project(client, tmp_path):
    from app.services.workspace_agents import _stage_preview

    headers, user_id = _user(client, "ws_codex_sync@test.com")
    project_id = _session(client, headers)
    sandbox.create_file(user_id, "gone.py", "print('old')\n", project_id=project_id)
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "gone.py").write_text("print('old')\n", encoding="utf-8")
    (stage / "new.py").write_text("print('new')\n", encoding="utf-8")
    baseline = {"gone.py": "print('old')\n"}
    (stage / "gone.py").unlink()
    assert sandbox.read_file(user_id, "gone.py", project_id=project_id)["content"] == "print('old')\n"
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.read_file(user_id, "new.py", project_id=project_id)
    assert exc.value.code == "NOT_FOUND"
    changes = _stage_preview(baseline, stage)
    assert [(change["path"], change["status"]) for change in changes] == [
        ("gone.py", "deleted"),
        ("new.py", "created"),
    ]


def test_codex_adapter_scopes_turn_and_denies_unapproved_commands(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import openai_codex
    import openai_codex.client
    from app.services import workspace_agents

    stage = tmp_path / "isolated-stage"
    stage.mkdir()
    monkeypatch.setattr(workspace_agents, "_stage_project", lambda *_args: (stage, {}))
    monkeypatch.setattr(
        workspace_agents, "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )

    class FakeCodexClient:
        def __init__(self, config, approval_handler):
            self.config = config
            self.approval_handler = approval_handler

        def start(self):
            return None

        def initialize(self):
            return None

        def account_login_start(self, _params):
            return None

        def thread_start(self, params):
            assert params.cwd == str(stage)
            assert params.sandbox.value == "workspace-write"
            assert self.approval_handler("item/fileChange/requestApproval", {}) == {
                "decision": "accept",
            }
            assert self.approval_handler("item/commandExecution/requestApproval", {}) == {
                "decision": "decline",
            }
            return SimpleNamespace(thread=SimpleNamespace(id="thread-id"))

        def close(self):
            return None

    class FakeTurn:
        def run(self):
            (stage / "new.py").write_text("print('proposal')\n", encoding="utf-8")
            return SimpleNamespace(final_response="finished")

    class FakeThread:
        def __init__(self, _client, thread_id):
            assert thread_id == "thread-id"

        def turn(self, _prompt):
            return FakeTurn()

    monkeypatch.setattr(openai_codex.client, "CodexClient", FakeCodexClient)
    monkeypatch.setattr(openai_codex, "Thread", FakeThread)
    changes: list[dict[str, object]] = []
    monkeypatch.setattr(
        workspace_agents.sandbox, "write_file",
        lambda *_args, **_kwargs: pytest.fail("preview wrote into the project"),
    )
    monkeypatch.setattr(
        workspace_agents.sandbox, "delete_file_journaled",
        lambda *_args, **_kwargs: pytest.fail("preview deleted a project file"),
    )
    reply = asyncio.run(workspace_agents._run_codex(
        "user-id", "project-id", "change code", "test-key", changes,
    ))
    assert reply == "finished"
    assert changes[0]["path"] == "new.py"
    assert changes[0]["status"] == "created"
    assert not stage.exists()


def test_codex_cancellation_interrupts_staged_turn_without_project_writes(
    monkeypatch, tmp_path,
):
    from types import SimpleNamespace

    import openai_codex
    import openai_codex.client
    from app.services import workspace_agents

    stage = tmp_path / "cancelled-stage"
    stage.mkdir()
    run_started = threading.Event()
    interrupted = threading.Event()
    monkeypatch.setattr(workspace_agents, "_stage_project", lambda *_args: (stage, {}))
    monkeypatch.setattr(
        workspace_agents, "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    monkeypatch.setattr(
        workspace_agents.sandbox, "write_file",
        lambda *_args, **_kwargs: pytest.fail("cancelled preview wrote into the project"),
    )
    monkeypatch.setattr(
        workspace_agents.sandbox, "delete_file_journaled",
        lambda *_args, **_kwargs: pytest.fail("cancelled preview deleted a project file"),
    )

    class FakeCodexClient:
        def __init__(self, _config, approval_handler):
            self.approval_handler = approval_handler
            pass

        def start(self):
            return None

        def initialize(self):
            return None

        def account_login_start(self, _params):
            return None

        def thread_start(self, _params):
            return SimpleNamespace(thread=SimpleNamespace(id="thread-id"))

        def close(self):
            return None

    class FakeTurn:
        def run(self):
            run_started.set()
            interrupted.wait(timeout=5)
            return SimpleNamespace(final_response="interrupted")

        def interrupt(self):
            interrupted.set()

    class FakeThread:
        def __init__(self, _client, _thread_id):
            pass

        def turn(self, _prompt):
            return FakeTurn()

    monkeypatch.setattr(openai_codex.client, "CodexClient", FakeCodexClient)
    monkeypatch.setattr(openai_codex, "Thread", FakeThread)

    async def cancel_adapter():
        changes: list[dict[str, object]] = []
        run = asyncio.create_task(workspace_agents._run_codex(
            "user-id", "project-id", "change code", "test-key", changes,
        ))
        assert await asyncio.to_thread(run_started.wait, 3)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    asyncio.run(cancel_adapter())
    assert interrupted.is_set()
    assert not stage.exists()


def test_copilot_receives_only_policy_allowed_staged_workspace_tools():
    from types import SimpleNamespace

    from app.tools.external_agent import _copilot_staging_tools

    default_user = SimpleNamespace(prefs={"autonomy": 2})
    copilot_tools, copilot_error = _copilot_staging_tools(default_user)
    assert copilot_error is None
    assert "read_file" in copilot_tools
    assert "edit_code" in copilot_tools
    assert "run_command" not in copilot_tools

    assisted_user = SimpleNamespace(prefs={"autonomy": 1})
    copilot_tools, copilot_error = _copilot_staging_tools(assisted_user)
    assert copilot_error is None
    assert "read_file" in copilot_tools
    assert "edit_code" not in copilot_tools

    denied_user = SimpleNamespace(prefs={
        "autonomy": 3,
        "tool_policies": {
            "read_file": "denied",
            "list_files": "denied",
            "inspect_code": "denied",
            "search_code": "denied",
            "analyze_error": "denied",
        },
    })
    copilot_tools, copilot_error = _copilot_staging_tools(denied_user)
    assert copilot_error is not None
    assert "edit_code" not in copilot_tools

    from app.tools.base import PermissionLevel, registry

    automatic_policies = {
        spec.name: "automatic"
        for spec in registry.all()
        if spec.category == "coding"
        and spec.permission >= PermissionLevel.CONFIRM
    }
    autonomous_user = SimpleNamespace(prefs={
        "autonomy": 3,
        "tool_policies": automatic_policies,
    })
    copilot_tools, copilot_error = _copilot_staging_tools(autonomous_user)
    assert copilot_error is None
    assert "edit_code" in copilot_tools
    assert "run_command" not in copilot_tools


def test_copilot_adapter_disables_builtins_and_rejects_permissions(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import copilot
    from app.services import workspace_agents
    from app.tools.base import PermissionLevel, ToolSpec
    from langchain_core.tools import StructuredTool
    from copilot.rpc import PermissionDecisionReject

    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.setattr(workspace_agents.sandbox, "workspace_root", lambda *_args: workdir)
    monkeypatch.setattr(
        workspace_agents, "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    executed: list[str] = []

    async def required_workspace_tool(required_value: str) -> str:
        executed.append(required_value)
        return "ran"

    tool = StructuredTool.from_function(
        coroutine=required_workspace_tool,
        name="required_workspace_tool",
        description="A test tool with required arguments.",
    )
    spec = ToolSpec(
        name="required_workspace_tool",
        tool=tool,
        permission=PermissionLevel.AUTO,
        category="coding",
    )
    monkeypatch.setattr(workspace_agents, "_coding_tool_specs", lambda: [spec])

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_and_wait(self, _prompt, *, timeout):
            assert timeout == 600.0
            return SimpleNamespace(data=SimpleNamespace(content="complete"))

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["mode"] == "empty"
            assert kwargs["working_directory"] == str(workdir)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def create_session(self, **kwargs):
            assert kwargs["available_tools"] == []
            assert kwargs["tools"]
            assert isinstance(kwargs["on_permission_request"](None, None), PermissionDecisionReject)
            invalid_result = await kwargs["tools"][0].handler(SimpleNamespace(
                tool_call_id="invalid-arguments-call",
                arguments={},
            ))
            assert invalid_result.result_type == "failure"
            assert "INVALID_TOOL_ARGUMENTS" in invalid_result.text_result_for_llm
            assert executed == []
            return FakeSession()

    monkeypatch.setattr(copilot, "CopilotClient", FakeClient)
    reply = asyncio.run(workspace_agents._run_copilot(
        "user-id", "project-id", "change code", "test-token",
        [], {"required_workspace_tool"},
    ))
    assert reply == "complete"


def test_copilot_stages_edits_and_never_mutates_the_selected_project(
    monkeypatch, tmp_path,
):
    from pathlib import Path
    from types import SimpleNamespace

    import copilot
    from app.services import workspace_agents

    user_id = "copilot-stage-user"
    project_id = "copilot-stage-project"
    source = tmp_path / "source"
    source.mkdir()
    stage_root = tmp_path / "staging"

    def workspace_root(_user_id, selected_project=""):
        root = source if selected_project == project_id else stage_root / selected_project
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(workspace_agents.sandbox, "workspace_root", workspace_root)
    monkeypatch.setattr(
        workspace_agents, "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    sandbox.write_file(user_id, "app.py", "print('old')\n", project_id=project_id)
    stage_dirs: list[str] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_and_wait(self, _prompt, *, timeout):
            assert timeout == 600.0
            return SimpleNamespace(data=SimpleNamespace(content="staged edit"))

    class FakeClient:
        def __init__(self, **kwargs):
            stage_dirs.append(kwargs["working_directory"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def create_session(self, **kwargs):
            edit_tool = next(tool for tool in kwargs["tools"] if tool.name == "edit_code")
            result = await edit_tool.handler(SimpleNamespace(arguments={
                "path": "app.py",
                "content": "print('proposal')\n",
            }))
            assert result.result_type == "success"
            return FakeSession()

    monkeypatch.setattr(copilot, "CopilotClient", FakeClient)
    changes: list[dict[str, object]] = []
    reply = asyncio.run(workspace_agents._run_copilot(
        user_id,
        project_id,
        "change app.py",
        "test-token",
        changes,
        {"read_file", "edit_code"},
    ))
    assert reply == "staged edit"
    assert sandbox.read_file(user_id, "app.py", project_id=project_id)["content"] == "print('old')\n"
    assert changes[0]["path"] == "app.py"
    assert changes[0]["status"] == "updated"
    assert "print('proposal')" in changes[0]["diff"]
    assert stage_dirs and not Path(stage_dirs[0]).exists()


def test_coding_agent_edits_land_in_project_folder(client, fake_router):
    """The workspace agent's ``edit_code`` writes into the project folder while
    the default per-user folder stays untouched."""
    headers, uid = _user(client, "ws_agent@test.com")
    # autonomy 3 + "automatic" policy lets the CONFIRM-gated edit run unpaused
    patched = client.patch("/api/settings", json={
        "autonomy": 3,
        "tool_policies": {"edit_code": "automatic"},
    }, headers=headers)
    assert patched.status_code == 200, patched.text

    pid = _session(client, headers, "Agent project")

    fake_router.script.append(AIMessage(content="", tool_calls=[{
        "name": "edit_code",
        "args": {"path": "managed.py", "content": "def answer():\n    return 42\n"},
        "id": "call-edit-1",
        "type": "tool_call",
    }]))
    fake_router.script.append(AIMessage(content="edited the file."))

    resp = client.post("/api/workspace/chat",
                       json={"message": "write managed.py with answer()",
                             "conversation_id": pid, "stream": False},
                       headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["intent"] == "coding"
    assert body["reply"] == "edited the file."

    # the edit landed in the project folder, journaled for diff/rollback
    content = sandbox.read_file(uid, "managed.py", project_id=pid)["content"]
    assert "return 42" in content
    assert [e["path"] for e in sandbox.pending_edits(uid, pid)] == ["managed.py"]
    # ...and NOT in the default per-user folder
    with pytest.raises(sandbox.SandboxError) as exc:
        sandbox.read_file(uid, "managed.py")
    assert exc.value.code == "NOT_FOUND"

    with SessionLocal() as db:
        conv = db.get(Conversation, pid)
        assert conv is not None and conv.project == "workspace"


# --------------------------------------------------------------------------- #
# project export (GET /api/workspace/export)                                  #
# --------------------------------------------------------------------------- #
def test_export_returns_zip(client):
    headers, uid = _user(client, "exz@test.com")
    pid = _session(client, headers, "Export project")
    sandbox.write_file(uid, "app.py", "print('export')\n", project_id=pid)
    resp = client.get("/api/workspace/export", headers=headers,
                      params={"project_id": pid})
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("Content-Type") == "application/zip"
    cd = resp.headers.get("Content-Disposition", "")
    assert "attachment" in cd and "workspace.zip" in cd
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert "app.py" in names
    assert zf.read("app.py").decode("utf-8") == "print('export')\n"
    # the .jalora journal / backups must never leak into the download
    assert all(".jalora/" not in n for n in names)


def test_export_requires_project_id(client):
    headers, _ = _user(client, "ex0@test.com")
    resp = client.get("/api/workspace/export", headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_export_bad_project_rejected(client):
    headers, _ = _user(client, "ex1@test.com")
    resp = client.get("/api/workspace/export", headers=headers,
                      params={"project_id": "../../etc"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_PROJECT"