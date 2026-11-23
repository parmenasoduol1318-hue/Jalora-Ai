from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.context import ToolContext, set_tool_context
from app.tools import device_tools
from app.tools.base import PermissionLevel, registry


def test_device_discovery_is_registered_as_read_only_central_tool():
    spec = registry.get("discover_available_device_capabilities")
    assert spec is not None
    assert spec.permission == PermissionLevel.AUTO
    assert spec.side_effect == "read_only"
    assert "discovery" in spec.tags


@pytest.mark.asyncio
async def test_open_local_app_uses_start_search_without_integrations_entry(monkeypatch):
    launched = []
    monkeypatch.setattr(device_tools.sys, "platform", "win32")
    monkeypatch.setattr(
        device_tools.local_device_control,
        "launch_app_from_start_menu",
        lambda *args: launched.append(args) or {
            "status": "started",
            "process_id": 123,
            "window_process_id": 123,
            "window_handle": 456,
        },
    )
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        expected_app_id = device_tools._local_app_identity("Notepad")[0]
        result = await device_tools.open_local_app(" Notepad ")
    finally:
        set_tool_context(None)

    assert result.success
    assert result.data == {
        "app": "Notepad",
        "status": "started",
        "process_id": 123,
        "window_process_id": 123,
        "window_handle": 456,
    }
    assert launched == [("user-1", expected_app_id, "Notepad")]


@pytest.mark.asyncio
async def test_open_local_app_rejects_control_characters(monkeypatch):
    monkeypatch.setattr(device_tools.sys, "platform", "win32")
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app("Notepad\nEnter")
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "INVALID_APP_NAME"


@pytest.mark.asyncio
async def test_open_local_app_uses_paired_android_when_backend_is_not_windows(monkeypatch):
    device = SimpleNamespace(
        id="phone-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=["android.apps.launch"],
        last_seen_at=datetime.now(timezone.utc),
        capability_manifest={
            "applications": [{
                "id": "whatsapp-app",
                "name": "WhatsApp",
                "capabilities": ["application.launch"],
            }],
        },
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [device])

    queued = []

    async def queue_launch(
        device_id, action, app_id, *, platform, app_name, follow_up_request="",
        await_receipt=True,
    ):
        assert await_receipt is False
        queued.append(
            (device_id, action, app_id, platform, app_name, follow_up_request)
        )
        return device_tools.ToolResult.ok(
            "Queued Android launch.",
            {"command_id": "command-1", "status": "pending"},
        )

    monkeypatch.setattr(device_tools.sys, "platform", "linux")
    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_request_remote_native_action", queue_launch)

    async def verified_receipt(command_id):
        assert command_id == "command-1"
        return device_tools.ToolResult.ok(
            "Returned the persisted device action outcome.",
            {
                "command_id": command_id,
                "device_id": "phone-1",
                "action": "android.launcher.open",
                "protocol_status": "succeeded",
                "receipt_status": "succeeded",
                "evidence": {
                    "operation_result": {
                        "ok": True,
                        "status": "app_opened",
                        "app_name": "WhatsApp",
                        "verified": True,
                    },
                },
            },
        )

    monkeypatch.setattr(
        device_tools,
        "get_remote_device_action_status",
        verified_receipt,
    )
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app(
            "Open my WhatsApp and tell me who sent unread messages"
        )
    finally:
        set_tool_context(None)

    assert result.success
    assert result.data["protocol_status"] == "succeeded"
    assert result.data["app"] == "WhatsApp"
    assert result.data["device_name"] == "My Android"
    assert result.data["device_connectivity"] == "online"
    assert result.data["follow_up_request"] == "tell me who sent unread messages"
    assert queued == [
        (
            "phone-1",
            "android.launcher.open",
            "",
            "android",
            "WhatsApp",
            "tell me who sent unread messages",
        )
    ]


@pytest.mark.asyncio
async def test_close_paired_android_app_only_reports_verified_home_navigation(monkeypatch):
    device = SimpleNamespace(
        id="phone-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=["android.screen.control", "android.screen.read"],
        last_seen_at=datetime.now(timezone.utc),
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [device])

    queued = []

    async def queue_home(
        device_id, action, app_id, *, platform, app_name, await_receipt=True
    ):
        assert await_receipt is False
        queued.append((device_id, action, app_id, platform, app_name))
        return device_tools.ToolResult.ok(
            "Queued Home action.",
            {"command_id": "command-home"},
        )

    async def verified_home(command_id):
        assert command_id == "command-home"
        return device_tools.ToolResult.ok(
            "Returned the persisted device action outcome.",
            {
                "command_id": command_id,
                "protocol_status": "succeeded",
                "receipt_status": "succeeded",
                "evidence": {
                    "operation_result": {
                        "status": "home_opened",
                        "verified": True,
                    },
                },
            },
        )

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_request_remote_native_action", queue_home)
    monkeypatch.setattr(device_tools, "get_remote_device_action_status", verified_home)
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.close_paired_android_app("WhatsApp")
    finally:
        set_tool_context(None)

    assert result.success
    assert "not force-stopped" in result.message
    assert queued == [
        ("phone-1", "android.screen.home", "", "android", "WhatsApp")
    ]


@pytest.mark.asyncio
async def test_android_home_receipt_rejects_unverified_navigation(monkeypatch):
    async def unverified_status(command_id):
        return device_tools.ToolResult.ok(
            "Returned the persisted device action outcome.",
            {
                "command_id": command_id,
                "protocol_status": "succeeded",
                "receipt_status": "succeeded",
                "evidence": {
                    "operation_result": {
                        "status": "home_opened",
                        "verified": False,
                    },
                },
            },
        )

    monkeypatch.setattr(
        device_tools,
        "get_remote_device_action_status",
        unverified_status,
    )
    result = await device_tools._await_android_home_receipt(
        "command-home-unverified",
        "WhatsApp",
    )

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_HOME_UNVERIFIED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("last_seen_at", "connectivity"),
    [
        (None, "unknown"),
        (datetime.now(timezone.utc) - timedelta(minutes=3), "offline"),
    ],
)
async def test_open_local_app_refuses_phone_without_recent_heartbeat(
    monkeypatch, last_seen_at, connectivity
):
    device = SimpleNamespace(
        id="phone-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=["android.apps.launch"],
        last_seen_at=last_seen_at,
        capability_manifest={"applications": []},
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [device])

    queued = []

    async def queue_launch(
        device_id, action, app_id, *, platform, app_name, follow_up_request="",
        await_receipt=True,
    ):
        assert await_receipt is False
        queued.append(
            (device_id, action, app_id, platform, app_name, follow_up_request)
        )
        return device_tools.ToolResult.ok(
            "Queued Android launch.",
            {"command_id": "command-reconnect", "status": "pending"},
        )

    monkeypatch.setattr(device_tools.sys, "platform", "linux")
    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_request_remote_native_action", queue_launch)
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app("WhatsApp")
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_DEVICE_OFFLINE"
    assert result.data["device_connectivity"] == connectivity
    assert queued == []


@pytest.mark.asyncio
async def test_android_launch_rejects_success_receipt_for_a_different_app(monkeypatch):
    async def wrong_app_receipt(_command_id):
        return device_tools.ToolResult.ok(
            "Returned the persisted device action outcome.",
            {
                "protocol_status": "succeeded",
                "receipt_status": "succeeded",
                "evidence": {
                    "operation_result": {
                        "ok": True,
                        "status": "app_opened",
                        "app_name": "Settings",
                        "verified": True,
                    },
                },
            },
        )

    monkeypatch.setattr(
        device_tools,
        "get_remote_device_action_status",
        wrong_app_receipt,
    )
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools._await_android_launch_receipt(
            "command-wrong-app",
            "WhatsApp",
        )
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_LAUNCH_UNVERIFIED"
    assert "will not report the app as open" in result.message


@pytest.mark.parametrize(
    ("protocol_status", "error_code", "reason"),
    [
        (
            "uncertain",
            "ANDROID_ACCESSIBILITY_REQUIRED",
            "Enable Jalora's accessibility service in Android Settings.",
        ),
        (
            "failed",
            "ANDROID_APP_NOT_FOUND",
            "No launchable installed app matched 'WhatsApp'.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_android_launch_preserves_device_failure_code_and_reason(
    monkeypatch, protocol_status, error_code, reason
):
    async def device_failure(_command_id):
        return device_tools.ToolResult.ok(
            "Returned the persisted device action outcome.",
            {
                "protocol_status": protocol_status,
                "receipt_status": protocol_status,
                "error_code": error_code,
                "evidence": {"reason": reason},
            },
        )

    monkeypatch.setattr(
        device_tools,
        "get_remote_device_action_status",
        device_failure,
    )
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools._await_android_launch_receipt(
            "command-device-failure",
            "WhatsApp",
        )
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == error_code
    assert result.error.details == reason


@pytest.mark.parametrize(
    "last_seen_at",
    [None, datetime.now(timezone.utc) - timedelta(minutes=3)],
)
@pytest.mark.asyncio
async def test_android_action_queue_rejects_a_stale_heartbeat(
    monkeypatch, last_seen_at
):
    device = SimpleNamespace(
        id="phone-1",
        user_id="user-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=["android.apps.launch"],
        last_seen_at=last_seen_at,
    )
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _device_id):
            return device

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        device_tools,
        "queue_device_command",
        lambda *_args, **_kwargs: pytest.fail("offline device command was queued"),
    )
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools._request_remote_native_action(
            "phone-1",
            "android.launcher.open",
            "",
            platform="android",
            app_name="WhatsApp",
        )
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_DEVICE_OFFLINE"


@pytest.mark.asyncio
async def test_android_action_waits_for_receipt_and_emits_correlated_progress(
    monkeypatch,
):
    created_at = datetime.now(timezone.utc)
    device = SimpleNamespace(
        id="phone-1",
        user_id="user-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=["android.screen.read", "android.screen.control"],
        last_seen_at=created_at,
    )
    command = SimpleNamespace(
        id="command-screen-inspect",
        action="android.screen.inspect",
        status="pending",
        protocol_status="queued",
        task_id="task-1",
        run_id="run-1",
        task_step_id="step-1",
        created_at=created_at,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _device_id):
            return device

        def commit(self):
            return None

        def refresh(self, _command):
            return None

    def queue_command(_db, **kwargs):
        assert kwargs["device"] is device
        assert kwargs["action"] == "android.screen.inspect"
        return command, False

    events = []
    status_calls = []

    async def completed_status(command_id):
        status_calls.append(command_id)
        return device_tools.ToolResult.ok(
            "The phone reported successful execution.",
            {
                "command_id": command_id,
                "device_id": "phone-1",
                "protocol_status": "succeeded",
                "receipt_status": "succeeded",
                "created_at": created_at,
                "completed_at": datetime.now(timezone.utc),
                "evidence": {"operation_result": {"ok": True, "status": "inspected"}},
            },
        )

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "queue_device_command", queue_command)
    monkeypatch.setattr(device_tools, "get_remote_device_action_status", completed_status)
    monkeypatch.setattr(
        device_tools,
        "emit",
        lambda event_name, payload: events.append((event_name, payload)),
    )
    set_tool_context(ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-1",
    ))
    try:
        result = await device_tools._request_remote_native_action(
            "phone-1",
            "android.screen.inspect",
            "",
            platform="android",
        )
    finally:
        set_tool_context(None)

    assert result.success
    assert result.data["protocol_status"] == "succeeded"
    assert status_calls == ["command-screen-inspect"]
    assert [payload["stage"] for _name, payload in events] == [
        "device_online",
        "command_queued",
        "action_verified",
    ]
    assert all(
        payload["task_id"] == "task-1"
        and payload["run_id"] == "run-1"
        and payload["command_id"] == "command-screen-inspect"
        and payload["device_id"] == "phone-1"
        for _name, payload in events
    )


@pytest.mark.asyncio
async def test_open_local_app_searches_launcher_without_manifest_match(monkeypatch):
    device = SimpleNamespace(
        id="phone-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=["android.apps.launch"],
        last_seen_at=datetime.now(timezone.utc),
        capability_manifest={"applications": []},
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [device])

    queued = []

    async def queue_launch(
        device_id, action, app_id, *, platform, app_name, follow_up_request="",
        await_receipt=True,
    ):
        assert await_receipt is False
        queued.append(
            (device_id, action, app_id, platform, app_name, follow_up_request)
        )
        return device_tools.ToolResult.ok(
            "Queued Android launcher search.",
            {"command_id": "command-2", "status": "pending"},
        )

    monkeypatch.setattr(device_tools.sys, "platform", "linux")
    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_request_remote_native_action", queue_launch)
    monkeypatch.setattr(
        device_tools,
        "ANDROID_LAUNCH_RECEIPT_TIMEOUT_SECONDS",
        0,
    )

    async def pending_status(command_id):
        return device_tools.ToolResult.ok(
            "This device action is still pending.",
            {
                "command_id": command_id,
                "protocol_status": "queued",
                "receipt_status": None,
                "evidence": {},
            },
        )

    monkeypatch.setattr(
        device_tools,
        "get_remote_device_action_status",
        pending_status,
    )
    monkeypatch.setattr(
        device_tools,
        "cancel_unstarted_device_command",
        lambda *_args, **_kwargs: True,
    )
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app("WhatsApp")
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_DEVICE_NOT_ACKNOWLEDGED"
    assert "cancelled it before execution" in result.message
    assert result.data["app"] == "WhatsApp"
    assert queued == [
        ("phone-1", "android.launcher.open", "", "android", "WhatsApp", "")
    ]


def test_local_app_agent_tools_respect_permission_levels():
    specs = {
        spec.name: spec
        for spec in device_tools.registry.all()
        if spec.category == "device_control"
    }

    assert {
        "open_local_app",
        "open_paired_android_app",
        "close_paired_android_app",
        "inspect_local_app",
        "click_local_app_control",
        "type_local_app_text",
        "press_local_app_key",
        "close_local_app",
    } <= specs.keys()
    expected_permissions = {
        "open_youtube": PermissionLevel.AUTO,
        "open_paired_android_app": PermissionLevel.AUTO,
        "close_paired_android_app": PermissionLevel.AUTO,
        "request_remote_android_action": PermissionLevel.AUTO,
        "execute_android_action_plan": PermissionLevel.AUTO,
        "prepare_whatsapp_message_draft": PermissionLevel.AUTO,
        "request_remote_android_url": PermissionLevel.AUTO,
        "assist_device_message": PermissionLevel.AUTO,
        "save_device_reply_draft": PermissionLevel.AUTO,
        "discover_available_device_capabilities": PermissionLevel.AUTO,
        "get_remote_device_action_status": PermissionLevel.AUTO,
        "delete_device_message": PermissionLevel.STRICT,
        "send_whatsapp_message": PermissionLevel.CONFIRM,
    }
    for name, permission in expected_permissions.items():
        assert specs[name].permission == permission
    assert all(
        spec.permission == PermissionLevel.CONFIRM
        for name, spec in specs.items()
        if name not in expected_permissions
    )


def _action_step(action, purpose, precondition, verification, **kwargs):
    return device_tools.AndroidActionStep(
        action=action,
        purpose=purpose,
        precondition=precondition,
        verification=verification,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_android_action_plan_inspects_then_clicks_unique_accessible_control(
    monkeypatch,
):
    calls = []
    evidence = {
        "accessible_controls": [{
            "name": "Search",
            "visible": True,
            "enabled": True,
            "clickable": True,
            "automation_id": "search",
        }],
    }

    async def command(device_id, action, context, *, stage, **kwargs):
        calls.append((action, stage, kwargs))
        return device_tools.ToolResult.ok(
            "Phone action verified.",
            {
                "command_id": f"command-{len(calls)}",
                "action": action,
                "evidence": evidence,
            },
        )

    monkeypatch.setattr(device_tools, "_android_plan_command", command)
    set_tool_context(ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-1",
    ))
    try:
        result = await device_tools.execute_android_action_plan(
            "phone-1",
            [
                _action_step(
                    "inspect_screen",
                    "Inspect the visible app",
                    "online_device",
                    "fresh_screen",
                ),
                _action_step(
                    "tap_control",
                    "Open contact search",
                    "unique_visible_control",
                    "screen_refreshed",
                    target="Search",
                ),
            ],
        )
    finally:
        set_tool_context(None)

    assert result.success
    assert [call[0] for call in calls] == [
        "android.screen.inspect",
        "android.screen.click",
    ]
    assert calls[1][2]["target"]["automation_id"] == "search"
    assert result.data["completed_steps"][1]["status"] == "verified"


@pytest.mark.asyncio
async def test_android_action_plan_refuses_sensitive_control_alias(monkeypatch):
    calls = []
    evidence = {
        "accessible_controls": [{
            "name": "Send",
            "child_labels": ["paper-plane icon"],
            "visible": True,
            "enabled": True,
            "clickable": True,
            "automation_id": "com.whatsapp:id/send",
        }],
    }

    async def command(_device_id, action, _context, **_kwargs):
        calls.append(action)
        return device_tools.ToolResult.ok(
            "Phone action verified.",
            {
                "command_id": "inspect-1",
                "evidence": evidence,
            },
        )

    monkeypatch.setattr(device_tools, "_android_plan_command", command)
    set_tool_context(ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-2",
    ))
    try:
        result = await device_tools.execute_android_action_plan(
            "phone-1",
            [
                _action_step(
                    "inspect_screen",
                    "Inspect the current screen",
                    "online_device",
                    "fresh_screen",
                ),
                _action_step(
                    "tap_control",
                    "Select the icon",
                    "unique_visible_control",
                    "screen_refreshed",
                    target="paper-plane icon",
                ),
            ],
        )
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_SENSITIVE_ACTION_REQUIRES_APPROVAL"
    assert calls == ["android.screen.inspect"]


@pytest.mark.asyncio
async def test_android_action_plan_types_text_only_after_exact_field_verification(
    monkeypatch,
):
    calls = []
    current_value = ""

    async def command(_device_id, action, _context, *, stage, **kwargs):
        nonlocal current_value
        calls.append(action)
        if action == "android.screen.type":
            current_value = kwargs["text"]
            return device_tools.ToolResult.ok(
                "The phone reported successful execution.",
                {"command_id": "type-1"},
            )
        return device_tools.ToolResult.ok(
            "Phone screen inspected.",
            {
                "command_id": f"inspect-{len(calls)}",
                "evidence": {
                    "accessible_controls": [{
                        "name": "Message",
                        "visible": True,
                        "enabled": True,
                        "editable": True,
                        "focused": True,
                        "value_status": "available",
                        "value": current_value,
                        "automation_id": "message",
                    }],
                },
            },
        )

    monkeypatch.setattr(device_tools, "_android_plan_command", command)
    set_tool_context(ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-3",
    ))
    try:
        result = await device_tools.execute_android_action_plan(
            "phone-1",
            [
                _action_step(
                    "inspect_screen",
                    "Inspect the message composer",
                    "online_device",
                    "fresh_screen",
                    include_editable_values=True,
                ),
                _action_step(
                    "enter_text",
                    "Enter the requested draft",
                    "focused_editable_field",
                    "exact_text_value",
                    text="Hi",
                ),
            ],
        )
    finally:
        set_tool_context(None)

    assert result.success
    assert calls == [
        "android.screen.inspect",
        "android.screen.type",
        "android.screen.inspect",
    ]
    assert result.data["completed_steps"][1]["action"] == "enter_text"


def _whatsapp_contact_evidence(
    *,
    search_value="",
    result_contacts=(),
    composer_value="",
    composer_focused=False,
    header="",
):
    controls = []
    if header:
        controls.append({
            "name": header,
            "visible": True,
            "editable": False,
            "automation_id": "com.whatsapp:id/conversation_contact_name",
        })
    if search_value is not None:
        controls.append({
            "name": "Search",
            "visible": True,
            "enabled": True,
            "editable": True,
            "focused": True,
            "value_status": "available",
            "value": search_value,
            "automation_id": "com.whatsapp:id/search",
        })
    controls.extend(result_contacts)
    if header:
        controls.append({
            "name": "Message",
            "visible": True,
            "enabled": True,
            "editable": True,
            "focused": composer_focused,
            "value_status": "available",
            "value": composer_value,
            "automation_id": "com.whatsapp:id/conversation_entry",
        })
    return {
        "operation_result": {"foreground_package": "com.whatsapp"},
        "accessible_controls": controls,
    }


def test_whatsapp_selection_proof_binds_contact_choice_to_current_task(monkeypatch):
    task_context = ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-send",
    )
    contact = {
        "name": "Mom",
        "child_labels": ["Mom", "+1 555 123 4567"],
        "visible": True,
        "enabled": True,
        "clickable": True,
        "automation_id": "com.whatsapp:id/chat_row_1",
    }
    search_command = SimpleNamespace(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        device_id="phone-1",
        action="android.screen.inspect",
        protocol_status="succeeded",
        receipt={
            "status": "succeeded",
            "evidence": _whatsapp_contact_evidence(
                search_value="Mom",
                result_contacts=[contact],
            ),
        },
        arguments={},
    )
    conversation_command = SimpleNamespace(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        device_id="phone-1",
        action="android.screen.click",
        protocol_status="succeeded",
        receipt={
            "status": "succeeded",
            "evidence": _whatsapp_contact_evidence(
                search_value=None,
                header="Mom",
            ),
        },
        arguments={
            "name": "",
            "automation_id": "com.whatsapp:id/chat_row_1",
        },
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, command_id):
            return {
                "search-1": search_command,
                "conversation-1": conversation_command,
            }.get(command_id)

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)

    valid, reason = device_tools._whatsapp_selection_proof(
        "search-1",
        "conversation-1",
        "Mom — +1 555 123 4567",
        "phone-1",
        task_context,
    )

    assert valid
    assert not reason


@pytest.mark.asyncio
async def test_whatsapp_draft_workflow_opens_contact_and_verifies_unsent_text(
    monkeypatch,
):
    device = SimpleNamespace(
        id="phone-1",
        user_id="user-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=[
            "android.apps.launch",
            "android.screen.read",
            "android.screen.control",
        ],
        last_seen_at=datetime.now(timezone.utc),
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _device_id):
            return device

    contact = {
        "name": "Mom",
        "child_labels": ["Mom"],
        "visible": True,
        "enabled": True,
        "clickable": True,
        "automation_id": "com.whatsapp:id/chat_row_1",
    }
    calls = []
    typed_message = ""

    async def plan_command(
        _device_id,
        action,
        _context,
        *,
        stage,
        text="",
        **_kwargs,
    ):
        nonlocal typed_message
        calls.append((action, stage))
        if action == "android.screen.type" and stage.endswith(":type_message"):
            typed_message = text
        if (
            stage.endswith(":inspect_screen")
            or stage.endswith(":inspect_search")
        ):
            evidence = _whatsapp_contact_evidence(
                search_value="",
            )
        elif stage.endswith(":verify_search"):
            evidence = _whatsapp_contact_evidence(
                search_value="Mom",
                result_contacts=[contact],
            )
        elif stage.endswith(":verify_conversation"):
            evidence = _whatsapp_contact_evidence(
                search_value=None,
                composer_value="",
                header="Mom",
            )
        elif stage.endswith(":verify_composer"):
            evidence = _whatsapp_contact_evidence(
                search_value=None,
                composer_value="",
                composer_focused=True,
                header="Mom",
            )
        elif stage.endswith(":verify_draft"):
            evidence = _whatsapp_contact_evidence(
                search_value=None,
                composer_value=typed_message,
                composer_focused=True,
                header="Mom",
            )
        else:
            evidence = {}
        return device_tools.ToolResult.ok(
            "Phone receipt verified.",
            {
                "command_id": f"command-{len(calls)}",
                "completed_at": datetime.now(timezone.utc),
                "evidence": evidence,
            },
        )

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_android_plan_command", plan_command)
    set_tool_context(ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-draft-1",
        idempotency_key="d" * 64,
    ))
    try:
        result = await device_tools.prepare_whatsapp_message_draft(
            "phone-1",
            "Mom",
            "Hi",
        )
    finally:
        set_tool_context(None)

    assert result.success
    assert result.data["recipient"] == "Mom"
    assert result.data["message"] == "Hi"
    assert result.data["search_command_id"]
    assert result.data["conversation_command_id"]
    assert result.data["draft_verified"] is True
    assert result.data["sent"] is False
    assert typed_message == "Hi"
    assert calls[-1][0] == "android.screen.inspect"
    assert any(action == "android.screen.click" for action, _stage in calls)


@pytest.mark.asyncio
async def test_whatsapp_draft_workflow_stops_for_ambiguous_contacts(monkeypatch):
    device = SimpleNamespace(
        id="phone-1",
        user_id="user-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=[
            "android.apps.launch",
            "android.screen.read",
            "android.screen.control",
        ],
        last_seen_at=datetime.now(timezone.utc),
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _device_id):
            return device

    contacts = [
        {
            "name": "Mom",
            "child_labels": ["Mom", "+1 555 123 4567"],
            "visible": True,
            "enabled": True,
            "clickable": True,
            "automation_id": "com.whatsapp:id/chat_row_1",
        },
        {
            "name": "Mom",
            "child_labels": ["Mom", "+1 555 765 4321"],
            "visible": True,
            "enabled": True,
            "clickable": True,
            "automation_id": "com.whatsapp:id/chat_row_2",
        },
    ]

    async def plan_command(_device_id, action, _context, *, stage, **_kwargs):
        evidence = (
            _whatsapp_contact_evidence(search_value="",)
            if stage.endswith(":inspect_screen")
            else _whatsapp_contact_evidence(search_value="")
            if stage.endswith(":inspect_search")
            else _whatsapp_contact_evidence(
                search_value="Mom",
                result_contacts=contacts,
            )
        )
        return device_tools.ToolResult.ok(
            "Phone receipt verified.",
            {
                "command_id": "command",
                "evidence": evidence,
            },
        )

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_android_plan_command", plan_command)
    set_tool_context(ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-draft-ambiguous",
        idempotency_key="e" * 64,
    ))
    try:
        result = await device_tools.prepare_whatsapp_message_draft(
            "phone-1",
            "Mom",
            "Hi",
        )
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "WHATSAPP_CONTACT_AMBIGUOUS"
    assert result.data["clarification"]["input_type"] == "choice"
    assert result.data["candidate_options"] == [
        "Mom — +1 555 123 4567",
        "Mom — +1 555 765 4321",
    ]
def _whatsapp_screen_evidence(draft, messages=(), recipient="Mom"):
    controls = [
        {
            "name": recipient,
            "visible": True,
            "editable": False,
            "enabled": True,
            "automation_id": "com.whatsapp:id/conversation_contact_name",
        },
        {
            "name": "Message",
            "visible": True,
            "editable": True,
            "focused": True,
            "value_status": "available",
            "value": draft,
        },
    ]
    controls.extend(
        {
            "name": item,
            "visible": True,
            "editable": False,
            "enabled": True,
            "automation_id": f"com.whatsapp:id/message_{index}",
        }
        for index, item in enumerate(messages)
    )
    if draft:
        controls.append({
            "name": "Send",
            "visible": True,
            "editable": False,
            "enabled": True,
            "automation_id": "com.whatsapp:id/send",
        })
    return {
        "operation_result": {"foreground_package": "com.whatsapp"},
        "accessible_controls": controls,
    }


@pytest.mark.asyncio
async def test_whatsapp_send_revalidates_exact_draft_and_sends_once(monkeypatch):
    device = SimpleNamespace(
        id="phone-1",
        user_id="user-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=["android.screen.read", "android.screen.control"],
        last_seen_at=datetime.now(timezone.utc),
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _device_id):
            return device

    calls = []

    async def request_action(
        device_id,
        action,
        app_id,
        *,
        platform,
        app_name="",
        name="",
        automation_id="",
        include_editable_values=False,
        tool_context=None,
        **_kwargs,
    ):
        calls.append((action, name, automation_id, include_editable_values, tool_context))
        if action == "android.screen.click":
            return device_tools.ToolResult.ok(
                "The phone reported successful execution of the Android action.",
                {"command_id": "send-command", "completed_at": datetime.now(timezone.utc)},
            )
        inspection_number = sum(
            item[0] == "android.screen.inspect" for item in calls
        )
        evidence = (
            _whatsapp_screen_evidence("", ("Hi",))
            if inspection_number == 2
            else _whatsapp_screen_evidence("Hi")
        )
        return device_tools.ToolResult.ok(
            "The phone verified the screen.",
            {
                "command_id": f"inspect-{inspection_number}",
                "completed_at": datetime.now(timezone.utc),
                "evidence": evidence,
            },
        )

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_request_remote_native_action", request_action)
    context = ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-1",
        idempotency_key="a" * 64,
    )
    set_tool_context(context)
    try:
        result = await device_tools.send_whatsapp_message(
            "phone-1",
            "Mom",
            "Hi",
        )
    finally:
        set_tool_context(None)

    assert result.success
    assert result.data["message_submitted_verified"] is True
    assert result.data["delivery_status"] == "unconfirmed"
    assert [item[0] for item in calls] == [
        "android.screen.inspect",
        "android.screen.click",
        "android.screen.inspect",
    ]
    assert calls[0][3] is True and calls[2][3] is True
    assert calls[1][1:4] == ("Send", "com.whatsapp:id/send", False)
    keys = [item[4].idempotency_key for item in calls]
    assert len(set(keys)) == 3
    assert all(len(key) == 64 for key in keys)
    assert device_tools._whatsapp_stage_context(context, "send").idempotency_key == keys[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("draft", "visible_recipient", "expected_code"),
    [
        ("Different text", "Mom", "WHATSAPP_DRAFT_MISMATCH"),
        ("Hi", "Another Contact", "WHATSAPP_RECIPIENT_UNVERIFIED"),
    ],
)
async def test_whatsapp_send_stops_when_recipient_or_draft_is_unverified(
    monkeypatch, draft, visible_recipient, expected_code
):
    device = SimpleNamespace(
        id="phone-1",
        user_id="user-1",
        name="My Android",
        platform="android",
        status="active",
        scopes=["android.screen.read", "android.screen.control"],
        last_seen_at=datetime.now(timezone.utc),
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _device_id):
            return device

    calls = []

    async def request_action(*_args, **_kwargs):
        calls.append(_args[1])
        return device_tools.ToolResult.ok(
            "The phone verified the screen.",
            {
                "evidence": _whatsapp_screen_evidence(
                    draft,
                    recipient=visible_recipient,
                )
            },
        )

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_request_remote_native_action", request_action)
    set_tool_context(ToolContext(
        user_id="user-1",
        task_id="task-1",
        run_id="run-1",
        tool_call_id="call-2",
    ))
    try:
        result = await device_tools.send_whatsapp_message("phone-1", "Mom", "Hi")
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == expected_code
    assert calls == ["android.screen.inspect"]


@pytest.mark.asyncio
async def test_open_youtube_uses_local_default_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(device_tools.sys, "platform", "win32")
    monkeypatch.setattr(
        device_tools.webbrowser,
        "open",
        lambda url, new: opened.append((url, new)) or True,
    )

    result = await device_tools.open_youtube()

    assert result.success
    assert result.data == {
        "url": "https://www.youtube.com/",
        "status": "opened",
    }
    assert opened == [("https://www.youtube.com/", 2)]


@pytest.mark.asyncio
async def test_open_youtube_reports_browser_failure(monkeypatch):
    monkeypatch.setattr(device_tools.sys, "platform", "win32")
    monkeypatch.setattr(device_tools.webbrowser, "open", lambda *_args, **_kwargs: False)

    result = await device_tools.open_youtube()

    assert not result.success
    assert result.error is not None
    assert result.error.code == "BROWSER_OPEN_FAILED"


@pytest.mark.asyncio
async def test_open_youtube_surfaces_browser_exception(monkeypatch):
    monkeypatch.setattr(device_tools.sys, "platform", "win32")

    def fail_to_open(*_args, **_kwargs):
        raise device_tools.webbrowser.Error("browser unavailable")

    monkeypatch.setattr(device_tools.webbrowser, "open", fail_to_open)

    result = await device_tools.open_youtube()

    assert not result.success
    assert result.error is not None
    assert result.error.code == "BROWSER_OPEN_FAILED"
    assert "browser unavailable" in result.error.details


@pytest.mark.asyncio
async def test_open_youtube_rejects_non_windows_hosts(monkeypatch):
    monkeypatch.setattr(device_tools.sys, "platform", "linux")

    result = await device_tools.open_youtube()

    assert not result.success
    assert result.error is not None
    assert result.error.code == "LOCAL_BROWSER_UNSUPPORTED"
