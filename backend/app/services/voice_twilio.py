"""Twilio Media Streams <-> VoiceSession bridge (Phase 2/3, §31-33/§57).

Twilio phone calls (outbound *live* mode and inbound) are carried by a
bidirectional Media Streams WebSocket. This module bridges that wire protocol to
the same :class:`app.services.voice_session.VoiceSession` used by the in-app live
call, so both share one conversational engine.

The Twilio framing/codec (8 kHz μ-law <-> PCM16) is handled by Pipecat's
:class:`pipecat.serializers.twilio.TwilioFrameSerializer` — the locked transport
choice — while turn-taking, STT, LLM+tools, the spoken confirmation gate and TTS
stay in our session.

Flow::

    Twilio --media(ulaw 8k)--> serializer.deserialize -> PCM16@16k -> session
    session --audio event--> AudioRawFrame -> serializer.serialize -> media back

Transcript/tool events are surfaced to the app through the existing
``app.events`` bus (``VoiceSession`` persists turns on the ``Call`` row), so the
UI observes both in-app and phone calls identically.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

import jwt

from app.config import get_settings

logger = logging.getLogger("jalora.voice.twilio")

Receive = Callable[[], Awaitable[dict[str, Any] | None]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

# Twilio DTMF -> spoken-confirmation gate mapping.
_DTMF_YES = {"1"}
_DTMF_NO = {"2"}
_SPEECH_RMS = 300.0
_TWILIO_EVENTS = {"connected", "start", "media", "dtmf", "stop", "mark"}


def _rms(pcm16: bytes) -> float:
    if not pcm16:
        return 0.0
    import numpy as np

    samples = np.frombuffer(pcm16, dtype="<i2")
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


# --------------------------------------------------------------------------- #
# stream URL + signed token                                                   #
# --------------------------------------------------------------------------- #

def _public_ws_base() -> str:
    settings = get_settings()
    base = str(
        getattr(settings, "voice_public_url", None)
        or getattr(settings, "calls_base_url", None)
        or "http://localhost:8000"
    ).rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):]
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):]
    if base.startswith(("ws://", "wss://")):
        return base
    return "wss://" + base


def stream_token(call_id: str) -> str:
    """Short-lived signed token authorising a media stream for ``call_id``."""
    settings = get_settings()
    ttl = int(getattr(settings, "voice_stream_token_seconds", 3600) or 3600)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": call_id,
        "scope": "twilio_stream",
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_stream_token(call_id: str, token: str) -> bool:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token or "", settings.jwt_secret, algorithms=[settings.jwt_algorithm],
        )
    except Exception:  # noqa: BLE001 - any failure = unauthenticated
        return False
    return (
        payload.get("scope") == "twilio_stream"
        and str(payload.get("sub")) == call_id
    )


def answer_twiml(call: Any) -> str:
    """TwiML connecting a live call's audio to our media WebSocket."""
    base = _public_ws_base()
    token = stream_token(call.id)
    url = f"{base}/api/calls/{call.id}/media?token={token}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f'<Stream url="{url}">'
        f'<Parameter name="call_id" value="{call.id}"/>'
        "</Stream>"
        "</Connect></Response>"
    )


def reject_twiml(message: str = "This number is not accepting calls right now.") -> str:
    safe = (message or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{safe}</Say><Hangup/></Response>"
    )


def is_twilio_message(msg: dict[str, Any] | None) -> bool:
    """True when ``msg`` follows the Twilio Media Streams wire protocol."""
    if not isinstance(msg, dict):
        return False
    event = msg.get("event")
    if event not in _TWILIO_EVENTS:
        return False
    return event in ("connected", "stop") or "start" in msg or "media" in msg \
        or "streamSid" in msg


# --------------------------------------------------------------------------- #
# the bridge                                                                  #
# --------------------------------------------------------------------------- #

class TwilioMediaBridge:
    """Runs one Twilio Media Stream against a bound ``VoiceSession``."""

    def __init__(self, call_id: str, send: Send) -> None:
        self.call_id = call_id
        self._send = send
        self._session = None
        self._serializer = None
        self._stream_sid = ""
        self._speaking = False

    async def run(self, first: dict[str, Any], receive: Receive) -> None:
        from app.database.session import SessionLocal
        from app.models.call import Call
        from app.services.voice_session import VoiceSession

        with SessionLocal() as db:
            call = db.get(Call, self.call_id)
            if call is None:
                logger.warning("media stream for unknown call %s", self.call_id)
                return
            call.status = "in_progress"
            call.started_at = call.started_at or datetime.now(timezone.utc)
            db.commit()
            user_id, purpose, kind = call.user_id, (call.purpose or "a phone call"), \
                (call.kind or "outbound")

        start = await self._wait_for_start(first, receive)
        if not start:
            return
        await self._build_serializer(start)
        self._store_call_sid(start)

        from app.services import voice_session as voice_session_service

        session = VoiceSession.create(
            user_id=user_id, send=self.send_event, purpose=purpose,
            call_id=self.call_id, kind=kind,
            router=voice_session_service.get_model_router(),
        )
        self._session = session
        try:
            await session.start()
            await self._loop(receive)
        finally:
            if not session._closed:
                await session.close("hangup")

    async def _wait_for_start(self, first: dict[str, Any],
                              receive: Receive) -> dict[str, Any]:
        if first.get("event") == "start":
            return first
        while True:
            msg = await receive()
            if msg is None or msg.get("event") == "stop":
                return {}
            if msg.get("event") == "start":
                return msg

    async def _build_serializer(self, start: dict[str, Any]) -> None:
        from pipecat.serializers.twilio import TwilioFrameSerializer

        inner = start.get("start") if isinstance(start.get("start"), dict) else {}
        self._stream_sid = inner.get("streamSid") or start.get("streamSid") or ""
        call_sid = inner.get("callSid") or ""
        settings = get_settings()
        account_sid = getattr(settings, "twilio_account_sid", None)
        auth_token = getattr(settings, "twilio_auth_token", None)
        auto_hang_up = bool(call_sid and account_sid and auth_token)

        params = TwilioFrameSerializer.InputParams(
            sample_rate=int(getattr(settings, "voice_sample_rate", 16000)),
            twilio_sample_rate=8000,
            auto_hang_up=auto_hang_up,
        )
        self._serializer = TwilioFrameSerializer(
            stream_sid=self._stream_sid,
            call_sid=call_sid or None,
            account_sid=account_sid,
            auth_token=auth_token,
            params=params,
        )
        await self._serializer.setup(SimpleNamespace(
            audio_in_sample_rate=params.sample_rate,
            audio_out_sample_rate=int(
                getattr(settings, "voice_tts_sample_rate", 22050)),
        ))

    def _store_call_sid(self, start: dict[str, Any]) -> None:
        from app.database.session import SessionLocal
        from app.models.call import Call

        inner = start.get("start") if isinstance(start.get("start"), dict) else {}
        call_sid = inner.get("callSid") or ""
        if not call_sid:
            return
        with SessionLocal() as db:
            call = db.get(Call, self.call_id)
            if call is not None:
                call.sid = call_sid
                db.commit()

    async def _loop(self, receive: Receive) -> None:
        while True:
            msg = await receive()
            if msg is None or msg.get("event") == "stop":
                break
            event = msg.get("event")
            if event == "media":
                await self._on_media(msg)
            elif event == "dtmf":
                await self._on_dtmf(msg)

    async def _on_media(self, msg: dict[str, Any]) -> None:
        if self._serializer is None or self._session is None:
            return
        try:
            frame = await self._serializer.deserialize(json.dumps(msg))
        except Exception as exc:  # noqa: BLE001 - a bad frame must not kill the call
            logger.debug("dropping malformed media frame: %s", exc)
            return
        audio = getattr(frame, "audio", None)
        if not audio:
            return
        if self._speaking and _rms(audio) >= _SPEECH_RMS:
            self._speaking = False
            await self._send({"event": "clear", "streamSid": self._stream_sid})
        await self._session.handle_audio(audio)

    async def _on_dtmf(self, msg: dict[str, Any]) -> None:
        digit = str(msg.get("dtmf", {}).get("digit") or "")
        if self._session is None or not digit:
            return
        if digit in _DTMF_YES:
            await self._session.decide_pending_approval(True)
        elif digit in _DTMF_NO:
            await self._session.decide_pending_approval(False)

    # ---- VoiceSession -> Twilio -------------------------------------------- #

    async def send_event(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        if etype == "audio":
            await self._send_audio(event)
        elif etype == "audio_begin":
            self._speaking = True
        elif etype == "audio_end":
            await self._send({
                "event": "mark", "streamSid": self._stream_sid,
                "mark": {"name": "audio_end"},
            })

    async def _send_audio(self, event: dict[str, Any]) -> None:
        from pipecat.frames.frames import AudioRawFrame

        if self._serializer is None:
            return
        try:
            pcm = base64.b64decode(event.get("data") or "")
        except Exception:  # noqa: BLE001 - malformed base64: skip
            return
        if not pcm:
            return
        rate = int(event.get("sample_rate") or 22050)
        payload = await self._serializer.serialize(
            AudioRawFrame(audio=pcm, sample_rate=rate, num_channels=1))
        if not payload:
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        if self._stream_sid:
            data["streamSid"] = self._stream_sid
        await self._send(data)


async def run_bridge(call_id: str, first: dict[str, Any],
                     receive: Receive, send: Send) -> None:
    """Entry point used by the ``/api/calls/{id}/media`` route."""
    await TwilioMediaBridge(call_id, send).run(first, receive)
