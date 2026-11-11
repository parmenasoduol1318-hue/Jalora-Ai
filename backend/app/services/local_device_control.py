from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from threading import RLock

from app.errors import BadRequestError, ConflictError, ServiceUnavailableError

_launched_processes: dict[tuple[str, str], subprocess.Popen] = {}
_process_lock = RLock()
_WM_CLOSE = 0x0010
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_KEYS = {
    "ENTER": 0x0D,
    "TAB": 0x09,
    "ESC": 0x1B,
    "BACKSPACE": 0x08,
    "SPACE": 0x20,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
}


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeyboardInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


def validate_executable_path(value: str) -> str:
    if sys.platform != "win32":
        raise ServiceUnavailableError(
            "Local app control currently supports Windows only.",
            "LOCAL_APP_CONTROL_UNSUPPORTED",
        )
    path = Path(value).expanduser()
    if not path.is_absolute() or path.suffix.casefold() != ".exe":
        raise BadRequestError(
            "Choose an absolute path to a Windows .exe file.",
            "INVALID_APP_PATH",
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BadRequestError(
            "The selected executable could not be found.",
            "APP_NOT_FOUND",
        ) from exc
    if not resolved.is_file():
        raise BadRequestError("The selected path is not a file.", "INVALID_APP_PATH")
    return str(resolved)


def is_running(user_id: str, app_id: str) -> bool:
    with _process_lock:
        process = _launched_processes.get((user_id, app_id))
        return process is not None and process.poll() is None


def launch_local_app(user_id: str, app_id: str, executable_path: str) -> dict[str, int | str]:
    executable_path = validate_executable_path(executable_path)
    key = (user_id, app_id)
    with _process_lock:
        process = _launched_processes.get(key)
        if process is not None and process.poll() is None:
            raise ConflictError("Jalora already launched this app and it is running.")
        try:
            process = subprocess.Popen(
                [executable_path],
                shell=False,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ServiceUnavailableError(
                f"Windows could not start the approved app: {exc}",
                "APP_LAUNCH_FAILED",
            ) from exc
        _launched_processes[key] = process
        return {"status": "started", "process_id": process.pid}


def _request_window_close(process_id: int) -> int:
    if os.name != "nt":
        raise ServiceUnavailableError(
            "Graceful app closing is available only on Windows.",
            "LOCAL_APP_CONTROL_UNSUPPORTED",
        )
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    close_windows = 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit_window(hwnd: int, parameter: int) -> bool:
        nonlocal close_windows
        del parameter
        window_process_id = wintypes.DWORD()
        get_process_id(hwnd, ctypes.byref(window_process_id))
        if window_process_id.value != process_id or not is_visible(hwnd):
            return True
        if post_message(hwnd, _WM_CLOSE, 0, 0):
            close_windows += 1
        return True

    enum_windows = user32.EnumWindows
    enum_windows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    enum_windows.restype = wintypes.BOOL
    get_process_id = user32.GetWindowThreadProcessId
    get_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    get_process_id.restype = wintypes.DWORD
    post_message = user32.PostMessageW
    post_message.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    post_message.restype = wintypes.BOOL
    is_visible = user32.IsWindowVisible
    is_visible.argtypes = [wintypes.HWND]
    is_visible.restype = wintypes.BOOL

    if not enum_windows(visit_window, 0):
        error = ctypes.get_last_error()
        if error:
            raise OSError(error, "Could not enumerate application windows.")
    return close_windows


def _foreground_app_window(process_id: int):
    if os.name != "nt":
        raise ServiceUnavailableError(
            "Desktop input is available only on Windows.",
            "LOCAL_APP_CONTROL_UNSUPPORTED",
        )
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_foreground = user32.GetForegroundWindow
    get_foreground.restype = wintypes.HWND
    get_window_process_id = user32.GetWindowThreadProcessId
    get_window_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    get_window_process_id.restype = wintypes.DWORD
    hwnd = get_foreground()
    if not hwnd:
        raise ConflictError("Focus the Jalora-launched app before operating it.")
    window_process_id = wintypes.DWORD()
    get_window_process_id(hwnd, ctypes.byref(window_process_id))
    if window_process_id.value != process_id:
        raise ConflictError(
            "The foreground window is not the selected Jalora-launched app. "
            "Focus that app and try again."
        )

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    client_rect = Rect()
    get_client_rect = user32.GetClientRect
    get_client_rect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
    get_client_rect.restype = wintypes.BOOL
    if not get_client_rect(hwnd, ctypes.byref(client_rect)):
        raise OSError(ctypes.get_last_error(), "Could not read the app window bounds.")
    return user32, hwnd, client_rect


def _window_screen_bounds(user32, hwnd) -> tuple[int, int, int, int]:
    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    rect = Rect()
    get_window_rect = user32.GetWindowRect
    get_window_rect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
    get_window_rect.restype = wintypes.BOOL
    if not get_window_rect(hwnd, ctypes.byref(rect)):
        raise ServiceUnavailableError(
            "Windows could not read the selected app's screen bounds.",
            "APP_WINDOW_BOUNDS_UNAVAILABLE",
        )
    return rect.left, rect.top, rect.right, rect.bottom


def _uia_window(process_id: int, hwnd: int):
    try:
        from pywinauto import Desktop

        window = Desktop(backend="uia").window(handle=int(hwnd))
        if not window.exists(timeout=1):
            raise RuntimeError("The selected application window is no longer available.")
        if window.process_id() != process_id:
            raise RuntimeError("The selected window no longer belongs to the approved app.")
        return window
    except Exception as exc:
        raise ServiceUnavailableError(
            "Windows UI Automation could not inspect the selected app. "
            "Check that the app is open at the same privilege level as Jalora.",
            "ACCESSIBILITY_INSPECTION_FAILED",
        ) from exc


def inspect_local_app(user_id: str, app_id: str, *, mode: str = "accessibility") -> dict:
    if os.name != "nt":
        raise ServiceUnavailableError(
            "Desktop inspection is available only on Windows.",
            "LOCAL_APP_CONTROL_UNSUPPORTED",
        )
    with _process_lock:
        process = _launched_processes.get((user_id, app_id))
        if process is None or process.poll() is not None:
            raise ConflictError("Open this approved app through Jalora before inspecting it.")
        user32, hwnd, _ = _foreground_app_window(process.pid)
        if mode == "accessibility":
            window = _uia_window(process.pid, hwnd)
            controls = []
            try:
                for control in window.descendants()[:300]:
                    info = control.element_info
                    rect = info.rectangle
                    controls.append({
                        "name": str(info.name or "")[:300],
                        "control_type": str(info.control_type or "")[:80],
                        "automation_id": str(info.automation_id or "")[:200],
                        "enabled": bool(info.enabled),
                        "visible": bool(info.visible),
                        "bounds": {
                            "left": int(rect.left),
                            "top": int(rect.top),
                            "right": int(rect.right),
                            "bottom": int(rect.bottom),
                        },
                    })
            except Exception as exc:
                raise ServiceUnavailableError(
                    "Windows returned an incomplete accessibility tree for this app.",
                    "ACCESSIBILITY_INSPECTION_FAILED",
                ) from exc
            return {"mode": mode, "controls": controls, "truncated": len(controls) == 300}

        if mode != "screenshot_ocr":
            raise BadRequestError("Unsupported inspection mode.", "INVALID_INSPECTION_MODE")
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise ServiceUnavailableError(
                "Screenshot inspection requires Pillow.",
                "SCREENSHOT_DEPENDENCY_MISSING",
            ) from exc
        try:
            screenshot = ImageGrab.grab(
                bbox=_window_screen_bounds(user32, hwnd),
                all_screens=True,
            )
        except Exception as exc:
            raise ServiceUnavailableError(
                "Windows could not capture the selected app window.",
                "SCREENSHOT_CAPTURE_FAILED",
            ) from exc
        try:
            import pytesseract
        except ImportError as exc:
            raise ServiceUnavailableError(
                "OCR requires the pytesseract package and the Tesseract OCR executable.",
                "OCR_DEPENDENCY_MISSING",
            ) from exc
        configured_tesseract = os.environ.get("JALORA_TESSERACT_PATH")
        tesseract = configured_tesseract or shutil.which("tesseract")
        if not tesseract:
            raise ServiceUnavailableError(
                "OCR is not configured. Install Tesseract or set JALORA_TESSERACT_PATH.",
                "OCR_ENGINE_MISSING",
            )
        pytesseract.pytesseract.tesseract_cmd = tesseract
        try:
            text = pytesseract.image_to_string(screenshot, timeout=15)
        except Exception as exc:
            raise ServiceUnavailableError(
                "Tesseract could not read text from the selected app window.",
                "OCR_FAILED",
            ) from exc
        return {"mode": mode, "text": text[:12000], "truncated": len(text) > 12000}


def click_accessible_control(
    user_id: str, app_id: str, *, name: str, automation_id: str | None = None,
) -> dict:
    if os.name != "nt":
        raise ServiceUnavailableError(
            "Desktop input is available only on Windows.",
            "LOCAL_APP_CONTROL_UNSUPPORTED",
        )
    normalized_name = name.strip()
    normalized_id = (automation_id or "").strip()
    if not normalized_name and not normalized_id:
        raise BadRequestError(
            "Specify an accessible control name or automation ID.",
            "ACCESSIBLE_CONTROL_REQUIRED",
        )
    with _process_lock:
        process = _launched_processes.get((user_id, app_id))
        if process is None or process.poll() is not None:
            raise ConflictError("Open this approved app through Jalora before operating it.")
        _, hwnd, _ = _foreground_app_window(process.pid)
        window = _uia_window(process.pid, hwnd)
        try:
            matches = [
                control for control in window.descendants()
                if (not normalized_name or control.element_info.name == normalized_name)
                and (
                    not normalized_id
                    or control.element_info.automation_id == normalized_id
                )
            ]
            if len(matches) != 1:
                raise BadRequestError(
                    "The accessible control must match exactly one item. "
                    f"Jalora found {len(matches)} matches.",
                    "ACCESSIBLE_CONTROL_NOT_UNIQUE",
                )
            control = matches[0]
            if not control.is_enabled() or not control.is_visible():
                raise ConflictError("The selected accessible control is not available.")
            before = [
                (item.element_info.control_type, item.element_info.name)
                for item in window.descendants()[:300]
            ]
            _ensure_foreground_window(process.pid, hwnd)
            control.click_input()
            after = [
                (item.element_info.control_type, item.element_info.name)
                for item in window.descendants()[:300]
            ]
            return {
                "status": "clicked",
                "name": normalized_name,
                "automation_id": normalized_id,
                "verified": before != after,
                "verification": "accessibility_tree_changed" if before != after else "no_visible_change",
            }
        except (BadRequestError, ConflictError):
            raise
        except Exception as exc:
            raise ServiceUnavailableError(
                "Windows UI Automation could not perform or verify the selected action.",
                "ACCESSIBLE_ACTION_FAILED",
            ) from exc


def _ensure_foreground_window(process_id: int, expected_hwnd: int) -> None:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_foreground = user32.GetForegroundWindow
    get_foreground.restype = wintypes.HWND
    get_window_process_id = user32.GetWindowThreadProcessId
    get_window_process_id.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    get_window_process_id.restype = wintypes.DWORD
    hwnd = get_foreground()
    if hwnd != expected_hwnd:
        raise ConflictError(
            "The foreground window changed. Focus the selected app and confirm the action again."
        )
    window_process_id = wintypes.DWORD()
    if not get_window_process_id(hwnd, ctypes.byref(window_process_id)):
        raise OSError(ctypes.get_last_error(), "Could not verify the focused app window.")
    if window_process_id.value != process_id:
        raise ConflictError(
            "The foreground window no longer belongs to the selected app."
        )


def _send_inputs(user32, inputs: list) -> None:
    native_inputs = (_Input * len(inputs))(*inputs)
    send_input = user32.SendInput
    send_input.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    send_input.restype = wintypes.UINT
    sent = send_input(len(inputs), native_inputs, ctypes.sizeof(_Input))
    if sent != len(inputs):
        raise ServiceUnavailableError(
            "Windows did not accept the requested input. Check that the selected app "
            "is foreground and running at the same privilege level as Jalora.",
            "APP_INPUT_REJECTED",
        )


def _mouse_input(flags: int):
    return _Input(
        type=0,
        mi=_MouseInput(
            dx=0,
            dy=0,
            mouseData=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )


def _keyboard_input(vk: int, scan: int, flags: int):
    return _Input(
        type=1,
        ki=_KeyboardInput(
            wVk=vk,
            wScan=scan,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )


def interact_with_local_app(
    user_id: str,
    app_id: str,
    *,
    action: str,
    x: int | None = None,
    y: int | None = None,
    text: str | None = None,
    key: str | None = None,
) -> dict[str, int | str]:
    if os.name != "nt":
        raise ServiceUnavailableError(
            "Desktop input is available only on Windows.",
            "LOCAL_APP_CONTROL_UNSUPPORTED",
        )
    with _process_lock:
        process = _launched_processes.get((user_id, app_id))
        if process is None or process.poll() is not None:
            raise ConflictError(
                "Open this approved app through Jalora before operating it."
            )
        user32, hwnd, client_rect = _foreground_app_window(process.pid)
        if action == "click":
            if x is None or y is None:
                raise BadRequestError("Click requires x and y coordinates.")
            width = client_rect.right - client_rect.left
            height = client_rect.bottom - client_rect.top
            if x >= width or y >= height:
                raise BadRequestError(
                    f"Click coordinates must be inside the app client area ({width}x{height})."
                )
            point = ctypes.wintypes.POINT(x, y)
            client_to_screen = user32.ClientToScreen
            client_to_screen.argtypes = [
                ctypes.wintypes.HWND,
                ctypes.POINTER(ctypes.wintypes.POINT),
            ]
            client_to_screen.restype = ctypes.wintypes.BOOL
            if not client_to_screen(hwnd, ctypes.byref(point)):
                raise OSError(ctypes.get_last_error(), "Could not locate the click position.")
            set_cursor = user32.SetCursorPos
            set_cursor.argtypes = [ctypes.c_int, ctypes.c_int]
            set_cursor.restype = ctypes.wintypes.BOOL
            if not set_cursor(point.x, point.y):
                raise OSError(ctypes.get_last_error(), "Could not move the pointer.")
            _ensure_foreground_window(process.pid, hwnd)
            _send_inputs(
                user32,
                [
                    _mouse_input(_MOUSEEVENTF_LEFTDOWN),
                    _mouse_input(_MOUSEEVENTF_LEFTUP),
                ],
            )
            return {"status": "clicked", "x": x, "y": y}

        if action == "type":
            if not text:
                raise BadRequestError("Type action requires non-empty text.")
            encoded = text.encode("utf-16-le")
            inputs = []
            for offset in range(0, len(encoded), 2):
                code_unit = int.from_bytes(encoded[offset:offset + 2], "little")
                inputs.append(_keyboard_input(0, code_unit, _KEYEVENTF_UNICODE))
                inputs.append(
                    _keyboard_input(0, code_unit, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)
                )
            _ensure_foreground_window(process.pid, hwnd)
            _send_inputs(user32, inputs)
            return {"status": "typed", "characters": len(text)}

        if action == "key":
            if not key or key.upper() not in _KEYS:
                raise BadRequestError("Unsupported key. Use Enter, Tab, Esc, Backspace, Space, or an arrow.")
            virtual_key = _KEYS[key.upper()]
            _ensure_foreground_window(process.pid, hwnd)
            _send_inputs(
                user32,
                [
                    _keyboard_input(virtual_key, 0, 0),
                    _keyboard_input(virtual_key, 0, _KEYEVENTF_KEYUP),
                ],
            )
            return {"status": "key_pressed", "key": key.upper()}

        raise BadRequestError("Unsupported local app action.")


def close_local_app(user_id: str, app_id: str) -> dict[str, int | str]:
    key = (user_id, app_id)
    with _process_lock:
        process = _launched_processes.get(key)
        if process is None:
            raise ConflictError(
                "Jalora can close only an app process it launched in this session."
            )
        if process.poll() is not None:
            _launched_processes.pop(key, None)
            return {"status": "already_exited", "process_id": process.pid}
        close_windows = _request_window_close(process.pid)
        if close_windows == 0:
            raise ConflictError(
                "No visible app window was found. Jalora did not force-terminate it."
            )
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            return {
                "status": "close_requested",
                "process_id": process.pid,
                "windows_notified": close_windows,
            }
        _launched_processes.pop(key, None)
        return {
            "status": "closed",
            "process_id": process.pid,
            "windows_notified": close_windows,
        }
