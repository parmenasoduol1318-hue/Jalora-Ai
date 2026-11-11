"""Artifact storage for browser screenshots/downloads (spec §16).

Files land under ``data/uploads/<user_id>/`` and are served through the
authenticated ``GET /api/artifacts/{user_id}/{path}`` endpoint. Names are
always server-generated so traversal is impossible.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import quote

from app.config import get_settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def user_upload_dir(user_id: str) -> Path:
    base = get_settings().upload_dir / user_id
    base.mkdir(parents=True, exist_ok=True)
    return base


def artifact_url(user_id: str, file_name: str) -> str:
    return f"/api/artifacts/{quote(user_id)}/{quote(file_name)}"


def safe_file_name(name: str) -> str:
    name = _SAFE_NAME.sub("_", name).strip("._") or "file"
    return name[:120]


def new_artifact_path(user_id: str, prefix: str, extension: str,
                      subdir: str = "") -> Path:
    ext = extension.lstrip(".").lower()
    name = f"{prefix}_{int(time.time() * 1000)}.{ext}"
    folder = user_upload_dir(user_id)
    if subdir:
        folder = folder / subdir
        folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def artifact_info(user_id: str, path: Path, *, kind: str, mime: str,
                  title: str) -> dict:
    rel = path.relative_to(user_upload_dir(user_id)).as_posix()
    url = artifact_url(user_id, rel)
    return {
        "kind": kind,
        "url": url,
        "file_name": path.name,
        "download_url": f"{url}?download=1",
        "mime": mime,
        "title": title,
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }