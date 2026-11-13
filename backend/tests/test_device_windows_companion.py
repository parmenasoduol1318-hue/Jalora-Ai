import asyncio
from urllib.parse import parse_qs, urlsplit

from app.context import ToolContext, bind_tool_execution
from app.database.session import SessionLocal
from app.models.device import DeviceRemoteCommand
from app.models.user import User
from app.tools.device_tools import request_remote_windows_action

_WINDOWS_SCOPES = [
    "windows.apps.launch",
    "windows.screen.read",
    "windows.screen.control",
]


def _paired_windows_device(client, owner_headers):
    pair_response = client.post(
        "/api/devices/pair-requests",
        headers=owner_headers,
        json={
            "name": "Windows companion test",
            "platform": "windows",
            "scopes": _WINDOWS_SCOPES,
            "consent_confirmed": True,
        },
    )
    assert pair_response.status_code == 200, pair_response.text
    pair = pair_response.json()
    fragment = parse_qs(urlsplit(pair["pairing_url"]).fragment)
    request_id = fragment["pair_request_id"][0]
    pairing_code = fragment["pair_code"][0]
    target = client.post(
        f"/api/devices/pair-requests/{request_id}/target",
        json={"pairing_code": pairing_code},
    )
    assert target.status_code == 200, target.text
    approved = client.post(
        f"/api/devices/pair-requests/{request_id}/approve",
        json={"confirm": True, "pairing_code": pairing_code},
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def test_windows_manifest_and_remote_action_keep_both_approval_gates(
    client, auth_headers
):
    owner_headers = auth_headers("windows-companion@test.com")
    paired = _paired_windows_device(client, owner_headers)
    device = paired["device"]
    device_headers = {"Authorization": f"Bearer {paired['device_token']}"}

    session = client.post(
        "/api/devices/protocol/v1/session",
        headers=device_headers,
    )
    assert session.status_code == 200, session.text
    access_headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
    manifest = {
        "schema_version": 1,
        "applications": [{
            "id": "test-notepad",
            "name": "Test Notepad",
            "capabilities": [
                "application.launch",
                "application.close",
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
    assert reported.json()["application_count"] == 1
    inventory = client.get("/api/devices/capabilities", headers=owner_headers)
    assert inventory.status_code == 200, inventory.text
    windows_device = next(
        row for row in inventory.json()["devices"] if row["id"] == device["id"]
    )
    assert windows_device["applications"] == [{
        "id": "test-notepad",
        "name": "Test Notepad",
        "capabilities": manifest["applications"][0]["capabilities"],
    }]
    assert next(
        operation for operation in windows_device["companion_capabilities"]
        if operation["operation"] == "application.launch"
    )["status"] == "available"

    duplicate_manifest = {
        "schema_version": 1,
        "applications": [
            {"id": "duplicate", "name": "One", "capabilities": ["application.launch"]},
            {"id": "duplicate", "name": "Two", "capabilities": ["application.launch"]},
        ],
    }
    duplicate = client.put(
        "/api/devices/protocol/v1/manifest",
        headers=access_headers,
        json=duplicate_manifest,
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "INVALID_DEVICE_MANIFEST"

    with SessionLocal() as db:
        user = db.query(User).filter_by(email="windows-companion@test.com").one()
        context = ToolContext(user_id=user.id, idempotency_key="windows-inspect-step")

    with bind_tool_execution(context):
        queued = asyncio.run(request_remote_windows_action(
            device["id"],
            "windows.screen.inspect",
            "test-notepad",
        ))
        unapproved_app = asyncio.run(request_remote_windows_action(
            device["id"],
            "windows.app.launch",
            "not-registered",
        ))

    assert queued.success
    assert queued.data["target_approval_required"] is True
    assert "not confirmed" in queued.message.lower()
    assert not unapproved_app.success

    browser_poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0&client_type=browser",
        headers=access_headers,
    )
    assert browser_poll.status_code == 403
    with SessionLocal() as db:
        stored = db.get(DeviceRemoteCommand, queued.data["command_id"])
        assert stored is not None
        assert stored.protocol_status == "queued"

    windows_poll = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0&client_type=windows",
        headers=access_headers,
    )
    assert windows_poll.status_code == 200, windows_poll.text
    command = windows_poll.json()["commands"][0]
    assert command["operation"] == "windows.screen.inspect"
    assert command["required_scope"] == "windows.screen.read"
    assert command["arguments"] == {"app_id": "test-notepad"}
    assert command["expected_evidence"] == [
        "target_approval",
        "operation_result",
        "accessible_controls",
    ]

    decision = client.post(
        f"/api/devices/protocol/v1/commands/{command['command_id']}/decision",
        headers=access_headers,
        json={"nonce": command["nonce"], "approve": True},
    )
    assert decision.status_code == 200, decision.text
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
                    "app_name": "Test Notepad",
                    "status": "inspected",
                    "control_count": 1,
                },
                "accessible_controls": [{
                    "name": "Editor",
                    "control_type": "Edit",
                    "automation_id": "text",
                    "enabled": True,
                    "visible": True,
                    "value": "Authorized visible text",
                    "value_status": "available",
                }],
            },
        },
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "succeeded"

    history = client.get("/api/devices/remote-command-history", headers=owner_headers)
    assert history.status_code == 200, history.text
    saved = next(row for row in history.json() if row["id"] == command["command_id"])
    assert saved["receipt"]["evidence"]["accessible_controls"][0]["name"] == "Editor"
    assert saved["receipt"]["evidence"]["accessible_controls"][0]["value"] == (
        "Authorized visible text"
    )

    with bind_tool_execution(
        ToolContext(user_id=context.user_id, idempotency_key="windows-ocr-step")
    ):
        ocr_queued = asyncio.run(
            request_remote_windows_action(
                device["id"],
                "windows.screen.ocr",
                "test-notepad",
            )
        )
    assert ocr_queued.success
    ocr_command = client.get(
        "/api/devices/protocol/v1/commands?wait_seconds=0&client_type=windows",
        headers=access_headers,
    ).json()["commands"][0]
    assert ocr_command["operation"] == "windows.screen.ocr"
    assert "screen_ocr_text" in ocr_command["expected_evidence"]
    ocr_decision = client.post(
        f"/api/devices/protocol/v1/commands/{ocr_command['command_id']}/decision",
        headers=access_headers,
        json={"nonce": ocr_command["nonce"], "approve": True},
    )
    assert ocr_decision.status_code == 200
    ocr_receipt = client.post(
        f"/api/devices/protocol/v1/commands/{ocr_command['command_id']}/receipt",
        headers=access_headers,
        json={
            "nonce": ocr_command["nonce"],
            "status": "succeeded",
            "evidence": {
                "operation_result": {"ok": True},
                "accessible_controls": [],
                "screen_ocr_text": "A bounded OCR result.",
            },
        },
    )
    assert ocr_receipt.status_code == 200, ocr_receipt.text
    assert ocr_receipt.json()["status"] == "succeeded"


def test_windows_manifest_cannot_exceed_granted_scopes(client, auth_headers):
    owner_headers = auth_headers("windows-scope-test@test.com")
    pair_response = client.post(
        "/api/devices/pair-requests",
        headers=owner_headers,
        json={
            "name": "Windows launch-only",
            "platform": "windows",
            "scopes": ["windows.apps.launch"],
            "consent_confirmed": True,
        },
    )
    assert pair_response.status_code == 200, pair_response.text
    pair = pair_response.json()
    fragment = parse_qs(urlsplit(pair["pairing_url"]).fragment)
    approved = client.post(
        f"/api/devices/pair-requests/{fragment['pair_request_id'][0]}/approve",
        json={
            "confirm": True,
            "pairing_code": fragment["pair_code"][0],
        },
    )
    assert approved.status_code == 200, approved.text
    session = client.post(
        "/api/devices/protocol/v1/session",
        headers={"Authorization": f"Bearer {approved.json()['device_token']}"},
    )
    assert session.status_code == 200, session.text
    result = client.put(
        "/api/devices/protocol/v1/manifest",
        headers={"Authorization": f"Bearer {session.json()['access_token']}"},
        json={
            "schema_version": 1,
            "applications": [{
                "id": "notepad",
                "name": "Notepad",
                "capabilities": ["application.launch", "screen.control"],
            }],
        },
    )
    assert result.status_code == 403
    assert result.json()["error"]["code"] == "DEVICE_SCOPE_REQUIRED"


def test_windows_scope_dependencies_are_enforced(client, auth_headers):
    response = client.post(
        "/api/devices/pair-requests",
        headers=auth_headers("windows-scope-dependency@test.com"),
        json={
            "name": "Invalid Windows companion",
            "platform": "windows",
            "scopes": ["windows.screen.control"],
            "consent_confirmed": True,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DEVICE_SCOPE_DEPENDENCY"

    wrong_platform = client.post(
        "/api/devices/pair-requests",
        headers=auth_headers("windows-scope-platform@test.com"),
        json={
            "name": "Android with Windows permissions",
            "platform": "android",
            "scopes": ["windows.apps.launch"],
            "consent_confirmed": True,
        },
    )
    assert wrong_platform.status_code == 400
    assert wrong_platform.json()["error"]["code"] == "DEVICE_PLATFORM_SCOPE_MISMATCH"
