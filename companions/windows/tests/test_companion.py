import sys
import types
from pathlib import Path

import httpx
import pytest
from jalora_windows_companion import executor as executor_module
from jalora_windows_companion import protocol, security
from jalora_windows_companion.executor import WindowsExecutor
from jalora_windows_companion.protocol import DeviceProtocolClient, parse_pairing_link


def test_pairing_link_requires_valid_codes_and_secure_transport():
    parsed = parse_pairing_link(
        "https://jalora.example/device-companion"
        "#pair_request_id=abc123&pair_code=high_entropy_code"
    )
    assert parsed.api_base == "https://jalora.example"
    assert parsed.request_id == "abc123"
    assert parsed.pairing_code == "high_entropy_code"

    with pytest.raises(ValueError, match="HTTPS"):
        parse_pairing_link(
            "http://jalora.example/device-companion"
            "#pair_request_id=abc123&pair_code=high_entropy_code"
        )
    with pytest.raises(ValueError, match="credentials"):
        parse_pairing_link(
            "https://user:pass@jalora.example/device-companion"
            "#pair_request_id=abc123&pair_code=high_entropy_code"
        )
    with pytest.raises(ValueError, match="one-time"):
        parse_pairing_link("https://jalora.example/device-companion#bad=1")


def test_loopback_pairing_link_can_use_local_development_http():
    parsed = parse_pairing_link(
        "http://localhost:5173/device-companion"
        "#pair_request_id=abc123&pair_code=high_entropy_code"
    )
    assert parsed.api_base == "http://localhost:5173"


def test_windows_manifest_contains_only_granted_capabilities():
    executor = WindowsExecutor.__new__(WindowsExecutor)
    applications = [{"id": "notepad-id", "name": "Notepad"}]

    manifest = executor.manifest(applications, ["windows.apps.launch"])

    assert manifest == {
        "schema_version": 1,
        "applications": [{
            "id": "notepad-id",
            "name": "Notepad",
            "capabilities": ["application.close", "application.launch"],
        }],
    }


def test_installed_application_discovery_is_local_and_exe_only(
    monkeypatch, tmp_path
):
    executable = tmp_path / "Desktop App.exe"
    executable.write_bytes(b"disposable installed application")
    uninstall_key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    roots = ("current_user", "local_machine")
    entries = {
        "current_user": {
            "desktop-app": {
                "DisplayName": "Desktop App",
                "DisplayIcon": f'"{executable}",0',
            },
            "non-executable": {
                "DisplayName": "Packaged App",
                "DisplayIcon": r"C:\Apps\packaged.dll,0",
            },
        },
        "local_machine": {},
    }

    class RegistryKey:
        def __init__(self, path, values=None):
            self.path = path
            self.values = values or {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class FakeRegistry:
        HKEY_CURRENT_USER = roots[0]
        HKEY_LOCAL_MACHINE = roots[1]
        KEY_READ = 1
        KEY_WOW64_32KEY = 2
        KEY_WOW64_64KEY = 4

        @staticmethod
        def OpenKey(root, path, *_args):
            if isinstance(root, RegistryKey):
                if path in entries[root.path]:
                    return RegistryKey(
                        (root.path, path),
                        entries[root.path][path],
                    )
                raise FileNotFoundError(path)
            if path == uninstall_key:
                return RegistryKey(root)
            raise FileNotFoundError(path)

        @staticmethod
        def EnumKey(key, index):
            names = list(entries[key.path])
            if index >= len(names):
                error = OSError("No more data")
                error.winerror = 259
                raise error
            return names[index]

        @staticmethod
        def QueryValueEx(key, value_name):
            if value_name not in key.values:
                raise FileNotFoundError(value_name)
            return key.values[value_name], 1

    monkeypatch.setitem(sys.modules, "winreg", FakeRegistry)

    assert WindowsExecutor.discover_installed_applications() == [{
        "name": "Desktop App",
        "path": str(executable.resolve()),
    }]


def test_browser_action_reports_only_the_local_handoff(monkeypatch):
    monkeypatch.setattr(
        executor_module.webbrowser,
        "open",
        lambda *_args, **_kwargs: True,
    )
    executor = WindowsExecutor.__new__(WindowsExecutor)

    result = executor.execute(
        "browser.open_url",
        {"url": "https://example.com/public"},
        [],
    )

    assert result == (
        "succeeded",
        {
            "browser_open_receipt": True,
            "opened_url_host": "example.com",
        },
        None,
    )


class FakeElement:
    def __init__(
        self,
        name: str,
        control_type: str,
        automation_id: str,
        *,
        enabled: bool = True,
        visible: bool = True,
        password: bool = False,
    ) -> None:
        self.element_info = type("Info", (), {
            "name": name,
            "control_type": control_type,
            "automation_id": automation_id,
            "enabled": enabled,
            "visible": visible,
        })()
        self.clicked = False
        self.password = password
        self.value = ""
        self.element_info.element = type(
            "FakeAutomationElement",
            (),
            {"CurrentIsPassword": property(lambda _self: self.password)},
        )()
        self.iface_value = type(
            "FakeValuePattern",
            (),
            {"CurrentValue": property(lambda _self: self.value)},
        )()

    def is_enabled(self):
        return self.element_info.enabled

    def is_visible(self):
        return self.element_info.visible

    def click_input(self):
        self.clicked = True
        self.element_info.name = "Submitted"

    def set_focus(self):
        return None


class FakeWindow:
    handle = 55

    def __init__(self) -> None:
        self.controls = [
            FakeElement("Editor", "Edit", "text"),
            FakeElement("Submit", "Button", "submit"),
        ]

    def exists(self, timeout=1):
        return True

    def process_id(self):
        return 123

    def descendants(self):
        return self.controls

    def set_focus(self):
        return None


class FakeApplication:
    def __init__(self, window: FakeWindow) -> None:
        self.window = window

    def connect(self, process, timeout):
        assert process == 123

    def top_window(self):
        return self.window


class FakeProcess:
    pid = 123

    def poll(self):
        return None


def _fake_executable(tmp_path):
    executable = tmp_path / "test-app.exe"
    executable.write_bytes(b"disposable test executable")
    digest = WindowsExecutor._digest(executable)
    return [{
        "id": "test-app",
        "name": "Test App",
        "path": str(executable),
        "sha256": digest,
    }]


def _fake_windows(monkeypatch, tmp_path):
    applications = _fake_executable(tmp_path)
    window = FakeWindow()
    monkeypatch.setattr(
        executor_module.subprocess,
        "Popen",
        lambda command, **kwargs: (
            assert_single_exe(command, applications[0]["path"], kwargs)
        ),
    )
    monkeypatch.setattr(
        executor_module,
        "Application",
        lambda backend: FakeApplication(window),
    )
    monkeypatch.setattr(
        executor_module,
        "Desktop",
        lambda backend: type(
            "FakeDesktop",
            (),
            {"window": lambda _self, handle: window},
        )(),
    )
    monkeypatch.setattr(
        WindowsExecutor,
        "_focus",
        staticmethod(lambda _session, _window: None),
    )
    executor = WindowsExecutor.__new__(WindowsExecutor)
    executor._sessions = {}
    return executor, applications, window


def assert_single_exe(command, expected_path, options):
    assert command == [expected_path]
    assert options == {"shell": False, "close_fds": True}
    return FakeProcess()


def test_windows_executor_launches_only_allowlisted_exe_and_inspects_uia(
    monkeypatch, tmp_path
):
    executor, applications, window = _fake_windows(monkeypatch, tmp_path)
    window.controls[0].value = "Visible draft content"
    window.controls[1].element_info.control_type = "Edit"
    window.controls[1].password = True
    window.controls[1].value = "must never be exposed"

    launch = executor.execute(
        "windows.app.launch",
        {"app_id": "test-app"},
        applications,
    )
    inspect = executor.execute(
        "windows.screen.inspect",
        {"app_id": "test-app"},
        applications,
    )

    assert launch[0] == "succeeded"
    assert launch[1]["operation_result"]["status"] == "process_started"
    assert inspect[0] == "succeeded"
    assert inspect[1]["operation_result"]["control_count"] == 2
    assert inspect[1]["accessible_controls"][0]["automation_id"] == "text"
    assert inspect[1]["accessible_controls"][0]["value"] == "Visible draft content"
    assert inspect[1]["accessible_controls"][1]["value_status"] == "redacted_password"
    assert "must never be exposed" not in str(inspect[1])


def test_windows_ocr_is_foreground_scoped_and_returns_text_only(
    monkeypatch, tmp_path
):
    executor, applications, window = _fake_windows(monkeypatch, tmp_path)
    window.capture_as_image = lambda: types.SimpleNamespace(width=800, height=600)
    monkeypatch.setattr(
        WindowsExecutor,
        "_foreground_pid",
        staticmethod(lambda: 123),
    )
    ocr_module = types.ModuleType("pytesseract")
    ocr_module.image_to_string = lambda image, timeout: "Approved test text"
    monkeypatch.setitem(sys.modules, "pytesseract", ocr_module)
    executor.execute("windows.app.launch", {"app_id": "test-app"}, applications)

    receipt = executor.execute(
        "windows.screen.ocr",
        {"app_id": "test-app"},
        applications,
    )

    assert receipt[0] == "succeeded"
    assert receipt[1]["screen_ocr_text"] == "Approved test text"
    assert receipt[1]["accessible_controls"][0]["name"] == "Editor"


def test_windows_ocr_stops_when_approved_app_is_not_foreground(
    monkeypatch, tmp_path
):
    executor, applications, window = _fake_windows(monkeypatch, tmp_path)
    window.capture_as_image = lambda: types.SimpleNamespace(width=800, height=600)
    monkeypatch.setattr(
        WindowsExecutor,
        "_foreground_pid",
        staticmethod(lambda: 456),
    )
    executor.execute("windows.app.launch", {"app_id": "test-app"}, applications)

    receipt = executor.execute(
        "windows.screen.ocr",
        {"app_id": "test-app"},
        applications,
    )

    assert receipt[0] == "failed"
    assert receipt[2] == "WINDOWS_FOREGROUND_CHANGED"


def test_windows_executor_click_requires_and_verifies_exact_accessible_control(
    monkeypatch, tmp_path
):
    executor, applications, _window = _fake_windows(monkeypatch, tmp_path)
    executor.execute("windows.app.launch", {"app_id": "test-app"}, applications)

    clicked = executor.execute(
        "windows.screen.click",
        {"app_id": "test-app", "name": "Submit", "automation_id": ""},
        applications,
    )
    missing = executor.execute(
        "windows.screen.click",
        {"app_id": "test-app", "name": "Missing", "automation_id": ""},
        applications,
    )

    assert clicked[0] == "succeeded"
    assert clicked[1]["operation_result"]["verified"] is True
    assert missing[0] == "failed"
    assert missing[2] == "WINDOWS_CONTROL_NOT_UNIQUE"


def test_windows_executor_scroll_requires_observed_accessibility_change(
    monkeypatch, tmp_path
):
    executor, applications, window = _fake_windows(monkeypatch, tmp_path)
    executor.execute("windows.app.launch", {"app_id": "test-app"}, applications)
    target = window.controls[0]
    scrolled = []

    def scroll(*, wheel_dist):
        scrolled.append(wheel_dist)
        target.element_info.name = "Scrolled Editor"

    target.wheel_mouse_input = scroll

    result = executor.execute(
        "windows.screen.scroll",
        {
            "app_id": "test-app",
            "name": "Editor",
            "automation_id": "text",
            "direction": "down",
        },
        applications,
    )

    assert result[0] == "succeeded"
    assert result[1]["operation_result"]["status"] == "scrolled"
    assert result[1]["operation_result"]["verified"] is True
    assert scrolled == [1]

    target.element_info.name = "Editor"
    target.wheel_mouse_input = lambda **_kwargs: None
    uncertain = executor.execute(
        "windows.screen.scroll",
        {
            "app_id": "test-app",
            "name": "Editor",
            "automation_id": "text",
            "direction": "up",
        },
        applications,
    )

    assert uncertain[0] == "uncertain"
    assert uncertain[2] == "WINDOWS_SCROLL_UNVERIFIED"


def test_windows_executor_types_only_into_allowlisted_edit_control(
    monkeypatch, tmp_path
):
    executor, applications, window = _fake_windows(monkeypatch, tmp_path)
    executor.execute("windows.app.launch", {"app_id": "test-app"}, applications)

    def send_unicode(text):
        window.controls[0].value += text

    monkeypatch.setattr(
        WindowsExecutor,
        "_send_unicode",
        staticmethod(send_unicode),
    )

    result = executor.execute(
        "windows.screen.type",
        {
            "app_id": "test-app",
            "name": "Editor",
            "automation_id": "text",
            "text": "safe test text",
        },
        applications,
    )

    assert result[0] == "succeeded"
    assert result[1]["operation_result"]["verified"] is True
    assert result[1]["operation_result"]["characters"] == len("safe test text")
    assert window.controls[0].value == "safe test text"
    assert "safe test text" not in str(result[1])


def test_windows_executor_never_types_into_password_fields(monkeypatch, tmp_path):
    executor, applications, window = _fake_windows(monkeypatch, tmp_path)
    window.controls[0].password = True
    executor.execute("windows.app.launch", {"app_id": "test-app"}, applications)
    sent = []
    monkeypatch.setattr(
        WindowsExecutor,
        "_send_unicode",
        staticmethod(lambda text: sent.append(text)),
    )

    result = executor.execute(
        "windows.screen.type",
        {
            "app_id": "test-app",
            "name": "Editor",
            "automation_id": "text",
            "text": "never enter secrets",
        },
        applications,
    )

    assert result[0] == "failed"
    assert result[2] == "WINDOWS_PASSWORD_INPUT_BLOCKED"
    assert sent == []


def test_windows_executor_reports_uncertain_when_typed_value_is_not_observed(
    monkeypatch, tmp_path
):
    executor, applications, _window = _fake_windows(monkeypatch, tmp_path)
    executor.execute("windows.app.launch", {"app_id": "test-app"}, applications)
    monkeypatch.setattr(
        WindowsExecutor,
        "_send_unicode",
        staticmethod(lambda _text: None),
    )

    result = executor.execute(
        "windows.screen.type",
        {
            "app_id": "test-app",
            "name": "Editor",
            "automation_id": "text",
            "text": "not entered",
        },
        applications,
    )

    assert result[0] == "uncertain"
    assert result[2] == "WINDOWS_TEXT_INPUT_UNVERIFIED"


def test_windows_executor_refuses_changed_allowlisted_executable(monkeypatch, tmp_path):
    executor, applications, _window = _fake_windows(monkeypatch, tmp_path)
    Path(applications[0]["path"]).write_bytes(b"replaced executable")

    result = executor.execute(
        "windows.app.launch",
        {"app_id": "test-app"},
        applications,
    )

    assert result[0] == "failed"
    assert result[2] == "WINDOWS_APP_CHANGED"


def test_device_protocol_client_uses_bounded_command_client_type():
    client = DeviceProtocolClient("https://jalora.example")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"commands": []})

    client._http.close()
    client._http = httpx.Client(
        base_url="https://jalora.example",
        transport=httpx.MockTransport(handle),
    )
    try:
        client.poll("short-lived-access-token")
    finally:
        client.close()

    assert requests[0].url.path == "/api/devices/protocol/v1/commands"
    assert requests[0].url.params["client_type"] == "windows"
    assert requests[0].headers["Authorization"] == "Bearer short-lived-access-token"


def test_device_credential_is_encrypted_with_current_windows_account(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "data_directory", lambda: tmp_path)
    token = "test-token-that-must-not-be-plaintext"
    credentials = {
        "api_base": "https://jalora.example",
        "device_id": "test-device",
        "device_token": token,
        "scopes": ["windows.apps.launch"],
    }

    security.save_credentials(credentials)

    encrypted = (tmp_path / "credentials.dpapi").read_bytes()
    assert token.encode("utf-8") not in encrypted
    assert security.load_credentials() == credentials


def test_http_protocol_errors_keep_status_code():
    def handle(_request):
        return httpx.Response(401, json={"error": {"details": "revoked"}})

    client = DeviceProtocolClient("https://jalora.example")
    client._http.close()
    client._http = httpx.Client(
        base_url="https://jalora.example",
        transport=httpx.MockTransport(handle),
    )
    try:
        with pytest.raises(protocol.ProtocolHTTPError) as raised:
            client.create_session("revoked-device-token")
    finally:
        client.close()

    assert raised.value.status_code == 401
    assert str(raised.value) == "revoked"
