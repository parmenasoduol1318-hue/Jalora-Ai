from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import re
import subprocess
import time
import webbrowser
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pywintypes
from pywinauto import Application, Desktop
from pywinauto.findwindows import (
    ElementNotFoundError,
    WindowAmbiguousError,
    WindowNotFoundError,
)
from pywinauto.uia_defines import NoPatternInterfaceError

_AUTOMATION_ERRORS = (
    AttributeError,
    ElementNotFoundError,
    OSError,
    RuntimeError,
    TimeoutError,
    TypeError,
    WindowAmbiguousError,
    WindowNotFoundError,
    NoPatternInterfaceError,
    pywintypes.error,
)

_SUPPORTED_KEYS = {
    "Enter": 0x0D,
    "Tab": 0x09,
    "Esc": 0x1B,
    "Backspace": 0x08,
    "Space": 0x20,
    "Up": 0x26,
    "Down": 0x28,
    "Left": 0x25,
    "Right": 0x27,
}
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_logger = logging.getLogger(__name__)


@dataclass
class AppSession:
    process: subprocess.Popen
    hwnd: int | None
    app: Application


class WindowsActionError(RuntimeError):
    def __init__(self, code: str, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


class WindowsExecutor:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("The Jalora Windows companion runs only on Windows.")
        self._sessions: dict[str, AppSession] = {}

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _executable_from_display_icon(display_icon: str) -> Path | None:
        value = display_icon.strip()
        if value.startswith('"'):
            match = re.fullmatch(
                r'"([^"]+\.exe)"(?:\s*,\s*-?\d+)?',
                value,
                re.IGNORECASE,
            )
        else:
            match = re.fullmatch(
                r"(.+?\.exe)(?:\s*,\s*-?\d+)?",
                value,
                re.IGNORECASE,
            )
        if match is None:
            return None
        path = Path(os.path.expandvars(match.group(1).strip())).expanduser()
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None
        return resolved if resolved.is_file() and resolved.suffix.casefold() == ".exe" else None

    @classmethod
    def discover_installed_applications(cls) -> list[dict[str, str]]:
        if os.name != "nt":
            raise RuntimeError("Installed-app discovery is available only on Windows.")

        import winreg

        uninstall_key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        registry_views = (
            0,
            getattr(winreg, "KEY_WOW64_32KEY", 0),
            getattr(winreg, "KEY_WOW64_64KEY", 0),
        )
        discovered: dict[str, dict[str, str]] = {}
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in registry_views:
                try:
                    root_key = winreg.OpenKey(
                        root,
                        uninstall_key,
                        0,
                        winreg.KEY_READ | view,
                    )
                except FileNotFoundError:
                    continue
                except OSError:
                    _logger.warning(
                        "Could not read the Windows installed-app registry view %s",
                        view,
                        exc_info=True,
                    )
                    continue
                with root_key:
                    index = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(root_key, index)
                        except OSError as exc:
                            if getattr(exc, "winerror", None) != 259:
                                _logger.warning(
                                    "Could not enumerate Windows installed apps in registry view %s",
                                    view,
                                    exc_info=True,
                                )
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(root_key, subkey_name) as app_key:
                                name = str(
                                    winreg.QueryValueEx(app_key, "DisplayName")[0]
                                ).strip()
                                display_icon = str(
                                    winreg.QueryValueEx(app_key, "DisplayIcon")[0]
                                )
                        except FileNotFoundError:
                            continue
                        except OSError:
                            _logger.warning(
                                "Could not inspect installed-app registry entry %s",
                                subkey_name,
                                exc_info=True,
                            )
                            continue
                        executable = cls._executable_from_display_icon(display_icon)
                        if (
                            executable is None
                            or not name
                            or len(name) > 120
                        ):
                            continue
                        key = str(executable).casefold()
                        discovered.setdefault(key, {
                            "name": name,
                            "path": str(executable),
                        })
        return sorted(
            discovered.values(),
            key=lambda item: (item["name"].casefold(), item["path"].casefold()),
        )

    def _approved_entry(self, app_id: str, applications: list[dict]) -> dict:
        entry = next(
            (item for item in applications if item.get("id") == app_id),
            None,
        )
        if entry is None:
            raise WindowsActionError(
                "WINDOWS_APP_NOT_ALLOWLISTED",
                "This app is not in the local companion allowlist.",
            )
        try:
            path = Path(entry["path"]).resolve(strict=True)
        except OSError as exc:
            raise WindowsActionError(
                "WINDOWS_APP_PATH_INVALID",
                "The approved executable is no longer available.",
            ) from exc
        if path.suffix.casefold() != ".exe" or not path.is_file():
            raise WindowsActionError(
                "WINDOWS_APP_PATH_INVALID",
                "The approved executable is no longer available.",
            )
        if self._digest(path) != entry["sha256"]:
            raise WindowsActionError(
                "WINDOWS_APP_CHANGED",
                "The executable changed after it was approved. Remove and add it again.",
            )
        return {**entry, "resolved_path": path}

    def manifest(self, applications: list[dict], scopes: list[str]) -> dict:
        capabilities_by_scope = {
            "windows.apps.launch": ["application.launch", "application.close"],
            "windows.screen.read": ["screen.accessibility.inspect"],
            "windows.screen.control": ["screen.control"],
        }
        granted_capabilities = {
            capability
            for scope in scopes
            for capability in capabilities_by_scope.get(scope, [])
        }
        return {
            "schema_version": 1,
            "applications": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "capabilities": sorted(granted_capabilities),
                }
                for item in applications
                if granted_capabilities
            ],
        }

    def execute(
        self,
        operation: str,
        arguments: dict[str, Any],
        applications: list[dict],
    ) -> tuple[str, dict, str | None]:
        if operation == "browser.open_url":
            url = arguments.get("url")
            parsed = urlsplit(url) if isinstance(url, str) else None
            if (
                parsed is None
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                return "failed", {
                    "browser_open_receipt": False,
                    "reason": "The received link is not a valid external HTTP(S) URL.",
                }, "INVALID_REMOTE_URL"
            try:
                opened = webbrowser.open(url, new=2)
            except (OSError, webbrowser.Error) as exc:
                return "failed", {
                    "browser_open_receipt": False,
                    "reason": f"Windows could not hand the link to the browser: {exc}"[:500],
                }, "BROWSER_OPEN_FAILED"
            if not opened:
                return "failed", {
                    "browser_open_receipt": False,
                    "reason": "Windows did not accept the browser handoff.",
                }, "BROWSER_OPEN_FAILED"
            return "succeeded", {
                "browser_open_receipt": True,
                "opened_url_host": parsed.hostname,
            }, None
        try:
            result = self._execute(operation, arguments, applications)
            evidence = {"operation_result": {"ok": True, **result}}
            if operation in {"windows.screen.inspect", "windows.screen.ocr"}:
                evidence["accessible_controls"] = result.pop("accessible_controls")
                if operation == "windows.screen.ocr":
                    evidence["screen_ocr_text"] = result.pop("screen_ocr_text")
                evidence["operation_result"] = {
                    "ok": True,
                    **{key: value for key, value in result.items() if key != "accessible_controls"},
                }
            return "succeeded", evidence, None
        except WindowsActionError as exc:
            status = "uncertain" if exc.uncertain else "failed"
            evidence = (
                {"reason": str(exc)}
                if exc.uncertain
                else {"operation_result": {"ok": False, "reason": str(exc)}}
            )
            return status, evidence, exc.code

    def _execute(
        self,
        operation: str,
        arguments: dict[str, Any],
        applications: list[dict],
    ) -> dict:
        app_id = arguments.get("app_id", "")
        entry = self._approved_entry(app_id, applications)
        if operation == "windows.app.launch":
            process = subprocess.Popen(
                [str(entry["resolved_path"])],
                shell=False,
                close_fds=True,
            )
            application = Application(backend="uia")
            try:
                application.connect(process=process.pid, timeout=10)
            except _AUTOMATION_ERRORS:
                self._sessions[app_id] = AppSession(process, None, application)
                return {
                    "app_name": entry["name"],
                    "status": "process_started_window_unavailable",
                    "verified": process.poll() is None,
                }
            session = AppSession(process, None, application)
            try:
                window = application.top_window()
                if window.exists(timeout=5):
                    session.hwnd = int(window.handle)
            except _AUTOMATION_ERRORS:
                session.hwnd = None
            self._sessions[app_id] = session
            return {
                "app_name": entry["name"],
                "status": "process_started",
                "verified": process.poll() is None,
            }
        session = self._session(app_id)
        if operation == "windows.app.close":
            return self._close(entry, session)
        window = self._window(session)
        if operation in {"windows.screen.inspect", "windows.screen.ocr"}:
            if operation == "windows.screen.ocr" and self._foreground_pid() != session.process.pid:
                raise WindowsActionError(
                    "WINDOWS_FOREGROUND_CHANGED",
                    "OCR is limited to the approved app while it is the foreground window.",
                )
            controls = []
            try:
                descendants = window.descendants()[:300]
                for control in descendants[:40]:
                    info = control.element_info
                    control_data = {
                        "name": str(info.name or "")[:100],
                        "control_type": str(info.control_type or "")[:80],
                        "automation_id": str(info.automation_id or "")[:100],
                        "enabled": bool(info.enabled),
                        "visible": bool(info.visible),
                    }
                    if control_data["control_type"] in {
                        "Edit",
                        "ComboBox",
                        "Document",
                    }:
                        try:
                            if control.element_info.element.CurrentIsPassword:
                                control_data["value_status"] = "redacted_password"
                            else:
                                value = control.iface_value.CurrentValue
                                if isinstance(value, str):
                                    control_data["value"] = value[:500]
                                    control_data["value_status"] = "available"
                                else:
                                    control_data["value_status"] = "unavailable"
                        except _AUTOMATION_ERRORS:
                            control_data["value_status"] = "unavailable"
                    controls.append(control_data)
            except _AUTOMATION_ERRORS as exc:
                raise WindowsActionError(
                    "WINDOWS_ACCESSIBILITY_FAILED",
                    f"Windows could not read this app's accessible controls: {exc}",
                ) from exc
            result = {
                "app_name": entry["name"],
                "status": "inspected",
                "control_count": len(descendants),
                "truncated": len(descendants) > len(controls),
                "accessible_controls": controls,
            }
            if operation == "windows.screen.ocr":
                try:
                    import pytesseract

                    image = window.capture_as_image()
                    if image.width > 4096 or image.height > 4096 or image.width * image.height > 16_000_000:
                        raise WindowsActionError(
                            "WINDOWS_SCREEN_TOO_LARGE",
                            "The selected app window exceeds the OCR image size limit.",
                        )
                    result["screen_ocr_text"] = pytesseract.image_to_string(
                        image,
                        timeout=10,
                    )[:10_000]
                    result["ocr_verified"] = True
                except WindowsActionError:
                    raise
                except (ImportError, OSError, RuntimeError, TimeoutError) as exc:
                    raise WindowsActionError(
                        "WINDOWS_OCR_UNAVAILABLE",
                        "Screen OCR requires Pillow, pytesseract, and the Tesseract executable.",
                    ) from exc
            return result
        if operation == "windows.screen.click":
            control = self._find_control(window, arguments)
            self._focus(session, window)
            before = self._tree_signature(window)
            try:
                control.click_input()
                time.sleep(0.25)
                after = self._tree_signature(window)
            except _AUTOMATION_ERRORS as exc:
                raise WindowsActionError(
                    "WINDOWS_CLICK_FAILED",
                    f"Windows could not click the selected accessible control: {exc}",
                    uncertain=True,
                ) from exc
            if before == after:
                raise WindowsActionError(
                    "WINDOWS_CLICK_UNVERIFIED",
                    "The control was clicked, but no accessibility change was observed.",
                    uncertain=True,
                )
            return {
                "app_name": entry["name"],
                "status": "clicked",
                "verified": True,
            }
        if operation == "windows.screen.scroll":
            control = self._find_control(window, arguments)
            direction = arguments.get("direction")
            if not isinstance(direction, str) or direction not in {"up", "down"}:
                raise WindowsActionError(
                    "WINDOWS_SCROLL_DIRECTION_INVALID",
                    "The scroll direction must be up or down.",
                )
            self._focus(session, window)
            before = self._tree_signature(window)
            try:
                control.wheel_mouse_input(
                    wheel_dist=1 if direction == "down" else -1
                )
                time.sleep(0.25)
                after = self._tree_signature(window)
            except _AUTOMATION_ERRORS as exc:
                raise WindowsActionError(
                    "WINDOWS_SCROLL_FAILED",
                    f"Windows could not scroll the selected accessible control: {exc}",
                    uncertain=True,
                ) from exc
            if before == after:
                raise WindowsActionError(
                    "WINDOWS_SCROLL_UNVERIFIED",
                    "The scroll was attempted, but no accessibility change was observed.",
                    uncertain=True,
                )
            return {
                "app_name": entry["name"],
                "status": "scrolled",
                "direction": direction,
                "verified": True,
            }
        if operation == "windows.screen.type":
            control = self._find_control(window, arguments)
            if str(control.element_info.control_type) not in {"Edit", "ComboBox", "Document"}:
                raise WindowsActionError(
                    "WINDOWS_TEXT_TARGET_UNSUPPORTED",
                    "Text can only be entered into an accessible edit field.",
                )
            try:
                if control.element_info.element.CurrentIsPassword:
                    raise WindowsActionError(
                        "WINDOWS_PASSWORD_INPUT_BLOCKED",
                        "The companion does not enter text into password fields.",
                    )
                before = control.iface_value.CurrentValue
            except WindowsActionError:
                raise
            except _AUTOMATION_ERRORS as exc:
                raise WindowsActionError(
                    "WINDOWS_TEXT_VERIFICATION_UNAVAILABLE",
                    "The accessible edit field does not expose a value for safe verification.",
                ) from exc
            self._focus(session, window)
            try:
                control.set_focus()
                self._send_unicode(str(arguments["text"]))
                time.sleep(0.1)
                after = control.iface_value.CurrentValue
            except _AUTOMATION_ERRORS as exc:
                raise WindowsActionError(
                    "WINDOWS_TEXT_INPUT_UNVERIFIED",
                    f"Windows did not confirm text input: {exc}",
                    uncertain=True,
                ) from exc
            requested_text = str(arguments["text"])
            if (
                not isinstance(after, str)
                or not requested_text
                or after == before
                or requested_text not in after
            ):
                raise WindowsActionError(
                    "WINDOWS_TEXT_INPUT_UNVERIFIED",
                    "Text entry was attempted, but the accessible field did not confirm the requested text.",
                    uncertain=True,
                )
            return {
                "app_name": entry["name"],
                "status": "input_sent",
                "verified": True,
                "characters": len(requested_text),
            }
        if operation == "windows.screen.key":
            key = arguments.get("key")
            virtual_key = _SUPPORTED_KEYS.get(key)
            if virtual_key is None:
                raise WindowsActionError(
                    "WINDOWS_KEY_UNSUPPORTED", "This keyboard key is not supported."
                )
            self._focus(session, window)
            self._send_key(virtual_key)
            return {
                "app_name": entry["name"],
                "status": "key_sent",
                "verified": False,
                "key": key,
            }
        raise WindowsActionError(
            "WINDOWS_OPERATION_UNSUPPORTED",
            "This Windows companion operation is not supported.",
        )

    def _session(self, app_id: str) -> AppSession:
        session = self._sessions.get(app_id)
        if session is None or session.process.poll() is not None:
            raise WindowsActionError(
                "WINDOWS_APP_NOT_RUNNING",
                "Launch this allowlisted app through Jalora before operating it.",
            )
        return session

    @staticmethod
    def _window(session: AppSession):
        try:
            window = (
                Desktop(backend="uia").window(handle=session.hwnd)
                if session.hwnd
                else session.app.top_window()
            )
            if not window.exists(timeout=1):
                raise RuntimeError("The approved app window is no longer available.")
            if window.process_id() != session.process.pid:
                raise RuntimeError("The window no longer belongs to the approved app.")
            session.hwnd = int(window.handle)
            return window
        except _AUTOMATION_ERRORS as exc:
            raise WindowsActionError(
                "WINDOWS_WINDOW_NOT_AVAILABLE",
                f"The approved app window could not be verified: {exc}",
            ) from exc

    @staticmethod
    def _find_control(window, arguments: dict):
        name = arguments.get("name", "")
        automation_id = arguments.get("automation_id", "")
        try:
            matches = [
                control
                for control in window.descendants()[:300]
                if (not name or control.element_info.name == name)
                and (
                    not automation_id
                    or control.element_info.automation_id == automation_id
                )
            ]
            if len(matches) != 1:
                raise WindowsActionError(
                    "WINDOWS_CONTROL_NOT_UNIQUE",
                    f"The target must match one accessible control; found {len(matches)}.",
                )
            control = matches[0]
            if not control.is_enabled() or not control.is_visible():
                raise WindowsActionError(
                    "WINDOWS_CONTROL_UNAVAILABLE",
                    "The selected accessible control is disabled or hidden.",
                )
            return control
        except WindowsActionError:
            raise
        except _AUTOMATION_ERRORS as exc:
            raise WindowsActionError(
                "WINDOWS_ACCESSIBILITY_FAILED",
                f"Windows could not locate the selected accessible control: {exc}",
            ) from exc

    @staticmethod
    def _tree_signature(window) -> tuple:
        return tuple(
            (item.element_info.control_type, item.element_info.name)
            for item in window.descendants()[:300]
        )

    @staticmethod
    def _focus(session: AppSession, window) -> None:
        try:
            window.set_focus()
            foreground = ctypes.windll.user32.GetForegroundWindow()
            process_id = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(
                foreground,
                ctypes.byref(process_id),
            )
            if process_id.value != session.process.pid:
                raise RuntimeError("Another application took focus.")
        except _AUTOMATION_ERRORS as exc:
            raise WindowsActionError(
                "WINDOWS_FOREGROUND_CHANGED",
                f"The approved app could not be made the foreground window: {exc}",
            ) from exc

    @staticmethod
    def _foreground_pid() -> int:
        foreground = ctypes.windll.user32.GetForegroundWindow()
        process_id = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(
            foreground,
            ctypes.byref(process_id),
        )
        return int(process_id.value)

    @staticmethod
    def _send_unicode(text: str) -> None:
        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeyboardInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("union",)
            _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]

        encoded = text.encode("utf-16-le")
        inputs = []
        for offset in range(0, len(encoded), 2):
            code_unit = int.from_bytes(encoded[offset:offset + 2], "little")
            inputs.extend((
                Input(
                    type=1,
                    ki=KeyboardInput(
                        0, code_unit, _KEYEVENTF_UNICODE, 0, 0
                    ),
                ),
                Input(
                    type=1,
                    ki=KeyboardInput(
                        0,
                        code_unit,
                        _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP,
                        0,
                        0,
                    ),
                ),
            ))
        native_inputs = (Input * len(inputs))(*inputs)
        send_input = ctypes.windll.user32.SendInput
        send_input.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        send_input.restype = wintypes.UINT
        if send_input(len(inputs), native_inputs, ctypes.sizeof(Input)) != len(inputs):
            raise OSError(ctypes.get_last_error(), "Windows rejected Unicode text input.")

    @staticmethod
    def _send_key(virtual_key: int) -> None:
        class KeyboardInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class InputUnion(ctypes.Union):
            _fields_ = [("ki", KeyboardInput)]

        class Input(ctypes.Structure):
            _anonymous_ = ("union",)
            _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]

        native_inputs = (Input * 2)(
            Input(type=1, ki=KeyboardInput(virtual_key, 0, 0, 0, 0)),
            Input(
                type=1,
                ki=KeyboardInput(
                    virtual_key,
                    0,
                    _KEYEVENTF_KEYUP,
                    0,
                    0,
                ),
            ),
        )
        send_input = ctypes.windll.user32.SendInput
        send_input.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        send_input.restype = wintypes.UINT
        if send_input(2, native_inputs, ctypes.sizeof(Input)) != 2:
            raise OSError(ctypes.get_last_error(), "Windows rejected keyboard input.")

    @staticmethod
    def _close(entry: dict, session: AppSession) -> dict:
        try:
            window = WindowsExecutor._window(session)
            window.close()
            try:
                session.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise WindowsActionError(
                    "WINDOWS_CLOSE_UNCONFIRMED",
                    "A graceful close was requested, but the process is still running.",
                    uncertain=True,
                )
            return {
                "app_name": entry["name"],
                "status": "closed",
                "verified": session.process.poll() is not None,
            }
        except WindowsActionError:
            raise
        except _AUTOMATION_ERRORS as exc:
            raise WindowsActionError(
                "WINDOWS_CLOSE_FAILED",
                f"Windows could not request a graceful app close: {exc}",
                uncertain=True,
            ) from exc
