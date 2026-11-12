from __future__ import annotations

import ctypes
from datetime import timedelta

import pytest
from langchain_core.messages import AIMessage

from app.api import devices as devices_api
from app.database.session import SessionLocal
from app.errors import BadRequestError, ConflictError
from app.models.device import DeviceConnection, DeviceMessage
from app.models.task import Task, TaskStep
from app.models.user import utcnow
from app.services import local_device_control
from sqlalchemy import select


def _approve_direct_action(client, headers, response):
    assert response.status_code == 202, response.text
    pending = response.json()
    assert pending["status"] == "paused_approval"
    approval = pending["approval"]
    assert approval["task_id"] == pending["task_id"]
    assert approval["run_id"] == pending["run_id"]
    approved = client.post(
        f"/api/approvals/{approval['id']}/approve", headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    return approved.json()


def _create_device(client, headers, scopes=None):
    return client.post(
        "/api/devices",
        headers=headers,
        json={
            "name": "Test phone",
            "platform": "android",
            "scopes": scopes or ["messages.read", "messages.draft", "messages.send"],
            "consent_confirmed": True,
        },
    )


def test_device_pairing_requires_consent_and_valid_scope(client, auth_headers):
    headers = auth_headers("device-consent@test.com")
    no_consent = client.post(
        "/api/devices",
        headers=headers,
        json={
            "name": "Phone",
            "platform": "android",
            "scopes": ["messages.read"],
            "consent_confirmed": False,
        },
    )
    assert no_consent.status_code == 400
    invalid_scope = client.post(
        "/api/devices",
        headers=headers,
        json={
            "name": "Phone",
            "platform": "android",
            "scopes": ["calls.answer"],
            "consent_confirmed": True,
        },
    )
    assert invalid_scope.status_code == 400


def test_device_scope_catalog_matches_supported_browser_companion_permissions(
    client, auth_headers
):
    headers = auth_headers("device-scope-catalog@test.com")
    response = client.get("/api/devices/scopes", headers=headers)

    assert response.status_code == 200
    result = response.json()
    assert {scope["id"] for scope in result["scopes"]} == {
        "messages.read",
        "messages.draft",
        "messages.send",
        "remote.browser.open_url",
    }
    assert "cannot inspect or operate other apps" in result["note"]


def test_device_capability_inventory_is_live_scoped_and_honest(client, auth_headers):
    assert client.get("/api/devices/capabilities").status_code == 401
    headers = auth_headers("device-capabilities@test.com")
    other_headers = auth_headers("other-device-capabilities@test.com")
    owned = _create_device(
        client,
        headers,
        scopes=[
            "messages.read",
            "messages.draft",
            "messages.send",
        ],
    )
    other = _create_device(client, other_headers)
    assert owned.status_code == 200, owned.text
    assert other.status_code == 200, other.text
    own_device = owned.json()

    heartbeat = client.get(
        "/api/devices/actions",
        headers={"Authorization": f"Bearer {own_device['device_token']}"},
    )
    assert heartbeat.status_code == 200, heartbeat.text

    response = client.get("/api/devices/capabilities", headers=headers)
    assert response.status_code == 200, response.text
    inventory = response.json()
    assert inventory["schema_version"] == 1
    assert [device["id"] for device in inventory["devices"]] == [own_device["id"]]
    device = inventory["devices"][0]
    assert device["status"] == "online"
    capabilities = {
        item["operation"]: item
        for item in device["companion_capabilities"]
    }
    for operation in ("messages.receive", "messages.draft", "messages.send"):
        assert capabilities[operation]["status"] == "registered_unavailable"
    assert capabilities["messages.send"]["approval_required"] is True
    assert capabilities["browser.open_url"]["status"] == "permission_required"
    assert all(
        item["status"] == "unsupported"
        for item in device["unsupported_operations"]
    )
    assert "cannot inspect screens" in " ".join(inventory["limitations"]).lower()
    assert "device_token" not in str(inventory)
    assert own_device["device_token"] not in str(inventory)


def test_device_message_reply_is_edited_approved_and_delivered(client, auth_headers):
    user_headers = auth_headers("device-flow@test.com")
    created = _create_device(client, user_headers)
    assert created.status_code == 200, created.text
    result = created.json()
    token = result["device_token"]
    device_id = result["id"]
    with SessionLocal() as db:
        row = db.get(DeviceConnection, device_id)
        assert row is not None
        assert row.token_hash != token

    device_headers = {"Authorization": f"Bearer {token}"}
    received = client.post(
        "/api/devices/inbox",
        headers=device_headers,
        json={
            "external_id": "notification-123",
            "source_app": "Messages",
            "sender": "A contact",
            "content": "Can you call me later?",
        },
    )
    assert received.status_code == 200, received.text
    message = received.json()
    duplicate = client.post(
        "/api/devices/inbox",
        headers=device_headers,
        json={
            "external_id": "notification-123",
            "source_app": "Messages",
            "sender": "A contact",
            "content": "Can you call me later?",
        },
    )
    assert duplicate.json()["id"] == message["id"]

    draft = client.patch(
        f"/api/devices/inbox/{message['id']}",
        headers=user_headers,
        json={"draft_reply": "Yes, I can call at 3."},
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["status"] == "completed"
    assert draft.json()["task_id"]
    with SessionLocal() as db:
        task = db.get(Task, draft.json()["task_id"])
        assert task is not None
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.tool_id == "save_device_reply_draft",
            )
        )
        assert step is not None
        assert step.result_data["execution_result"]["output"]["draft_reply"] == (
            "Yes, I can call at 3."
        )
    not_confirmed = client.post(
        f"/api/devices/inbox/{message['id']}/approve",
        headers=user_headers,
        json={"reply": "Edited: I'll call at 3.", "confirm_send": False},
    )
    assert not_confirmed.status_code == 400

    approved = client.post(
        f"/api/devices/inbox/{message['id']}/approve",
        headers=user_headers,
        json={"reply": "Edited: I'll call at 3.", "confirm_send": True},
    )
    _approve_direct_action(client, user_headers, approved)
    queued = client.get("/api/devices/inbox", headers=user_headers)
    assert queued.status_code == 200
    saved_message = next(row for row in queued.json() if row["id"] == message["id"])
    assert saved_message["draft_reply"] == "Edited: I'll call at 3."
    assert saved_message["action_status"] == "queued"

    actions = client.get("/api/devices/actions", headers=device_headers)
    assert actions.status_code == 200, actions.text
    assert actions.json() == [
        {
            "id": message["id"],
            "external_id": "notification-123",
            "source_app": "Messages",
            "reply": "Edited: I'll call at 3.",
        }
    ]
    acknowledged = client.post(
        f"/api/devices/actions/{message['id']}/ack", headers=device_headers
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "delivered"


def test_device_message_delete_uses_shared_strict_approval(
    client, auth_headers, fake_router
):
    headers = auth_headers("device-message-delete@test.com")
    paired = _create_device(client, headers)
    assert paired.status_code == 200, paired.text
    received = client.post(
        "/api/devices/inbox",
        headers={"Authorization": f"Bearer {paired.json()['device_token']}"},
        json={
            "external_id": "delete-me",
            "source_app": "Messages",
            "sender": "A contact",
            "content": "An unsent notification.",
        },
    )
    assert received.status_code == 200, received.text
    message_id = received.json()["id"]

    pending = client.delete(
        f"/api/devices/inbox/{message_id}", headers=headers,
    )
    assert pending.status_code == 202, pending.text
    with SessionLocal() as db:
        assert db.get(DeviceMessage, message_id) is not None
        task = db.get(Task, pending.json()["task_id"])
        assert task is not None
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.tool_id == "delete_device_message",
            )
        )
        assert step is not None
        assert step.status != "done"

    completed = _approve_direct_action(client, headers, pending)
    assert completed["status"] == "completed"
    with SessionLocal() as db:
        assert db.get(DeviceMessage, message_id) is None
        task = db.get(Task, pending.json()["task_id"])
        assert task is not None
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.tool_id == "delete_device_message",
            )
        )
        assert step is not None
        assert step.result_data["execution_result"]["output"]["deleted"] is True


def test_device_scopes_and_revocation_are_enforced(client, auth_headers):
    user_headers = auth_headers("device-scopes@test.com")
    response = _create_device(
        client, user_headers, scopes=["messages.read"]
    )
    token = response.json()["device_token"]
    device_headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/devices/actions", headers=device_headers).status_code == 403

    received = client.post(
        "/api/devices/inbox",
        headers=device_headers,
        json={
            "external_id": "read-only-1",
            "source_app": "Chat",
            "content": "A message",
        },
    )
    assert received.status_code == 200
    no_draft = client.patch(
        f"/api/devices/inbox/{received.json()['id']}",
        headers=user_headers,
        json={"draft_reply": "A reply"},
    )
    assert no_draft.status_code == 403
    queued = client.post(
        f"/api/devices/inbox/{received.json()['id']}/approve",
        headers=user_headers,
        json={"reply": "A reply", "confirm_send": True},
    )
    assert queued.status_code == 403

    device_id = response.json()["id"]
    assert client.delete(f"/api/devices/{device_id}", headers=user_headers).status_code == 200
    assert client.get("/api/devices/actions", headers=device_headers).status_code == 401


def test_device_connectivity_uses_recent_authenticated_heartbeat(
    client, auth_headers
):
    user_headers = auth_headers("device-heartbeat@test.com")
    response = _create_device(
        client,
        user_headers,
        scopes=["messages.read"],
    )
    assert response.status_code == 200, response.text
    device = response.json()
    device_headers = {"Authorization": f"Bearer {device['device_token']}"}

    listed = client.get("/api/devices", headers=user_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["connectivity"] == "offline"

    heartbeat = client.post(
        "/api/devices/inbox",
        headers=device_headers,
        json={
            "external_id": "heartbeat-message-1",
            "source_app": "Messages",
            "content": "Heartbeat",
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    listed = client.get("/api/devices", headers=user_headers)
    assert listed.json()[0]["connectivity"] == "online"

    with SessionLocal() as db:
        row = db.get(DeviceConnection, device["id"])
        assert row is not None
        row.last_seen_at = utcnow() - timedelta(seconds=31)
        db.commit()

    listed = client.get("/api/devices", headers=user_headers)
    assert listed.json()[0]["connectivity"] == "offline"


def test_message_assist_generates_draft_without_agent_tools(
    client, auth_headers, fake_router
):
    headers = auth_headers("device-assist@test.com")
    fake_router.script.append(AIMessage(content="I will reply tomorrow."))
    paired = _create_device(client, headers, scopes=["messages.read", "messages.draft"])
    token = paired.json()["device_token"]
    received = client.post(
        "/api/devices/inbox",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "external_id": "assist-1",
            "source_app": "Chat",
            "sender": "A contact",
            "content": "Please reply tomorrow.",
        },
    )
    message_id = received.json()["id"]

    response = client.post(
        f"/api/devices/inbox/{message_id}/assist",
        headers=headers,
        json={"mode": "draft"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["reply"] == "I will reply tomorrow."
    assert response.json()["task_id"]
    assert response.json()["run_id"]
    with SessionLocal() as db:
        message = db.get(DeviceMessage, message_id)
        assert message is not None
        assert message.draft_reply == "I will reply tomorrow."
        task = db.get(Task, response.json()["task_id"])
        assert task is not None
        assert task.run_id == response.json()["run_id"]
        step = db.scalar(
            select(TaskStep).where(
                TaskStep.task_id == task.id,
                TaskStep.tool_id == "assist_device_message",
            )
        )
        assert step is not None
        assert step.result_data["execution_result"]["output"]["reply"] == (
            "I will reply tomorrow."
        )


def test_local_app_actions_require_registration_consent_and_confirmation(
    client, auth_headers, monkeypatch, tmp_path
):
    headers = auth_headers("local-app@test.com")
    executable = tmp_path / "trusted-app.exe"
    executable.write_bytes(b"test executable placeholder")
    monkeypatch.setattr(
        devices_api.local_device_control,
        "validate_executable_path",
        lambda value: str(executable) if value == str(executable) else "",
    )
    registration = client.post(
        "/api/devices/local/apps",
        headers=headers,
        json={
            "name": "Trusted App",
            "executable_path": str(executable),
            "consent_confirmed": False,
        },
    )
    assert registration.status_code == 400

    registration = client.post(
        "/api/devices/local/apps",
        headers=headers,
        json={
            "name": "Trusted App",
            "executable_path": str(executable),
            "consent_confirmed": True,
        },
    )
    assert registration.status_code == 200, registration.text
    app_id = registration.json()["id"]

    launch_calls = []
    monkeypatch.setattr(
        devices_api.local_device_control,
        "launch_local_app",
        lambda user_id, selected_app_id, path: (
            launch_calls.append((user_id, selected_app_id, path))
            or {"status": "started", "process_id": 321}
        ),
    )
    not_confirmed = client.post(
        f"/api/devices/local/apps/{app_id}/launch",
        headers=headers,
        json={"confirm": False},
    )
    assert not_confirmed.status_code == 400
    launched = client.post(
        f"/api/devices/local/apps/{app_id}/launch",
        headers=headers,
        json={"confirm": True},
    )
    _approve_direct_action(client, headers, launched)
    assert len(launch_calls) == 1

    close_calls = []
    monkeypatch.setattr(
        devices_api.local_device_control,
        "close_local_app",
        lambda user_id, selected_app_id: (
            close_calls.append((user_id, selected_app_id))
            or {"status": "close_requested", "process_id": 321, "windows_notified": 1}
        ),
    )
    closed = client.post(
        f"/api/devices/local/apps/{app_id}/close",
        headers=headers,
        json={"confirm": True},
    )
    _approve_direct_action(client, headers, closed)
    assert len(close_calls) == 1

    input_calls = []
    monkeypatch.setattr(
        devices_api.local_device_control,
        "interact_with_local_app",
        lambda user_id, selected_app_id, **kwargs: (
            input_calls.append((user_id, selected_app_id, kwargs))
            or (
                {"status": "typed", "characters": len(kwargs["text"])}
                if kwargs["action"] == "type"
                else {"status": "clicked"}
            )
        ),
    )
    unconfirmed_input = client.post(
        f"/api/devices/local/apps/{app_id}/interact",
        headers=headers,
        json={"action": "type", "text": "Draft only", "confirm": False},
    )
    assert unconfirmed_input.status_code == 400
    malformed_click = client.post(
        f"/api/devices/local/apps/{app_id}/interact",
        headers=headers,
        json={"action": "click", "x": 20, "confirm": True},
    )
    assert malformed_click.status_code == 400
    typed = client.post(
        f"/api/devices/local/apps/{app_id}/interact",
        headers=headers,
        json={"action": "type", "text": "Draft only", "confirm": True},
    )
    _approve_direct_action(client, headers, typed)
    assert input_calls[0][2] == {"action": "type", "text": "Draft only"}
    clicked = client.post(
        f"/api/devices/local/apps/{app_id}/interact",
        headers=headers,
        json={"action": "click", "x": 20, "y": 30, "confirm": True},
    )
    _approve_direct_action(client, headers, clicked)
    assert input_calls[1][2] == {"action": "click", "x": 20, "y": 30}


def test_local_app_controls_are_user_scoped(client, auth_headers, monkeypatch, tmp_path):
    owner_headers = auth_headers("local-app-owner@test.com")
    other_headers = auth_headers("local-app-other@test.com")
    executable = tmp_path / "approved.exe"
    executable.write_bytes(b"test executable placeholder")
    monkeypatch.setattr(
        devices_api.local_device_control,
        "validate_executable_path",
        lambda value: str(executable) if value == str(executable) else "",
    )
    created = client.post(
        "/api/devices/local/apps",
        headers=owner_headers,
        json={
            "name": "Owner app",
            "executable_path": str(executable),
            "consent_confirmed": True,
        },
    )
    app_id = created.json()["id"]

    denied = client.post(
        f"/api/devices/local/apps/{app_id}/launch",
        headers=other_headers,
        json={"confirm": True},
    )
    assert denied.status_code == 404


def test_local_inspection_and_accessible_click_require_confirmation(
    client, auth_headers, monkeypatch, tmp_path
):
    headers = auth_headers("local-inspection@test.com")
    executable = tmp_path / "inspectable.exe"
    executable.write_bytes(b"test executable placeholder")
    monkeypatch.setattr(
        devices_api.local_device_control,
        "validate_executable_path",
        lambda value: str(executable) if value == str(executable) else "",
    )
    created = client.post(
        "/api/devices/local/apps",
        headers=headers,
        json={
            "name": "Inspectable App",
            "executable_path": str(executable),
            "consent_confirmed": True,
        },
    )
    assert created.status_code == 200, created.text
    app_id = created.json()["id"]

    inspections = []
    monkeypatch.setattr(
        devices_api.local_device_control,
        "inspect_local_app",
        lambda user_id, selected_app_id, *, mode: (
            inspections.append((user_id, selected_app_id, mode))
            or {"mode": mode, "controls": [], "truncated": False}
        ),
    )
    denied_inspection = client.post(
        f"/api/devices/local/apps/{app_id}/inspect",
        headers=headers,
        json={"mode": "accessibility", "confirm": False},
    )
    assert denied_inspection.status_code == 400
    assert inspections == []
    inspected = client.post(
        f"/api/devices/local/apps/{app_id}/inspect",
        headers=headers,
        json={"mode": "accessibility", "confirm": True},
    )
    _approve_direct_action(client, headers, inspected)
    assert inspections[0][2] == "accessibility"

    clicks = []
    monkeypatch.setattr(
        devices_api.local_device_control,
        "click_accessible_control",
        lambda user_id, selected_app_id, *, name, automation_id: (
            clicks.append((user_id, selected_app_id, name, automation_id))
            or {
                "status": "clicked",
                "verified": True,
                "verification": "accessibility_tree_changed",
            }
        ),
    )
    denied_click = client.post(
        f"/api/devices/local/apps/{app_id}/click-control",
        headers=headers,
        json={"name": "Send", "confirm": False},
    )
    assert denied_click.status_code == 400
    assert clicks == []
    clicked = client.post(
        f"/api/devices/local/apps/{app_id}/click-control",
        headers=headers,
        json={"name": "Send", "automation_id": "send-button", "confirm": True},
    )
    _approve_direct_action(client, headers, clicked)
    assert clicks[0][2:] == ("Send", "send-button")


def test_local_app_process_tracker_only_closes_owned_process(monkeypatch):
    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

        def wait(self, timeout=None):
            raise local_device_control.subprocess.TimeoutExpired("trusted.exe", timeout)

    monkeypatch.setattr(
        local_device_control,
        "validate_executable_path",
        lambda value: value,
    )
    def fake_popen(args, **kwargs):
        assert args == ["C:\\trusted.exe"]
        assert kwargs["shell"] is False
        return FakeProcess()

    monkeypatch.setattr(local_device_control.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        local_device_control,
        "_request_window_close",
        lambda process_id: 1 if process_id == 1234 else 0,
    )
    launched = local_device_control.launch_local_app("owner", "app", "C:\\trusted.exe")
    assert launched["status"] == "started"
    closed = local_device_control.close_local_app("owner", "app")
    assert closed["status"] == "close_requested"

    with pytest.raises(ConflictError, match="only an app process it launched"):
        local_device_control.close_local_app("other-user", "app")
    with local_device_control._process_lock:
        local_device_control._launched_processes.pop(("owner", "app"), None)


def test_local_app_close_is_verified_only_after_process_exit(monkeypatch):
    class FakeProcess:
        pid = 4321

        def poll(self):
            return None

        def wait(self, timeout=None):
            assert timeout == 2
            return 0

    monkeypatch.setattr(
        local_device_control,
        "validate_executable_path",
        lambda value: value,
    )
    monkeypatch.setattr(
        local_device_control.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(local_device_control, "_request_window_close", lambda _pid: 1)

    local_device_control.launch_local_app("owner", "app", "C:\\trusted.exe")
    closed = local_device_control.close_local_app("owner", "app")

    assert closed["status"] == "closed"
    assert closed["process_id"] == 4321
    with local_device_control._process_lock:
        assert ("owner", "app") not in local_device_control._launched_processes


def test_local_input_is_limited_to_foreground_owned_app(monkeypatch):
    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

    class FakeFunction:
        def __init__(self, func):
            self.func = func
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.func(*args)

    class FakeUser32:
        def __init__(self):
            self.cursor = None
            self.ClientToScreen = FakeFunction(self.client_to_screen)
            self.SetCursorPos = FakeFunction(self.set_cursor_pos)

        def client_to_screen(self, _hwnd, point):
            point_value = ctypes.cast(
                point, ctypes.POINTER(ctypes.wintypes.POINT)
            ).contents
            point_value.x += 50
            point_value.y += 60
            return 1

        def set_cursor_pos(self, x, y):
            self.cursor = (x, y)
            return 1

    fake_user32 = FakeUser32()
    sent_batches = []
    monkeypatch.setattr(
        local_device_control,
        "_launched_processes",
        {("owner", "app"): FakeProcess()},
    )
    monkeypatch.setattr(
        local_device_control,
        "_foreground_app_window",
        lambda _pid: (
            fake_user32,
            55,
            type(
                "Rect",
                (),
                {"left": 0, "top": 0, "right": 400, "bottom": 300},
            )(),
        ),
    )
    monkeypatch.setattr(
        local_device_control,
        "_ensure_foreground_window",
        lambda process_id, hwnd: (
            None if (process_id, hwnd) == (1234, 55) else pytest.fail("Wrong app window")
        ),
    )
    monkeypatch.setattr(
        local_device_control,
        "_send_inputs",
        lambda _user32, inputs: sent_batches.append(inputs),
    )

    click = local_device_control.interact_with_local_app(
        "owner", "app", action="click", x=100, y=120
    )
    assert click == {"status": "clicked", "x": 100, "y": 120}
    assert fake_user32.cursor == (150, 180)

    typed = local_device_control.interact_with_local_app(
        "owner", "app", action="type", text="Hello"
    )
    assert typed == {"status": "typed", "characters": 5}
    assert len(sent_batches[-1]) == 10

    key = local_device_control.interact_with_local_app(
        "owner", "app", action="key", key="Enter"
    )
    assert key == {"status": "key_pressed", "key": "ENTER"}
    assert len(sent_batches[-1]) == 2

    with pytest.raises(BadRequestError, match="inside the app client area"):
        local_device_control.interact_with_local_app(
            "owner", "app", action="click", x=400, y=0
        )
    with pytest.raises(BadRequestError, match="Unsupported key"):
        local_device_control.interact_with_local_app(
            "owner", "app", action="key", key="ALT+F4"
        )


def test_local_app_executable_registration_requires_existing_exe(tmp_path):
    executable = tmp_path / "trusted.exe"
    executable.write_bytes(b"test executable placeholder")
    assert local_device_control.validate_executable_path(str(executable)) == str(executable)

    with pytest.raises(BadRequestError, match="absolute path to a Windows .exe"):
        local_device_control.validate_executable_path(str(tmp_path / "notes.txt"))

    with pytest.raises(BadRequestError, match="could not be found"):
        local_device_control.validate_executable_path(str(tmp_path / "missing.exe"))
