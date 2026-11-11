"""Twilio Media Streams <-> VoiceSession bridge (Phase 2/3).

Offline coverage of the wire protocol: we synthesise real Twilio `connected`/
`start`/`media`/`stop` frames (8 kHz μ-law via Pipecat's serializer), drive the
bridge with a mock STT/TTS and a stubbed LLM, and assert the assistant's reply
comes back as Twilio `media` frames — proving the codec, the session wiring and
the signed-token gate without a phone number.
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from conftest import FAKE_SPEC
from pipecat.serializers.twilio import TwilioFrameSerializer
from sqlalchemy import select

from app.models.call import Call
from app.models.user import User
from app.database.session import SessionLocal
from app.services import voice_twilio
from app.services.model_router import TaskRoute
from app.services.voice_providers import MockSTT, MockTTS, STTChain, TTSChain
from app.services.voice_session import VoiceSession


def _user_id() -> str:
    with SessionLocal() as db:
        return db.scalar(select(User)).id


def _make_call(kind: str = "outbound", mode: str = "live") -> str:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        call = Call(
            user_id=_user_id(), kind=kind, mode=mode, provider="twilio",
            to_phone="+254700000000", from_phone="+15005550006",
            purpose="call the plumber about the leak",
            status="in_progress", answers={}, transcript=[],
            created_at=now, updated_at=now, started_at=now,
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        return call.id


def _speech_pcm(seconds: float) -> bytes:
    n = int(16000 * seconds)
    t = np.arange(n) / 16000.0
    return (np.sin(2 * np.pi * 220 * t) * 4000).astype("<i2").tobytes()


def _twilio_frames(speech_seconds: float = 1.2, silence_seconds: float = 1.2) -> list[dict]:
    """Build real Twilio frames: connect, start, media(speech+silence), stop."""
    ser = TwilioFrameSerializer(
        stream_sid="MZtest",
        params=TwilioFrameSerializer.InputParams(
            sample_rate=16000, twilio_sample_rate=8000, auto_hang_up=False),
    )
    setup = type("S", (), {
        "audio_in_sample_rate": 16000, "audio_out_sample_rate": 16000})()

    async def _build() -> list[dict]:
        from pipecat.frames.frames import AudioRawFrame

        await ser.setup(setup)
        frames: list[dict] = [
            {"event": "connected", "protocol": "Call", "version": "1.0.0"},
            {"event": "start", "sequenceNumber": "1", "start": {
                "streamSid": "MZtest", "accountSid": "ACtest", "callSid": "CAtest",
                "tracks": ["inbound"],
                "mediaFormat": {"encoding": "audio/x-mulaw",
                                "sampleRate": 8000, "channels": 1},
            }},
        ]
        pcm = _speech_pcm(speech_seconds) + bytes(int(16000 * silence_seconds) * 2)
        step = 320 * 2  # 20 ms @ 16 kHz, int16
        for off in range(0, len(pcm), step):
            chunk = pcm[off:off + step]
            if not chunk:
                continue
            out = await ser.serialize(AudioRawFrame(
                audio=chunk, sample_rate=16000, num_channels=1))
            if out:
                frames.append(json.loads(out))
        frames.append({"event": "stop", "streamSid": "MZtest"})
        return frames

    return asyncio.run(_build())


class _StubRouter:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def route(self, **kwargs):
        return TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="test")

    def route_chain(self, **kwargs):
        return [TaskRoute(provider=FAKE_SPEC, model="fake-model", reason="test")]

    def get_model(self, route):
        from langchain_core.messages import AIMessage

        from conftest import FakeModel

        model = FakeModel()
        model.script.append(AIMessage(content="", tool_calls=[{
            "name": "update_plan",
            "args": {"steps": [{
                "id": "voice_reply",
                "description": "Reply to the phone caller",
                "kind": "response",
                "dependencies": [],
            }]},
            "id": "phone_voice_plan",
            "type": "tool_call",
        }]))
        model.script.append(AIMessage(content=self._reply))
        return model


class _Script:
    """receive() backed by a fixed frame list; collects everything we send."""

    def __init__(self, frames: list[dict]) -> None:
        self._frames = list(frames)
        self.sent: list[dict] = []

    async def receive(self) -> dict | None:
        return self._frames.pop(0) if self._frames else None

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)


def _patch_engines(monkeypatch, phrase: str, reply: str) -> None:
    from app.services import voice_session as vs

    monkeypatch.setattr(vs, "build_stt", lambda s: STTChain([MockSTT(phrase)]))
    monkeypatch.setattr(vs, "build_tts", lambda s: TTSChain([MockTTS()]))
    monkeypatch.setattr(vs, "get_model_router", lambda: _StubRouter(reply))
    _ = VoiceSession


# --------------------------------------------------------------------------- #
# token + TwiML                                                                #
# --------------------------------------------------------------------------- #

def test_stream_token_roundtrip():
    token = voice_twilio.stream_token("call-a")
    assert voice_twilio.verify_stream_token("call-a", token)
    assert not voice_twilio.verify_stream_token("call-b", token)
    assert not voice_twilio.verify_stream_token("call-a", "not-a-token")


def test_answer_twiml_embeds_verifiable_token():
    call = type("C", (), {"id": "call.123"})()
    twiml = voice_twilio.answer_twiml(call)
    assert "<Connect>" in twiml and "<Stream" in twiml
    url = twiml.split('url="', 1)[1].split('"', 1)[0]
    assert url.startswith(("ws://", "wss://"))
    token = parse_qs(urlparse(url).query)["token"][0]
    assert voice_twilio.verify_stream_token("call.123", token)


def test_is_twilio_message_detection():
    assert voice_twilio.is_twilio_message({"event": "connected"})
    assert voice_twilio.is_twilio_message({"event": "start", "start": {}})
    assert voice_twilio.is_twilio_message(
        {"event": "media", "media": {"payload": "x"}})
    assert voice_twilio.is_twilio_message(
        {"event": "dtmf", "streamSid": "S", "dtmf": {"digit": "1"}})
    # legacy deterministic protocol must NOT be mistaken for Twilio
    assert not voice_twilio.is_twilio_message({"event": "transcript", "text": "hi"})
    assert not voice_twilio.is_twilio_message({"event": "dtmf", "text": "hello"})
    assert not voice_twilio.is_twilio_message(None)


def test_dtmf_decisions_use_the_shared_voice_approval_handler():
    decisions: list[bool] = []

    class Session:
        async def decide_pending_approval(self, yes: bool) -> None:
            decisions.append(yes)

    bridge = voice_twilio.TwilioMediaBridge("call", _Script([]).send)
    bridge._session = Session()

    async def decide() -> None:
        await bridge._on_dtmf({"dtmf": {"digit": "1"}})
        await bridge._on_dtmf({"dtmf": {"digit": "2"}})
        await bridge._on_dtmf({"dtmf": {"digit": "9"}})

    asyncio.run(decide())
    assert decisions == [True, False]


# --------------------------------------------------------------------------- #
# end-to-end bridge across the real codec                                      #
# --------------------------------------------------------------------------- #

def test_bridge_roundtrip(client, auth_headers, monkeypatch):
    _ = VoiceSession
    auth_headers()  # register the owner whose call row we bind
    _patch_engines(monkeypatch, "there is a leak under the sink",
                   "Sure, I'll call the plumber.")
    call_id = _make_call()

    frames = _twilio_frames()
    script = _Script(frames[1:])  # bridge receives the rest after `connected`

    asyncio.run(voice_twilio.run_bridge(
        call_id, frames[0], script.receive, script.send))

    media_out = [m for m in script.sent if m.get("event") == "media"]
    assert media_out, f"no audio returned to Twilio: {script.sent}"
    payload = media_out[0]["media"]["payload"]
    assert len(base64.b64decode(payload)) > 0
    assert all(m.get("streamSid") == "MZtest" for m in media_out)

    with SessionLocal() as db:
        call = db.get(Call, call_id)
        roles = [t.get("role") for t in (call.transcript or [])]
        assert "user" in roles and "agent" in roles
        assert call.sid == "CAtest"
        assert call.task_id and call.run_id and call.conversation_id
        from app.models.agent import AgentRun
        from app.models.task import Task

        assert db.get(Task, call.task_id).run_id == call.run_id
        assert db.get(AgentRun, call.run_id).task_id == call.task_id


def test_bridge_rejects_unknown_call(monkeypatch):
    _patch_engines(monkeypatch, "hi", "hello")
    script = _Script(_twilio_frames())
    # No call row for this id -> bridge must not raise and must not stream audio.
    asyncio.run(voice_twilio.run_bridge(
        "does-not-exist", {"event": "start", "start": {"streamSid": "S"}},
        script.receive, script.send))
    assert not script.sent


def test_verify_token_gate_used_by_route():
    # The media route rejects a mismatched token before bridging.
    assert not voice_twilio.verify_stream_token("call-x", voice_twilio.stream_token("y"))


# --------------------------------------------------------------------------- #
# API wiring: inbound webhook + media WS token gate                            #
# --------------------------------------------------------------------------- #

def test_media_ws_rejects_bad_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/calls/nope/media") as ws:
            ws.send_json({"event": "connected"})
            ws.receive_json()  # server closes 1008 -> raises


def test_inbound_webhook_rejects_when_disabled(client):
    resp = client.post("/api/calls/inbound",
                       data={"From": "+15551234567", "To": "+15005550006"})
    assert resp.status_code == 200
    assert "<Connect>" not in resp.text
    assert "<Hangup/>" in resp.text


def test_inbound_webhook_creates_live_inbound_call(client, auth_headers, monkeypatch):
    from app.config import get_settings

    auth_headers()  # owner = user@test.com
    settings = get_settings()
    monkeypatch.setattr(settings, "voice_inbound_enabled", True)
    monkeypatch.setattr(settings, "voice_inbound_email", "user@test.com")
    monkeypatch.setattr(settings, "voice_public_url", "https://jal.test")

    resp = client.post("/api/calls/inbound",
                       data={"From": "+15551234567", "To": "+15005550006"})
    assert resp.status_code == 200
    assert "<Connect>" in resp.text and "<Stream" in resp.text
    assert "wss://jal.test/api/calls/" in resp.text

    with SessionLocal() as db:
        call = db.scalar(select(Call).where(Call.kind == "inbound"))
    assert call is not None
    assert call.mode == "live" and call.provider == "twilio"
    assert call.from_phone == "+15551234567"


def test_inbound_webhook_no_owner_rejects(client, auth_headers, monkeypatch):
    from app.config import get_settings

    auth_headers()
    settings = get_settings()
    monkeypatch.setattr(settings, "voice_inbound_enabled", True)
    monkeypatch.setattr(settings, "voice_inbound_email", "nobody@test.com")
    resp = client.post("/api/calls/inbound", data={"From": "+1", "To": "+2"})
    assert resp.status_code == 200 and "<Connect>" not in resp.text
