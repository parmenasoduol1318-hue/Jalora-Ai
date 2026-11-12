from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pywintypes


def data_directory() -> Path:
    if os.name != "nt":
        raise RuntimeError("The Jalora Windows companion runs only on Windows.")
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise RuntimeError("Windows did not provide the current user's LOCALAPPDATA.")
    directory = Path(root) / "Jalora" / "WindowsCompanion"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_config() -> dict[str, Any]:
    path = data_directory() / "config.json"
    if not path.exists():
        return {"applications": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read the Windows companion config: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("applications"), list):
        raise TypeError("The Windows companion config has an invalid shape.")
    for application in data["applications"]:
        if (
            not isinstance(application, dict)
            or not isinstance(application.get("id"), str)
            or not re.fullmatch(r"[a-f0-9]{20}", application["id"])
            or not isinstance(application.get("name"), str)
            or not 1 <= len(application["name"]) <= 120
            or not isinstance(application.get("path"), str)
            or len(application["path"]) > 1024
            or Path(application["path"]).suffix.casefold() != ".exe"
            or not isinstance(application.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", application["sha256"])
        ):
            raise TypeError("The Windows companion app allowlist is invalid.")
    return data


def save_config(config: dict[str, Any]) -> None:
    path = data_directory() / "config.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_credentials() -> dict[str, Any] | None:
    path = data_directory() / "credentials.dpapi"
    if not path.exists():
        return None
    try:
        import win32crypt

        payload = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1]
        result = json.loads(payload.decode("utf-8"))
    except (OSError, ValueError, ImportError, pywintypes.error) as exc:
        raise RuntimeError(f"Could not decrypt Windows companion credentials: {exc}") from exc
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("device_token"), str)
        or not isinstance(result.get("api_base"), str)
        or not isinstance(result.get("device_id"), str)
        or not isinstance(result.get("scopes"), list)
    ):
        raise TypeError("The stored Windows companion credentials are invalid.")
    return result


def save_credentials(credentials: dict[str, Any]) -> None:
    import win32crypt

    path = data_directory() / "credentials.dpapi"
    temporary = path.with_suffix(".dpapi.tmp")
    payload = json.dumps(credentials, ensure_ascii=True).encode("utf-8")
    protected = win32crypt.CryptProtectData(
        payload,
        "Jalora Windows companion credentials",
        None,
        None,
        None,
        0,
    )
    temporary.write_bytes(protected)
    temporary.replace(path)


def clear_credentials() -> None:
    path = data_directory() / "credentials.dpapi"
    try:
        path.unlink()
    except FileNotFoundError:
        return
