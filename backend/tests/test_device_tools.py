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
async def test_open_local_app_uses_only_exact_enabled_user_approved_app(monkeypatch):
    app = SimpleNamespace(
        id="app-1",
        name="YouTube",
        executable_path=r"C:\Apps\youtube.exe",
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [app])

    launched = []
    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools.sys, "platform", "win32")
    monkeypatch.setattr(
        device_tools.local_device_control,
        "launch_local_app",
        lambda *args: launched.append(args) or {
            "status": "started",
            "process_id": 123,
        },
    )
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app(" youtube ")
    finally:
        set_tool_context(None)

    assert result.success
    assert result.data == {"app": "YouTube", "status": "started", "process_id": 123}
    assert launched == [("user-1", "app-1", r"C:\Apps\youtube.exe")]


@pytest.mark.asyncio
async def test_open_local_app_rejects_unregistered_app(monkeypatch):
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr(device_tools, "SessionLocal", FakeSession)
    monkeypatch.setattr(device_tools.sys, "platform", "win32")
    set_tool_context(ToolContext(user_id="user-1"))
    try:
        result = await device_tools.open_local_app("YouTube")
    finally:
        set_tool_context(None)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "APP_NOT_APPROVED"


def test_local_app_agent_tools_require_explicit_approval():
    specs = {
        spec.name: spec
        for spec in device_tools.registry.all()
        if spec.category == "device_control"
    }

    assert {
        "open_local_app",
        "inspect_local_app",
        "click_local_app_control",
        "type_local_app_text",
        "press_local_app_key",
        "close_local_app",
    } <= specs.keys()
    expected_permissions = {
        "open_youtube": PermissionLevel.AUTO,
        "assist_device_message": PermissionLevel.AUTO,
        "save_device_reply_draft": PermissionLevel.AUTO,
        "discover_available_device_capabilities": PermissionLevel.AUTO,
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
