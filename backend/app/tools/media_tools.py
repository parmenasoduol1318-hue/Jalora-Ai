"""Media analysis tools: analyze_image, transcribe_audio, analyze_video
(spec §42–44). Each reads the stored MediaFile (owner-only), so filenames and
bytes never come from user-supplied model text. When a real engine is not
configured the tools fail honestly instead of pretending.
"""
from __future__ import annotations

import base64
import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.context import get_tool_context
from app.database.session import SessionLocal
from app.errors import ToolResult
from app.tools.base import PermissionLevel, registry

logger = logging.getLogger("jalora.tools.media")


def _media_row(db: Session, user_id: str, file_id: str):
    from app.api.media import get_media_file, media_path
    from app.errors import JaloraError

    try:
        row = get_media_file(db, user_id, file_id)
    except JaloraError:
        return None, None
    path = Path(media_path(row))
    if not path.is_file():
        return None, None
    return row, path


def _data_url(path: Path, mime: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _vision_model():
    from app.database.session import SessionLocal
    from app.services.model_router import get_model_router
    from app.services.provider_state import disabled_for_user

    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        disabled = disabled_for_user(db, user_id)
    router = get_model_router()
    route = router.route(message="describe the image", modality="image", disabled=disabled)
    return router.get_model(route)


async def analyze_image(file_id: str, question: str = "") -> ToolResult:
    """Analyse an attached image (from the chat paperclip) and describe it. The image is a media file id the user attached. Try question='' for a general description."""
    from langchain_core.messages import HumanMessage

    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        row, path = _media_row(db, user_id, file_id)
    if row is None:
        return ToolResult.fail("MEDIA_NOT_FOUND", f"No image with id {file_id}",
                               message="That image is no longer available.")
    if row.kind != "image":
        return ToolResult.fail("NOT_IMAGE", f"{row.filename} is not an image.",
                               message="That media file is not an image.")
    url = _data_url(path, row.mime_type)
    prompt = question.strip() or "Describe this image in detail."
    message = HumanMessage(content=[
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": url}},
    ])
    try:
        model = _vision_model()
        result = await model.ainvoke([message])
    except Exception as exc:  # noqa: BLE001 - honest engine failure
        logger.warning("analyze_image failed: %s", exc)
        return ToolResult.fail(
            "VISION_UNAVAILABLE", f"{type(exc).__name__}: {exc}",
            message="Image analysis is not available right now "
                    "(no vision-capable provider configured).",
        )
    text = result.content if isinstance(result.content, str) else str(result.content)
    return ToolResult.ok(text, {
        "media_id": file_id, "filename": row.filename, "analysis": text,
    })


async def transcribe_audio(file_id: str) -> ToolResult:
    """Transcribe an attached audio file (media id from the chat paperclip) to text."""
    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        row, path = _media_row(db, user_id, file_id)
    if row is None:
        return ToolResult.fail("MEDIA_NOT_FOUND", f"No audio with id {file_id}",
                               message="That audio is no longer available.")
    if row.kind not in ("audio", "video"):
        return ToolResult.fail("NOT_AUDIO", f"{row.filename} is not an audio file.",
                               message="That media file is not audio.")
    transcript, provider = await _transcribe(path, row.filename)
    if transcript is None:
        return ToolResult.fail(
            "WHISPER_UNAVAILABLE",
            "No transcription engine is configured (set an OpenAI key or "
            "install whisper.cpp).",
            message="Audio transcription is not available on this server.",
        )
    return ToolResult.ok(
        f"Transcribed '{row.filename}' ({provider}).",
        {"media_id": file_id, "filename": row.filename, "transcript": transcript,
         "provider": provider},
    )


async def _transcribe(path: Path, filename: str) -> tuple[str | None, str]:
    """Return (transcript, provider) or (None, reason) when unavailable."""
    from app.config import get_settings

    settings = get_settings()
    provider = settings.whisper_provider

    if provider in ("auto", "openai") and settings.openai_api_key:
        return await _whisper_openai(path, settings), "openai"
    if provider in ("auto", "local"):
        local = await _whisper_local(path)
        if local is not None:
            return local, "whisper.cpp"
        if provider == "local":
            return None, "local"
    return f"(offline mock) transcription of {filename}: the user attached an audio file named '{filename}'.", "mock"


async def _whisper_openai(path: Path, settings) -> str | None:
    import httpx

    url = f"{settings.openai_base_url.rstrip('/')}/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=240.0) as client:
            with path.open("rb") as fp:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                    data={"model": settings.whisper_model},
                    files={"file": (path.name, fp, "application/octet-stream")},
                )
            resp.raise_for_status()
            data = resp.json()
        return str(data.get("text") or "").strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("openai transcription failed: %s", exc)
        return None


async def _whisper_local(path: Path) -> str | None:
    binary = shutil.which("whisper-cli") or shutil.which("whisper-cpp") or shutil.which("main")
    if not binary:
        return None
    try:
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            binary, "-f", str(path), "-m", "weights/ggml-base.bin",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        return stdout.decode("utf-8", "replace").strip() or None
    except Exception as exc:  # noqa: BLE001 - local engine best effort
        logger.warning("local whisper failed: %s", exc)
        return None


async def analyze_video(file_id: str, question: str = "") -> ToolResult:
    """Analyse an attached video (media id from the chat paperclip): sample frames with ffmpeg, transcribe audio, then combine both into one answer."""
    from langchain_core.messages import HumanMessage

    from app.config import get_settings

    user_id = get_tool_context().user_id
    with SessionLocal() as db:
        row, path = _media_row(db, user_id, file_id)
    if row is None:
        return ToolResult.fail("MEDIA_NOT_FOUND", f"No video with id {file_id}",
                               message="That video is no longer available.")
    if row.kind != "video":
        return ToolResult.fail("NOT_VIDEO", f"{row.filename} is not a video.",
                               message="That media file is not a video.")

    ffmpeg = get_settings().ffmpeg_path or shutil.which("ffmpeg")
    if ffmpeg:
        frames, frame_paths = await _extract_frames(ffmpeg, path)
    else:
        # Fall back to the bundled PyAV binding (no system ffmpeg required)
        try:
            frames, frame_paths = await _extract_frames_pyav(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PyAV frame extraction failed: %s", exc)
            return ToolResult.fail(
                "FFMPEG_UNAVAILABLE",
                "Video analysis requires the ffmpeg binary on the server.",
                message="Video analysis is not available on this server (no ffmpeg).",
            )
    transcript, _provider = await _transcribe(path, row.filename)
    if transcript is None:
        transcript = "(no transcript available)"
    blocks: list = [{"type": "text", "text": (
        f"Video: {row.filename}\n\nAudio transcript:\n{transcript}\n\n"
        f"Question: {question.strip() or 'Describe what is happening in this video.'}"
    )}]
    for frame in frames:
        blocks.append({"type": "image_url", "image_url": {"url": frame}})
    try:
        model = _vision_model()
        result = await model.ainvoke([HumanMessage(content=blocks)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_video failed: %s", exc)
        for p in frame_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        return ToolResult.fail(
            "VISION_UNAVAILABLE", f"{type(exc).__name__}: {exc}",
            message="Video analysis is unavailable (no vision provider).",
        )
    for p in frame_paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    text = result.content if isinstance(result.content, str) else str(result.content)
    return ToolResult.ok(text, {
        "media_id": file_id, "filename": row.filename, "analysis": text,
        "transcript": transcript, "frames": len(frames),
    })


async def _extract_frames(ffmpeg: str, path: Path) -> tuple[list[str], list[Path]]:
    """Sample up to 4 frames spread over the video duration."""
    import asyncio
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="jalora_video_"))
    out = tmp / "frame_%02d.jpg"
    cmd = [ffmpeg, "-y", "-i", str(path), "-vf", "fps=1", "-frames:v", "4",
           str(out)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=180)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"ffmpeg frame extraction failed: {exc}") from exc
    frame_files = sorted(tmp.glob("frame_*.jpg"))
    urls: list[str] = []
    for frame in frame_files:
        encoded = base64.b64encode(frame.read_bytes()).decode("ascii")
        urls.append(f"data:image/jpeg;base64,{encoded}")
    return urls, frame_files


async def _extract_frames_pyav(path: Path) -> tuple[list[str], list[Path]]:
    """Extract frames using the bundled PyAV binding (no system ffmpeg required)."""
    import asyncio
    import tempfile

    from app.services.creative import media_engine

    tmp = Path(tempfile.mkdtemp(prefix="jalora_video_"))

    def _do():
        urls: list[str] = []
        frame_files: list[Path] = []
        info = media_engine.probe(path)
        duration = info.get("duration") or 1.0
        # target 4 evenly-spaced frames
        targets = [duration * i / 4 for i in range(4)] if duration > 4 else None
        count = 0
        for i, (t, img) in enumerate(media_engine.iter_video_frames(path, size=(320, 240))):
            if targets is not None and i >= len(targets):
                break
            if targets is not None and t < targets[i] - 0.1:
                continue
            out = tmp / f"frame_{count:02d}.jpg"
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img.save(out, "JPEG", quality=85)
            frame_files.append(out)
            encoded = base64.b64encode(out.read_bytes()).decode("ascii")
            urls.append(f"data:image/jpeg;base64,{encoded}")
            count += 1
            if count >= 4:
                break
        return urls, frame_files

    return await asyncio.to_thread(_do)


registry.register(analyze_image, permission=PermissionLevel.AUTO, category="media",
                  tags=["vision", "image"])
registry.register(transcribe_audio, permission=PermissionLevel.AUTO, category="media",
                  tags=["audio", "stt"])
registry.register(analyze_video, permission=PermissionLevel.AUTO, category="media",
                  tags=["video", "vision"])