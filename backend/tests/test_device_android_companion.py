import asyncio
from urllib.parse import parse_qs, urlsplit

from app.context import ToolContext, bind_tool_execution
from app.database.session import SessionLocal
from app.models.agent import AgentRun
from app.models.conversation import Conversation
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
        headers={"Authorization": "Bearer " + paired["device_token"]},
    )
    assert session.status_code == 200, session.text
    access_headers = {
        "Authorization": "Bearer " + session.json()["access_token"]
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
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

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
        conversation = Conversation(
            user_id=user.id,
            title="Inspect Android app",
        )
        db.add(conversation)
        db.flush()
        task = Task(
            user_id=user.id,
            conversation_id=conversation.id,
            goal="Inspect the selected Android app",
        )
        db.add(task)
        db.flush()
        run = AgentRun(
            user_id=user.id,
            task_id=task.id,
            conversation_id=conversation.id,
        )
        db.add(run)
        db.flush()
        task.run_id = run.id
        step = TaskStep(
            task_id=task.id,
            idx=0,
            plan_step_id="inspect-selected-app",
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
    from app.services.chat_service import serialize_state

    state = {
        "task_id": context.task_id,
        "run_id": context.run_id,
        "user_id": context.user_id,
        "intent": "chat",
        "messages": [
            HumanMessage(content=(
                "Inspect the selected Android app."
            )),
            AIMessage(content="", tool_calls=[{
                "name": "request_remote_android_action",
                "args": {
                    "device_id": device["id"],
                    "action": "android.screen.inspect",
                    "app_id": "a1b2c3d4e5f6a7b8c9d0",
                },
                "id": "android-inspect-call",
                "type": "tool_call",
            }]),
            ToolMessage(
                content=queued.model_dump_json(),
                tool_call_id="android-inspect-call",
            ),
        ],
        "plan": [{
            "id": "inspect-selected-app",
            "description": "Inspect the selected Android app.",
            "tool": "request_remote_android_action",
            "arguments": {
                "device_id": device["id"],
                "action": "android.screen.inspect",
                "app_id": "a1b2c3d4e5f6a7b8c9d0",
            },
            "dependencies": [],
            "status": "active",
        }],
        "status": "partial",
        "recoverable": True,
    }
    with SessionLocal() as db:
        task = db.get(Task, context.task_id)
        run = db.get(AgentRun, context.run_id)
        step = db.get(TaskStep, context.step_id)
        assert task is not None and run is not None and step is not None
        task.status = "partial"
        run.status = "partial"
        run.state_json = serialize_state(state)
        step.outcome_status = "submitted"
        step.status = "active"
        db.commit()
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
    assert receipt.json()["task_resume"]["status"] == "completed"
    task_detail = client.get(f"/api/tasks/{context.task_id}", headers=owner_headers)
    assert task_detail.status_code == 200, task_detail.text
    assert task_detail.json()["status"] == "completed"
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


def test_mocked_multi_app_commands_record_receipts_on_shared_task_steps(
    client, auth_headers
):
    owner_headers = auth_headers("android-multi-app@test.com")
    paired = _paired_android_device(client, owner_headers)
    device = paired["device"]
    session = client.post(
        "/api/devices/protocol/v1/session",
        headers={"Authorization": "Bearer " + paired["device_token"]},
    )
    assert session.status_code == 200, session.text
    access_headers = {
        "Authorization": "Bearer " + session.json()["access_token"]
    }
    app_ids = ["c1d2e3f4a5b6c7d8e9f0", "d1e2f3a4b5c6d7e8f9a0"]
    manifest = client.put(
        "/api/devices/protocol/v1/manifest",
        headers=access_headers,
        json={
            "schema_version": 1,
            "applications": [
                {
                    "id": app_id,
                    "name": f"Approved test app {index}",
                    "capabilities": ["application.launch"],
                }
                for index, app_id in enumerate(app_ids)
            ],
        },
    )
    assert manifest.status_code == 200, manifest.text

    with SessionLocal() as db:
        user = db.query(User).filter_by(email="android-multi-app@test.com").one()
        task = Task(user_id=user.id, goal="Open the two selected test apps")
        db.add(task)
        db.flush()
        run = AgentRun(user_id=user.id, task_id=task.id, status="running")
        db.add(run)
        db.flush()
        task.run_id = run.id
        steps = [
            TaskStep(
                task_id=task.id,
                idx=index,
                plan_step_id=f"launch-app-{index}",
                description=f"Launch approved test app {index}",
                tool_id="request_remote_android_action",
            )
            for index in range(len(app_ids))
        ]
        db.add_all(steps)
        db.commit()
        contexts = [
            ToolContext(
                user_id=user.id,
                task_id=task.id,
                run_id=run.id,
                step_id=step.id,
                idempotency_key=f"multi-app-launch-{index}",
            )
            for index, step in enumerate(steps)
        ]

    queued_commands = []
    for app_id, context in zip(app_ids, contexts):
        with bind_tool_execution(context):
            queued = asyncio.run(
                request_remote_android_action(
                    device["id"],
                    "android.app.launch",
                    app_id,
                )
            )
        assert queued.success
        queued_commands.append(queued.data["command_id"])
    assert len(set(queued_commands)) == 2

    poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0&client_type=android",
        headers=access_headers,
    )
    assert poll.status_code == 200, poll.text
    commands = poll.json()["commands"]
    assert {item["command_id"] for item in commands} == set(queued_commands)
    for command in commands:
        assert command["operation"] == "android.app.launch"
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
                        "status": "launched",
                    },
                },
            },
        )
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["status"] == "succeeded"

    task_detail = client.get(
        f"/api/tasks/{task.id}", headers=owner_headers
    )
    assert task_detail.status_code == 200, task_detail.text
    task_steps = task_detail.json()["steps"]
    assert [step["status"] for step in task_steps] == ["done", "done"]
    assert [step["verification_status"] for step in task_steps] == [
        "verified",
        "verified",
    ]
    assert all(
        any(
            evidence.get("type") == "remote_device_receipt"
            and evidence.get("status") == "succeeded"
            for evidence in step["evidence"]
        )
        for step in task_steps
    )
