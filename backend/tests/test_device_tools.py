from datetime import datetime, timezone
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

    async def queue_launch(device_id, action, app_id, *, platform):
        queued.append((device_id, action, app_id, platform))
        return device_tools.ToolResult.ok(
            "Queued Android launch.",
            {"command_id": "command-1", "status": "pending"},
        )

    monkeypatch.setattr(device_tools.sys, "platform", "linux")
    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools, "_request_remote_native_action", queue_launch)
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app("WhatsApp")
    finally:
        set_tool_context(None)

    assert result.success
    assert result.data == {
        "command_id": "command-1",
        "status": "pending",
        "app": "WhatsApp",
        "device_name": "My Android",
    }
    assert queued == [
        ("phone-1", "android.app.launch", "whatsapp-app", "android")
    ]


@pytest.mark.asyncio
async def test_open_local_app_reports_when_android_app_is_not_installed(monkeypatch):
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

    monkeypatch.setattr(device_tools.sys, "platform", "linux")
    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app("WhatsApp")
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "ANDROID_APP_UNAVAILABLE"
    assert "No launchable app named" in result.error.details


def test_local_app_agent_tools_respect_permission_levels():
    specs = {
        spec.name: spec
        for spec in device_tools.registry.all()
        if spec.category == "device_control"
    }

    assert {
        "open_local_app",
        "open_paired_android_app",
        "inspect_local_app",
        "click_local_app_control",
        "type_local_app_text",
        "press_local_app_key",
        "close_local_app",
    } <= specs.keys()
    expected_permissions = {
        "open_youtube": PermissionLevel.AUTO,
        "open_paired_android_app": PermissionLevel.AUTO,
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
