"""Voice REST API (spec §31-33): server-side speech-to-text fallback.

The chat composer prefers the browser's Web Speech API for live dictation. When
that is unavailable (Firefox, older Safari, some mobile browsers) the frontend
records a short clip and posts it here; we reuse the same whisper providers as
the transcribe_audio tool so behaviour stays consistent (OpenAI audio API ->
local whisper.cpp -> offline mock).

Speech synthesis stays in the browser, so no reply audio is ever uploaded; the
/config endpoint reports capabilities so the client can pick a fallback.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, WebSocket

from app.api.deps import get_current_user
from app.auth.security import decode_access_token
from app.config import get_settings
from app.database.session import SessionLocal
from app.errors import BadRequestError, ServiceUnavailableError
from app.models.call import Call
from app.models.user import User
from app.schemas import TranscribeOut, VoiceConfigOut
from app.services.extractors import MIME_EXTENSIONS, sniff_mime

router = APIRouter(prefix="/api/voice", tags=["voice"])

MAX_CLIP_BYTES = 25 * 1024 * 1024


@router.get("/config", response_model=VoiceConfigOut)
def voice_config(user: User = Depends(get_current_user)) -> VoiceConfigOut:
    """What the server can do, so the client can pick a fallback. Transcription
    is always available (the whisper chain ends in an offline mock); the live
    call pipeline streams server-side TTS when enabled."""
    settings = get_settings()
    live = bool(getattr(settings, "voice_enabled", True))
    return VoiceConfigOut(
        stt=True,
        tts="server" if live else "browser",
        provider=settings.whisper_provider,
        max_clip_bytes=MAX_CLIP_BYTES,
        live=live,
        stt_provider=settings.voice_stt_provider,
        tts_provider=settings.voice_tts_provider,
        sample_rate=int(getattr(settings, "voice_sample_rate", 16000)),
        tts_sample_rate=int(getattr(settings, "voice_tts_sample_rate", 22050)),
        max_seconds=int(getattr(settings, "voice_max_seconds", 600)),
    )



@router.post("/transcribe", response_model=TranscribeOut)
async def transcribe_endpoint(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> TranscribeOut:
    """Transcribe a short recorded clip posted as multipart field ``file``.
    Magic-byte sniffing is enforced so a renamed binary can never pretend to be
    audio, and the same whisper chain as the transcribe_audio tool is reused."""
    content = await file.read()
    if not content:
        raise BadRequestError("The uploaded audio is empty.", "EMPTY_FILE")
    mime = sniff_mime(content)
    if not (mime.startswith("audio/") or mime.startswith("video/")):
        raise BadRequestError(
            "That file is not an audio recording.", "UNSUPPORTED_FORMAT",
        )
    if len(content) > MAX_CLIP_BYTES:
        raise BadRequestError(
            f"The recording exceeds the {MAX_CLIP_BYTES // (1024 * 1024)} MB limit.",
            "FILE_TOO_LARGE",
        )

    suffix = Path(file.filename or "").suffix or MIME_EXTENSIONS.get(mime, ".webm")
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(content)
        handle.close()
        from app.tools.media_tools import _transcribe

        transcript, provider = await _transcribe(
            Path(handle.name), file.filename or f"recording{suffix}")
    finally:
        Path(handle.name).unlink(missing_ok=True)
    if transcript is None:
        raise ServiceUnavailableError(
            "No transcription engine is configured (set an OpenAI key or "
            "install whisper.cpp).",
            code="WHISPER_UNAVAILABLE",
        )
    return TranscribeOut(transcript=transcript, provider=provider)


@router.websocket("/live")
async def live_call_websocket(websocket: WebSocket) -> None:
    """Live in-app two-way voice call (Phase 1).

    Auth is via ``?token=<jwt>`` (browsers cannot set headers on WebSockets).
    The     client sends a ``start`` control after its microphone and playback context
    are ready, then binary PCM16 @ 16 kHz frames plus JSON control messages
    (``end`` to finish an utterance, ``text`` for typed input, ``confirm`` for
    tool approvals, ``hangup`` to end). The server streams transcript, call
    state, tool and PCM16 audio events. See ``app.services.voice_session``.
    """
    settings = get_settings()
    if not getattr(settings, "voice_enabled", True):
        await websocket.close(code=1013)  # try again later
        return

    token = websocket.query_params.get("token", "")
    try:
        payload = decode_access_token(token)
    except Exception:  # noqa: BLE001 - any decode failure = unauthenticated
        await websocket.close(code=1008)  # policy violation
        return
    user_id = str(payload.get("sub", ""))
    with SessionLocal() as db:
        user = db.get(User, user_id)
    if user is None or not user.is_active:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    async def send(event: dict) -> None:
        try:
            await websocket.send_json(event)
        except Exception:  # noqa: BLE001 - client disconnected mid-call
            pass

    from app.services.voice_session import VoiceSession

    conversation_id = websocket.query_params.get("conversation_id") or None
    call_id = websocket.query_params.get("call_id") or None
    if call_id:
        with SessionLocal() as db:
            call = db.get(Call, call_id)
        if call is None or call.user_id != user.id:
            await websocket.close(code=1008)
            return
    from app.services import voice_session as voice_session_service

    session = VoiceSession.create(
        user_id=user.id,
        send=send,
        conversation_id=conversation_id,
        call_id=call_id,
        router=voice_session_service.get_model_router(),
    )
    try:
        await session.prepare()
        started = False
        while True:
            try:
                message = await websocket.receive()
            except Exception:  # noqa: BLE001 - client disconnected
                break
            mtype = message.get("type")
            if mtype == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                if started:
                    await session.handle_audio(message["bytes"])
                continue
            raw = message.get("text")
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") in ("hangup", "stop"):
                await session.close("hangup")
                break
            if data.get("type") == "start" and not started:
                purpose = str(data.get("purpose") or "").strip()
                if purpose:
                    session.purpose = purpose
                await session.start()
                started = True
                continue
            if not started:
                continue
            await session.handle_control(data)
    finally:
        await session.close("disconnect")
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
