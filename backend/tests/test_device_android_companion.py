import asyncio
from urllib.parse import parse_qs, urlsplit

from app.context import ToolContext, bind_tool_execution
from app.database.session import SessionLocal
from app.models.agent import AgentRun
from app.models.task import Task, TaskStep
from app.models.user import User
from app.tools.device_tools import (
    get_remote_device_action_status,
    request_remote_android_action,
)

_ANDROID_SCOPES = [
    "android.apps.launch",
    "android.screen.read",
    "android.screen.control",
]


def _paired_android_device(client, owner_headers, scopes=None):
    pair_response = client.post(
        "/api/devices/pair-requests",
        headers=owner_headers,
        json={
            "name": "Android companion test",
            "platform": "android",
            "scopes": scopes or _ANDROID_SCOPES,
            "consent_confirmed": True,
        },
    )
    assert pair_response.status_code == 200, pair_response.text
    pair = pair_response.json()
    fragment = parse_qs(urlsplit(pair["pairing_url"]).fragment)
    request_id = fragment["pair_request_id"][0]
    pairing_code = fragment["pair_code"][0]
    inspected = client.post(
        f"/api/devices/pair-requests/{request_id}/target",
        json={"pairing_code": pairing_code},
    )
    assert inspected.status_code == 200, inspected.text
    approved = client.post(
        f"/api/devices/pair-requests/{request_id}/approve",
        json={"confirm": True, "pairing_code": pairing_code},
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def test_android_notification_only_pairing_accepts_empty_app_manifest(
    client, auth_headers
):
    owner_headers = auth_headers("android-notifications@test.com")
    paired = _paired_android_device(
        client,
        owner_headers,
        scopes=["messages.read"],
    )
    device = paired["device"]
    session = client.post(
        "/api/devices/protocol/v1/session",
        headers={"Authorization": f"Bearer {paired['device_token']}"},
    )
    assert session.status_code == 200, session.text
    access_headers = {
        "Authorization": f"Bearer {session.json()['access_token']}"
    }

    manifest = client.put(
        "/api/devices/protocol/v1/manifest",
        headers=access_headers,
        json={"schema_version": 1, "applications": []},
    )
    assert manifest.status_code == 200, manifest.text
    polled = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0&client_type=android",
        headers=access_headers,
    )
    assert polled.status_code == 200, polled.text
    assert polled.json()["commands"] == []
    message = client.post(
        "/api/devices/inbox",
        headers={"Authorization": f"Bearer {paired['device_token']}"},
        json={
            "external_id": "notification-only-message",
            "source_app": "Selected app",
            "content": "A notification allowed by messages.read.",
        },
    )
    assert message.status_code == 200, message.text


def test_android_companion_uses_shared_manifest_approval_and_receipt_lifecycle(
    client, auth_headers
):
    owner_headers = auth_headers("android-companion@test.com")
    paired = _paired_android_device(client, owner_headers)
    device = paired["device"]
    session = client.post(
        "/api/devices/protocol/v1/session",
        headers={"Authorization": f"Bearer {paired['device_token']}"},
    )
    assert session.status_code == 200, session.text
    access_headers = {
        "Authorization": f"Bearer {session.json()['access_token']}"
    }

    manifest = {
        "schema_version": 1,
        "applications": [{
            "id": "a1b2c3d4e5f6a7b8c9d0",
            "name": "Test app",
            "capabilities": [
                "application.launch",
                "screen.accessibility.inspect",
                "screen.control",
            ],
        }],
    }
    reported = client.put(
        "/api/devices/protocol/v1/manifest",
        headers=access_headers,
        json=manifest,
    )
    assert reported.status_code == 200, reported.text
    inventory = client.get("/api/devices/capabilities", headers=owner_headers)
    android_device = next(
        row for row in inventory.json()["devices"] if row["id"] == device["id"]
    )
    assert android_device["applications"][0]["id"] == "a1b2c3d4e5f6a7b8c9d0"
    launch_capability = next(
        item for item in android_device["companion_capabilities"]
        if item["operation"] == "application.launch"
    )
    assert launch_capability["status"] == "available"

    with SessionLocal() as db:
        user = db.query(User).filter_by(email="android-companion@test.com").one()
        task = Task(user_id=user.id, goal="Inspect the selected Android app")
        db.add(task)
        db.flush()
        run = AgentRun(user_id=user.id, task_id=task.id)
        db.add(run)
        db.flush()
        task.run_id = run.id
        step = TaskStep(
            task_id=task.id,
            idx=0,
            description="Inspect the selected app",
            tool_id="request_remote_android_action",
        )
        db.add(step)
        db.commit()
        context = ToolContext(
            user_id=user.id,
            task_id=task.id,
            run_id=run.id,
            step_id=step.id,
            idempotency_key="android-inspect-command",
        )
    with bind_tool_execution(context):
        queued = asyncio.run(
            request_remote_android_action(
                device["id"],
                "android.screen.inspect",
                "a1b2c3d4e5f6a7b8c9d0",
            )
        )

    assert queued.success
    assert queued.data["target_approval_required"] is True
    wrong_client = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0&client_type=windows",
        headers=access_headers,
    )
    assert wrong_client.status_code == 403
    poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0&client_type=android",
        headers=access_headers,
    )
    assert poll.status_code == 200, poll.text
    command = poll.json()["commands"][0]
    assert command["device_id"] == device["id"]
    assert command["operation"] == "android.screen.inspect"
    assert command["arguments"] == {"app_id": "a1b2c3d4e5f6a7b8c9d0"}

    decision = client.post(
        f"/api/devices/protocol/v1/commands/{command['command_id']}/decision",
        headers=access_headers,
        json={"nonce": command["nonce"], "approve": True},
    )
    assert decision.status_code == 200
    assert decision.json()["execute"] is True
    receipt = client.post(
        f"/api/devices/protocol/v1/commands/{command['command_id']}/receipt",
        headers=access_headers,
        json={
            "nonce": command["nonce"],
            "status": "succeeded",
            "evidence": {
                "operation_result": {
                    "ok": True,
                    "status": "inspected",
                    "control_count": 1,
                },
                "accessible_controls": [{
                    "name": "Continue",
                    "control_type": "Button",
                    "automation_id": "continue",
                    "enabled": True,
                    "visible": True,
                }],
            },
        },
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "succeeded"
    task_detail = client.get(f"/api/tasks/{context.task_id}", headers=owner_headers)
    assert task_detail.status_code == 200, task_detail.text
    step_receipt = next(
        evidence
        for evidence in task_detail.json()["steps"][0]["evidence"]
        if evidence.get("type") == "remote_device_receipt"
    )
    assert step_receipt["command_id"] == command["command_id"]
    assert step_receipt["status"] == "succeeded"
    assert task_detail.json()["steps"][0]["verification_status"] == "verified"
    with bind_tool_execution(context):
        stored_status = asyncio.run(
            get_remote_device_action_status(command["command_id"])
        )
    assert stored_status.success
    assert stored_status.data["receipt_status"] == "succeeded"


def test_android_companion_rejects_mismatched_scopes_and_unapproved_app(
    client, auth_headers
):
    owner_headers = auth_headers("android-scope-test@test.com")
    mismatched = client.post(
        "/api/devices/pair-requests",
        headers=owner_headers,
        json={
            "name": "Invalid Android permissions",
            "platform": "windows",
            "scopes": ["android.apps.launch"],
            "consent_confirmed": True,
        },
    )
    assert mismatched.status_code == 400
    assert mismatched.json()["error"]["code"] == "DEVICE_PLATFORM_SCOPE_MISMATCH"

    paired = _paired_android_device(
        client,
        owner_headers,
        scopes=["android.apps.launch"],
    )
    device = paired["device"]
    session = client.post(
        "/api/devices/protocol/v1/session",
        headers={"Authorization": f"Bearer {paired['device_token']}"},
    )
    assert session.status_code == 200
    access_headers = {
        "Authorization": f"Bearer {session.json()['access_token']}"
    }
    invalid_manifest = client.put(
        "/api/devices/protocol/v1/manifest",
        headers=access_headers,
        json={
            "schema_version": 1,
            "applications": [{
                "id": "untrusted",
                "name": "Untrusted",
                "capabilities": ["screen.accessibility.inspect"],
            }],
        },
    )
    assert invalid_manifest.status_code == 403
    with SessionLocal() as db:
        user = db.query(User).filter_by(email="android-scope-test@test.com").one()
        context = ToolContext(
            user_id=user.id,
            idempotency_key="android-unapproved-app",
        )
    with bind_tool_execution(context):
        unknown_app = asyncio.run(
            request_remote_android_action(
                device["id"],
                "android.app.launch",
                "not-in-the-local-allowlist",
            )
        )
    assert not unknown_app.success
    assert unknown_app.error.code == "DEVICE_COMMAND_INVALID"
