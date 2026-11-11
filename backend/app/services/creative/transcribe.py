"""Speech-to-text for the Creative Studio, built on faster-whisper (local,
keyless). Produces timestamped segments (with word timings when available),
SRT/WebVTT serialisation, silence detection and filler-word flags used by
transcript-linked editing.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.services.creative import CreativeEngineError

logger = logging.getLogger("jalora.creative.transcribe")

_MODEL_CACHE: dict[str, object] = {}
_MODEL_LOCK = threading.Lock()

FILLER_WORDS = {
    "um", "uh", "er", "ah", "like", "you know", "basically", "actually",
    "literally", "right", "so", "well", "i mean", "kind of", "sort of",
}


def _model(size: str):
    with _MODEL_LOCK:
        if size in _MODEL_CACHE:
            return _MODEL_CACHE[size]
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover - dependency presence
            raise CreativeEngineError(
                "faster-whisper is not installed; transcription is unavailable.",
                "STT_UNAVAILABLE",
            ) from exc
        try:
            model = WhisperModel(size, device="cpu", compute_type="int8")
        except Exception as exc:
            raise CreativeEngineError(
                f"Could not load the Whisper '{size}' model: {exc}", "STT_MODEL_ERROR"
            ) from exc
        _MODEL_CACHE[size] = model
        return model


def transcribe(path: str | Path, *, model_size: str = "base",
               language: str | None = None) -> dict:
    """Return ``{language, duration, segments:[{start,end,text,words,is_filler}]}``."""
    model = _model(model_size)
    try:
        segments, info = model.transcribe(str(path), language=language,
                                          word_timestamps=True, vad_filter=True)
    except Exception as exc:
        raise CreativeEngineError(f"Transcription failed: {exc}", "STT_FAILED") from exc

    out: list[dict] = []
    for seg in segments:
        text = (seg.text or "").strip()
        words = []
        for w in (getattr(seg, "words", None) or []):
            words.append({"word": w.word.strip(), "start": round(w.start, 3),
                          "end": round(w.end, 3),
                          "prob": round(getattr(w, "probability", 0.0) or 0.0, 3)})
        low = text.lower().strip(" .,!?")
        out.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": text,
            "words": words,
            "is_filler": low in FILLER_WORDS,
        })
    return {
        "language": getattr(info, "language", language) or (language or "en"),
        "duration": round(getattr(info, "duration", 0.0) or 0.0, 3),
        "segments": out,
    }


def detect_silences(path: str | Path, *, noise_db: float = -35.0,
                    min_duration: float = 0.6) -> list[dict]:
    """Return silence spans ``[{start,end}]`` using a simple energy gate."""
    from app.services.creative import media_engine

    try:
        data, sr = _load_any(path)
    except Exception:
        return []
    import numpy as np

    window = max(1, int(0.05 * sr))
    n = len(data) // window
    if n == 0:
        return []
    rms = np.sqrt(np.array([np.mean(data[i * window:(i + 1) * window] ** 2)
                            for i in range(n)]) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    quiet = db < noise_db
    spans: list[dict] = []
    start = None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            dur = (i - start) * 0.05
            if dur >= min_duration:
                spans.append({"start": round(start * 0.05, 3), "end": round(i * 0.05, 3)})
            start = None
    if start is not None and (n - start) * 0.05 >= min_duration:
        spans.append({"start": round(start * 0.05, 3), "end": round(n * 0.05, 3)})
    return spans


def _load_any(path: str | Path):
    from app.services.creative import audio_engine

    try:
        return audio_engine.load_audio(path)
    except Exception:
        # decode via PyAV to a temp wav
        import tempfile

        from app.services.creative import media_engine

        tmp = Path(tempfile.mkdtemp()) / "decoded.wav"
        media_engine.extract_audio(path, tmp)
        return audio_engine.load_audio(tmp)


# ------------------------------------------------------------ serialisation

def _fmt_ts(seconds: float, *, comma: bool = True) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_ts(seg['start'])} --> {_fmt_ts(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)


def to_vtt(segments: list[dict]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_fmt_ts(seg['start'], comma=False)} --> {_fmt_ts(seg['end'], comma=False)}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)


def parse_subtitles(text: str) -> list[dict]:
    """Parse SRT or WebVTT into ``[{start,end,text}]``."""
    import re

    def parse_ts(ts: str) -> float:
        ts = ts.replace(",", ".")
        parts = re.split(r"[:.]", ts.strip())
        nums = [float(p) for p in parts if p != ""]
        while len(nums) < 4:
            nums.insert(0, 0.0)
        h, m, s, ms = nums[-4], nums[-3], nums[-2], nums[-1]
        return h * 3600 + m * 60 + s + ms / 1000.0

    blocks = re.split(r"\n\s*\n", text.strip().replace("\r\n", "\n"))
    out: list[dict] = []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip() and ln.strip() != "WEBVTT"]
        timing = next((ln for ln in lines if "-->" in ln), None)
        if not timing:
            continue
        a, b = [p.strip() for p in timing.split("-->")]
        idx = lines.index(timing)
        content = "\n".join(lines[idx + 1:]).strip()
        out.append({"start": parse_ts(a), "end": parse_ts(b), "text": content})
    return out
