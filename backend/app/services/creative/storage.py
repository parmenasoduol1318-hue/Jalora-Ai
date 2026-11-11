"""Asset storage helpers for the Creative Studio.

All creative media (uploads, generated images/videos, render outputs, thumbnails)
lives under the owner's artifact root ``data/uploads/<user_id>/creative/`` and is
served by the owner-only ``/api/artifacts`` route. This module centralises path
resolution, checksumming, magic-byte validation and :class:`MediaAsset` creation.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.creative import MediaAsset
from app.services import artifacts

# magic-byte prefixes -> (file_type, canonical extension)
_MAGIC: dict[bytes, tuple[str, str]] = {
    b"\xff\xd8\xff": ("image", ".jpg"),
    b"\x89PNG\r\n\x1a\n": ("image", ".png"),
    b"GIF87a": ("image", ".gif"),
    b"GIF89a": ("image", ".gif"),
    b"RIFF": ("audio", ".wav"),  # refined below (WAV vs WEBP)
    b"OggS": ("audio", ".ogg"),
    b"ID3": ("audio", ".mp3"),
    b"\x1aE\xdf\xa3": ("video", ".webm"),  # EBML (Matroska/WebM)
}
_ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
_ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".opus"}
_ALLOWED_TEXT_EXT = {".srt", ".vtt", ".txt", ".json"}


def creative_dir(user_id: str) -> Path:
    base = artifacts.user_upload_dir(user_id) / "creative"
    base.mkdir(parents=True, exist_ok=True)
    return base


def resolve(user_id: str, rel_path: str) -> Path:
    """Resolve ``rel_path`` inside the owner's artifact root, traversal-safe."""
    root = artifacts.user_upload_dir(user_id).resolve()
    path = (root / rel_path).resolve()
    if not str(path).startswith(str(root)):
        raise ValueError("path escapes the user's artifact root")
    return path


def sniff_and_classify(filename: str, content: bytes) -> tuple[str, str]:
    """Return ``(file_type, extension)`` from magic bytes, falling back to ext.

    Raises :class:`ValueError` if the content is not a supported media type.
    """
    ext = Path(filename or "").suffix.lower()
    head = content[:16]
    if head.startswith(b"\x89PNG"):
        return "image", ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image", ".jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image", ".gif"
    if head[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image", ".webp"
    if head[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return "audio", ".wav"
    if head.startswith(b"OggS"):
        return "audio", ".ogg"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio", ".mp3"
    if head.startswith(b"fLaC"):
        return "audio", ".flac"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video", ".webm"
    if head[4:8] == b"ftyp":
        return "video", ".mp4"
    # extension fallback for containers we cannot magic-sniff (mkv/avi/mov)
    if ext in _ALLOWED_IMAGE_EXT:
        return "image", ext
    if ext in _ALLOWED_VIDEO_EXT:
        return "video", ext
    if ext in _ALLOWED_AUDIO_EXT:
        return "audio", ext
    if ext in _ALLOWED_TEXT_EXT:
        return "text", ext
    raise ValueError(f"unsupported file type: {filename!r}")


def save_bytes(user_id: str, content: bytes, filename: str, *, subdir: str = "",
               force_ext: str | None = None) -> tuple[str, int, str]:
    """Persist raw bytes under the user's creative folder.

    Returns ``(rel_path, size_bytes, sha256_hex)`` where ``rel_path`` is relative
    to the artifact root (e.g. ``creative/uploads/ab12.png``).
    """
    folder = creative_dir(user_id)
    if subdir:
        folder = folder / subdir
        folder.mkdir(parents=True, exist_ok=True)
    ext = force_ext or Path(filename or "").suffix.lower() or ".bin"
    name = f"{uuid.uuid4().hex}{ext}"
    path = folder / name
    path.write_bytes(content)
    rel = path.relative_to(artifacts.user_upload_dir(user_id)).as_posix()
    return rel, len(content), hashlib.sha256(content).hexdigest()


def save_file(user_id: str, source: Path, *, subdir: str = "", name: str | None = None) -> tuple[str, int, str]:
    """Copy an on-disk file into the user's creative folder, returning rel path."""
    folder = creative_dir(user_id)
    if subdir:
        folder = folder / subdir
        folder.mkdir(parents=True, exist_ok=True)
    ext = source.suffix.lower()
    dest = folder / (name or f"{uuid.uuid4().hex}{ext}")
    if source.resolve() != dest.resolve():
        dest.write_bytes(source.read_bytes())
    data = dest.read_bytes()
    rel = dest.relative_to(artifacts.user_upload_dir(user_id)).as_posix()
    return rel, len(data), hashlib.sha256(data).hexdigest()


def create_asset(
    db: Session,
    user_id: str,
    *,
    rel_path: str,
    filename: str,
    file_type: str,
    project_id: str | None = None,
    size_bytes: int = 0,
    source: str = "upload",
    checksum: str | None = None,
    duration: float | None = None,
    width: int | None = None,
    height: int | None = None,
    thumbnail_path: str | None = None,
    metadata_json: dict | None = None,
) -> MediaAsset:
    asset = MediaAsset(
        id=uuid.uuid4().hex,
        user_id=user_id,
        project_id=project_id,
        filename=filename,
        file_path=rel_path,
        file_type=file_type,
        source=source,
        size_bytes=size_bytes,
        checksum=checksum,
        duration=duration,
        width=width,
        height=height,
        thumbnail_path=thumbnail_path,
        metadata_json=metadata_json or {},
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset
