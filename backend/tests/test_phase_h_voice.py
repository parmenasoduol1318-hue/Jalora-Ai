"""Phase H voice interface tests (§46-48): browser-fallback STT endpoint,
transcript->chat bridge, voice capability config and voice settings persistence."""
from __future__ import annotations

import io

from langchain_core.messages import AIMessage

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c636000000200010003a104f20000000049454e44ae426082"
)
MP3_MAGIC = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 8


def _transcribe(client, headers, filename="clip.mp3", content=MP3_MAGIC,
                mime="audio/mpeg"):
    return client.post("/api/voice/transcribe", headers=headers, files={
        "file": (filename, io.BytesIO(content), mime),
    })


# --------------------------------------------------------------------------- #
# capability config                                                            #
# --------------------------------------------------------------------------- #
def test_voice_config(client, auth_headers):
    resp = client.get("/api/voice/config", headers=auth_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stt"] is True
    # Phase 1: server-side streaming TTS for the live call pipeline.
    assert body["tts"] == "server"
    assert body["live"] is True
    assert body["max_clip_bytes"] == 25 * 1024 * 1024


def test_voice_config_requires_auth(client):
    assert client.get("/api/voice/config").status_code == 401


# --------------------------------------------------------------------------- #
# transcription endpoint                                                       #
# --------------------------------------------------------------------------- #
def test_transcribe_requires_auth(client):
    assert _transcribe(client, {}).status_code == 401


def test_transcribe_rejects_empty(client, auth_headers):
    resp = _transcribe(client, auth_headers(), content=b"")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "EMPTY_FILE"


def test_transcribe_rejects_non_audio(client, auth_headers):
    resp = _transcribe(client, auth_headers(), filename="cat.png",
                       content=PNG, mime="image/png")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_transcribe_mock_returns_transcript(client, auth_headers):
    # No OpenAI key and no whisper binary in tests -> deterministic mock path.
    resp = _transcribe(client, auth_headers("audio@test.com"), "memo.mp3")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "mock"
    assert body["transcript"]


# --------------------------------------------------------------------------- #
# transcript -> chat bridge (§47)                                              #
# --------------------------------------------------------------------------- #
def test_transcript_bridges_to_chat(client, fake_router, auth_headers):
    headers = auth_headers()
    resp = _transcribe(client, headers, filename="memo.mp3")
    assert resp.status_code == 200, resp.text
    transcript = resp.json()["transcript"]

    fake_router.script.append(AIMessage(content="Spoken reply from Jalora."))
    chat = client.post("/api/chat", headers=headers,
                       json={"message": transcript})
    assert chat.status_code == 200, chat.text
    assert chat.json()["reply"] == "Spoken reply from Jalora."
    seen = fake_router.model.tape[0]
    human = next(m for m in seen if getattr(m, "type", "") == "human")
    assert transcript in str(human.content)


# --------------------------------------------------------------------------- #
# voice settings persistence                                                   #
# --------------------------------------------------------------------------- #
def test_voice_settings_defaults(client, auth_headers):
    resp = client.get("/api/settings", headers=auth_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["voice"] == {
        "enabled": False, "auto_speak": True, "continuous": False, "voice": "",
    }


def test_voice_settings_patch_and_persist(client, auth_headers):
    headers = auth_headers()
    resp = client.patch("/api/settings", headers=headers, json={
        "voice": {"enabled": True, "auto_speak": True, "continuous": True,
                  "voice": "en-US-Aria"},
    })
    assert resp.status_code == 200, resp.text
    voice = resp.json()["voice"]
    assert voice == {"enabled": True, "auto_speak": True,
                     "continuous": True, "voice": "en-US-Aria"}

    got = client.get("/api/settings", headers=headers).json()["voice"]
    assert got == voice
