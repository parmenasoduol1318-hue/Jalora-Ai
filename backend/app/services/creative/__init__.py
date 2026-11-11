"""Creative Studio service layer.

Real media engines (PyAV for video/audio, Pillow for stills, numpy/scipy for
audio DSP, faster-whisper for speech), a provider-independent generation layer,
a validated AI editing agent, and a background job runner.

Nothing here fakes a capability: when an engine or provider is unavailable the
function raises a typed :class:`CreativeEngineError` (mapped to an honest HTTP
error) instead of returning a placeholder.
"""
from __future__ import annotations


class CreativeEngineError(RuntimeError):
    """An engine cannot perform the requested operation.

    ``code`` is a machine-readable reason surfaced to the UI so it can explain
    the failure honestly (e.g. ``PROVIDER_QUOTA``, ``CODEC_UNAVAILABLE``).
    """

    def __init__(self, message: str, code: str = "CREATIVE_ENGINE_ERROR",
                 *, provider: str | None = None):
        super().__init__(message)
        self.code = code
        self.provider = provider


__all__ = ["CreativeEngineError"]
