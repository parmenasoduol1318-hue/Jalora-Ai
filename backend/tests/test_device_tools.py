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
        device_id, action, app_id, *, platform, app_name, follow_up_request=""
    ):
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

    async def queue_home(device_id, action, app_id, *, platform, app_name):
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


@pytest.mark.parametrize(
    ("last_seen_at", "connectivity"),
    [
        (None, "unknown"),
        (datetime.now(timezone.utc) - timedelta(minutes=3), "offline"),
    ],
)
@pytest.mark.asyncio
async def test_open_local_app_queues_for_one_paired_phone_without_recent_heartbeat(
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
        device_id, action, app_id, *, platform, app_name, follow_up_request=""
    ):
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
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app("WhatsApp")
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_ACTION_PENDING"
    assert "No verified completion receipt arrived" in result.message
    assert "It may already be open" in result.message
    assert "do not report the launch as successful" in result.message
    assert result.data["device_connectivity"] == connectivity
    assert queued == [
        ("phone-1", "android.launcher.open", "", "android", "WhatsApp", "")
    ]


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
    result = await device_tools._await_android_launch_receipt(
        "command-wrong-app",
        "WhatsApp",
    )

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_LAUNCH_UNVERIFIED"
    assert "will not report the app as open" in result.message


@pytest.mark.parametrize(
    "last_seen_at",
    [None, datetime.now(timezone.utc) - timedelta(minutes=3)],
)
@pytest.mark.asyncio
async def test_android_action_queue_does_not_reject_a_stale_heartbeat(
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
    command = SimpleNamespace(
        id="command-reconnect",
        action="android.launcher.open",
        status="pending",
        protocol_status="queued",
        task_id=None,
        run_id=None,
        task_step_id=None,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _device_id):
            return device

        def commit(self):
            pass

        def refresh(self, _command):
            pass

    def queue_command(_db, **kwargs):
        assert kwargs["device"] is device
        assert kwargs["action"] == "android.launcher.open"
        return command, False

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "queue_device_command", queue_command)
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

    assert result.success
    assert result.data["command_id"] == "command-reconnect"
    assert result.data["protocol_status"] == "queued"


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
        device_id, action, app_id, *, platform, app_name, follow_up_request=""
    ):
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
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app("WhatsApp")
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_ACTION_PENDING"
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
        "request_remote_android_url": PermissionLevel.AUTO,
        "assist_device_message": PermissionLevel.AUTO,
        "save_device_reply_draft": PermissionLevel.AUTO,
        "discover_available_device_capabilities": PermissionLevel.AUTO,
        "get_remote_device_action_status": PermissionLevel.AUTO,
        "delete_device_message": PermissionLevel.STRICT,
    }
    for name, permission in expected_permissions.items():
        assert specs[name].permission == permission
    assert all(
        spec.permission == PermissionLevel.CONFIRM
        for name, spec in specs.items()
        if name not in expected_permissions
    )


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
