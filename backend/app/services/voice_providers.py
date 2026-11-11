"""Real-time voice providers: streaming STT and TTS for the live-call pipeline.

Design (locked):
  * Local engines are preferred (Ollama-first philosophy): faster-whisper for
    STT and Piper for TTS, both keyless and offline-capable.
  * Cloud providers are opt-in fallbacks (OpenAI audio API today).
  * Every provider resolves ``local -> cloud -> mock`` and every call is wrapped
    in a chain, so a missing engine, key or model degrades gracefully to the
    deterministic mock instead of failing the call.

The engines are imported lazily: importing this module never requires
``faster_whisper``/``piper``/``pipecat`` to be installed, so the app keeps
running (mock-first) without the voice extras.

Audio contract: callers pass raw PCM16 mono @ ``sample_rate`` (16 kHz by
default). faster-whisper is fed numpy arrays directly, so the PyAV decoder is
never invoked (we never decode compressed files here).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import types
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import numpy as np

from app.config import Settings, get_settings

logger = logging.getLogger("jalora.voice")


class VoiceProviderError(RuntimeError):
    """An STT/TTS provider could not fulfil a request (chain then falls back)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- #
# audio helpers                                                               #
# --------------------------------------------------------------------------- #

def pcm16_to_float32(data: bytes) -> "np.ndarray":
    """PCM16 little-endian bytes -> float32 mono array in [-1, 1]."""
    if not data:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(data, dtype="<i2").astype(np.float32)
    return samples / 32768.0


def float32_to_pcm16(array: "np.ndarray") -> bytes:
    clipped = np.clip(array, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def resample(array: "np.ndarray", src_rate: int, dst_rate: int) -> "np.ndarray":
    """Resample a mono float32 array. Uses soxr when available, else linear."""
    if src_rate == dst_rate or array.size == 0:
        return array
    try:
        import soxr  # type: ignore

        return soxr.resample(array, src_rate, dst_rate, quality="HQ").astype(np.float32)
    except Exception:  # noqa: BLE001 - soxr optional; fall back to linear
        n_out = int(round(array.size * dst_rate / src_rate))
        if n_out <= 0:
            return np.zeros(0, dtype=np.float32)
        src_idx = np.linspace(0.0, array.size - 1, num=n_out)
        return np.interp(src_idx, np.arange(array.size), array).astype(np.float32)


def _import_faster_whisper():
    """Import faster-whisper, tolerating a PyAV DLL block.

    Our streaming path feeds numpy arrays straight to the model and never calls
    ``decode_audio``; when a Windows Application Control policy blocks PyAV's
    DLL, importing the package still fails because ``faster_whisper.audio``
    imports ``av`` at module scope. A stub satisfies that import without
    touching the (never used) decoder.
    """
    try:
        import av  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - PyAV may be blocked or missing
        logger.warning("PyAV unavailable (%s); using decode stub (numpy path only)", exc)
        sys.modules.setdefault("av", types.ModuleType("av"))
    import faster_whisper  # type: ignore

    return faster_whisper


# --------------------------------------------------------------------------- #
# speech-to-text                                                              #
# --------------------------------------------------------------------------- #

class STTProvider:
    name = "stt"

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        raise NotImplementedError


class MockSTT(STTProvider):
    """Deterministic offline STT. Returns a fixed phrase (never real audio)."""

    name = "mock"

    def __init__(self, phrase: str = "") -> None:
        self._phrase = phrase

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        return self._phrase


class FasterWhisperSTT(STTProvider):
    name = "faster_whisper"

    def __init__(self, model_size: str, compute_type: str) -> None:
        self._model_size = model_size
        self._compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is None:
            fw = _import_faster_whisper()
            self._model = fw.WhisperModel(
                self._model_size, device="cpu", compute_type=self._compute_type,
            )
        return self._model

    def _transcribe_blocking(self, pcm16: bytes, sample_rate: int) -> str:
        audio = pcm16_to_float32(pcm16)
        if audio.size == 0:
            return ""
        if sample_rate != 16000:
            audio = resample(audio, sample_rate, 16000)
        model = self._load()
        segments, _info = model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        try:
            return await asyncio.to_thread(self._transcribe_blocking, pcm16, sample_rate)
        except VoiceProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as a provider error
            raise VoiceProviderError(
                "STT_FAILED", f"faster-whisper failed: {type(exc).__name__}: {exc}"
            ) from exc


class OpenAIWhisperSTT(STTProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        self._api_key = api_key
        self._model = model

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        import io
        import wave

        import httpx
        from app.services.model_router import get_async_client

        audio = pcm16_to_float32(pcm16)
        if sample_rate != 16000:
            audio = resample(audio, sample_rate, 16000)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(float32_to_pcm16(audio))
        buf.seek(0)
        files = {"file": ("audio.wav", buf, "audio/wav")}
        data = {"model": self._model}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            client = get_async_client()
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers, data=data, files=files, timeout=60.0,
            )
        except (httpx.HTTPError, OSError) as exc:
            raise VoiceProviderError("STT_FAILED", f"openai STT unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise VoiceProviderError(
                "STT_FAILED", f"openai STT {resp.status_code}: {resp.text[:200]}"
            )
        return (resp.json().get("text") or "").strip()


# --------------------------------------------------------------------------- #
# text-to-speech                                                              #
# --------------------------------------------------------------------------- #

class TTSProvider:
    name = "tts"
    sample_rate = 22050

    async def synthesize(self, text: str) -> bytes:
        """Return PCM16 mono audio at ``self.sample_rate``."""
        raise NotImplementedError

    async def synthesize_stream(self, chunks: "AsyncIterator[str]"):
        """Yield PCM16 audio frames for a stream of text chunks.

        Default implementation falls back to whole-utterance ``synthesize`` per
        chunk so any provider gains a streaming interface at the cost of chunk
        boundaries. Providers with true token-at-a-time TTS should override.
        """
        async for text in chunks:
            if not text:
                continue
            try:
                audio = await self.synthesize(text)
            except VoiceProviderError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise VoiceProviderError(
                    "TTS_FAILED", f"{self.name} streaming chunk failed: {exc}"
                ) from exc
            yield audio


class MockTTS(TTSProvider):
    """Silent PCM covering a rough speaking duration. Deterministic/offline."""

    name = "mock"
    sample_rate = 16000

    def __init__(self, words_per_minute: int = 150) -> None:
        self._wpm = words_per_minute

    async def synthesize(self, text: str) -> bytes:
        words = max(1, len((text or "").split()))
        seconds = words / self._wpm * 60.0
        n = int(self.sample_rate * min(seconds, 30.0))
        return bytes(n * 2)  # zeroed int16 samples


class PiperTTS(TTSProvider):
    name = "piper"

    def __init__(self, voice_path: str) -> None:
        self._voice_path = voice_path
        self._voice = None
        self.sample_rate = 22050

    def _load(self):
        if self._voice is None:
            from piper.voice import PiperVoice  # type: ignore

            self._voice = PiperVoice.load(self._voice_path)
            self.sample_rate = int(getattr(self._voice.config, "sample_rate", 22050))
        return self._voice

    def _synthesize_blocking(self, text: str) -> bytes:
        voice = self._load()
        chunks = [chunk.audio_int16_bytes for chunk in voice.synthesize(text)]
        return b"".join(chunks)

    async def synthesize(self, text: str) -> bytes:
        try:
            return await asyncio.to_thread(self._synthesize_blocking, text)
        except VoiceProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VoiceProviderError(
                "TTS_FAILED", f"piper failed: {type(exc).__name__}: {exc}"
            ) from exc


class OpenAITTS(TTSProvider):
    name = "openai"
    sample_rate = 24000

    def __init__(self, api_key: str, model: str = "gpt-4o-mini-tts",
                 voice: str = "alloy") -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice

    async def synthesize(self, text: str) -> bytes:
        import httpx
        from app.services.model_router import get_async_client

        headers = {"Authorization": f"Bearer {self._api_key}"}
        body = {
            "model": self._model,
            "voice": self._voice,
            "input": text,
            "response_format": "pcm",
        }
        try:
            client = get_async_client()
            resp = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers=headers, json=body, timeout=60.0,
            )
        except (httpx.HTTPError, OSError) as exc:
            raise VoiceProviderError("TTS_FAILED", f"openai TTS unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise VoiceProviderError(
                "TTS_FAILED", f"openai TTS {resp.status_code}: {resp.text[:200]}"
            )
        # The speech endpoint returns raw PCM16 little-endian at 24 kHz.
        return resp.content


# --------------------------------------------------------------------------- #
# chains + resolution                                                         #
# --------------------------------------------------------------------------- #

class STTChain:
    def __init__(self, providers: list[STTProvider]) -> None:
        self.providers = providers

    @property
    def name(self) -> str:
        return self.providers[0].name if self.providers else "none"

    def describe(self) -> list[str]:
        return [p.name for p in self.providers]

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> tuple[str, str]:
        last: VoiceProviderError | None = None
        for provider in self.providers:
            try:
                return await provider.transcribe(pcm16, sample_rate), provider.name
            except VoiceProviderError as exc:
                last = exc
                logger.warning("STT provider %s failed: %s", provider.name, exc)
        raise last or VoiceProviderError("STT_UNAVAILABLE", "no STT provider available")


class TTSChain:
    def __init__(self, providers: list[TTSProvider]) -> None:
        self.providers = providers

    @property
    def name(self) -> str:
        return self.providers[0].name if self.providers else "tts"
    sample_rate = 22050

    def describe(self) -> list[str]:
        return [p.name for p in self.providers]

    async def synthesize(self, text: str) -> tuple[bytes, int, str]:
        last: VoiceProviderError | None = None
        for provider in self.providers:
            try:
                audio = await provider.synthesize(text)
                return audio, provider.sample_rate, provider.name
            except VoiceProviderError as exc:
                last = exc
                logger.warning("TTS provider %s failed: %s", provider.name, exc)
        raise last or VoiceProviderError("TTS_FAILED", "no TTS provider available")

    async def synthesize_stream(self, chunks: "AsyncIterator[str]"):
        """Yield ``(pcm16, sample_rate, name)`` per non-empty text chunk.

        Offline-safe by construction: each chunk is resolved through the chain's
        ``synthesize`` fallback, so ``MockTTS`` (which returns a short zeroed
        frame per chunk) drives an incremental audio stream with no engine
        installed. Real providers can override ``synthesize_stream`` for true
        token-at-a-time synthesis.
        """
        async for text in chunks:
            if not text:
                continue
            last: VoiceProviderError | None = None
            for provider in self.providers:
                try:
                    audio = await provider.synthesize(text)
                except VoiceProviderError as exc:
                    last = exc
                    logger.warning("TTS provider %s failed: %s", provider.name, exc)
                    continue
                yield audio, provider.sample_rate, provider.name
                break
            else:
                raise last or VoiceProviderError("TTS_FAILED", "no TTS provider available")


def _file_exists(path: str) -> bool:
    from pathlib import Path

    return bool(path) and Path(path).is_file()


def _stt_candidates(settings, preferred: str) -> list[STTProvider]:
    local = FasterWhisperSTT(settings.voice_stt_model, settings.voice_stt_compute_type)
    cloud = OpenAIWhisperSTT(settings.openai_api_key) if settings.openai_api_key else None
    mapping = {"faster_whisper": local, "openai": cloud}
    order = [preferred] if preferred != "auto" else ["faster_whisper", "openai"]
    candidates: list[STTProvider] = [
        mapping[name] for name in order if mapping.get(name) is not None
    ]
    candidates.append(MockSTT())
    return candidates


def _tts_candidates(settings, preferred: str) -> list[TTSProvider]:
    local = (
        PiperTTS(settings.piper_voice_path)
        if settings.piper_voice_path and _file_exists(settings.piper_voice_path)
        else None
    )
    cloud = OpenAITTS(settings.openai_api_key) if settings.openai_api_key else None
    mapping = {"piper": local, "openai": cloud}
    order = [preferred] if preferred != "auto" else ["piper", "openai"]
    candidates: list[TTSProvider] = [
        mapping[name] for name in order if mapping.get(name) is not None
    ]
    candidates.append(MockTTS())
    return candidates


def build_stt(settings) -> STTChain:
    """Build the STT chain (local -> cloud -> mock) for the configured provider."""
    return STTChain(_stt_candidates(settings, settings.voice_stt_provider))


def build_tts(settings) -> TTSChain:
    """Build the TTS chain (local -> cloud -> mock) for the configured provider."""
    return TTSChain(_tts_candidates(settings, settings.voice_tts_provider))
