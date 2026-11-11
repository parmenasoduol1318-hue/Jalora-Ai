"""Audio & voice engine (soundfile + numpy/scipy, PyAV for compressed export).

Multitrack mix, gain/fade/trim/reverse, peak & loudness normalisation,
spectral-gate noise reduction, parametric EQ, simple dynamics compression,
music ducking under speech, crossfades, beat detection and waveform peaks.

Output is always a real audio file; nothing is simulated.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from app.services.creative import CreativeEngineError


def load_audio(path: str | Path) -> tuple[np.ndarray, int]:
    """Load audio as float32 mono/stereo array shaped (samples[, channels])."""
    import soundfile as sf

    data, sr = sf.read(str(path), always_2d=False, dtype="float32")
    if data.ndim == 2 and data.shape[1] == 1:
        data = data[:, 0]
    return data, int(sr)


def save_audio(path: str | Path, data: np.ndarray, sr: int) -> Path:
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    sf.write(str(path), data, int(sr))
    return path


def encode_audio(path: str | Path, data: np.ndarray, sr: int, codec: str = "aac",
                 bitrate: int = 192000) -> Path:
    """Export audio to a compressed container (aac/mp3/opus) via PyAV."""
    import av

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    if data.ndim == 1:
        data = data[:, None]
    layout = "stereo" if data.shape[1] >= 2 else "mono"
    if data.shape[1] > 2:
        data = data[:, :2]
        layout = "stereo"
    codec_name = {"mp3": "libmp3lame", "aac": "aac", "m4a": "aac", "opus": "libopus",
                  "ogg": "libvorbis"}.get(codec.lower(), "aac")
    fmt = {"webm": "webm", "ogg": "ogg", "matroska": "matroska"}.get(path.suffix.lstrip("."), "mp4")
    out = av.open(str(path), mode="w", format=fmt)
    stream = out.add_stream(codec_name, rate=sr)
    stream.bit_rate = bitrate
    stream.layout = layout
    stream.sample_rate = sr
    frame_size = 1024
    for start in range(0, len(data), frame_size):
        chunk = data[start:start + frame_size]
        if len(chunk) == 0:
            continue
        af = av.AudioFrame.from_ndarray(chunk.T.astype(np.float32), format="fltp", layout=layout)
        af.sample_rate = sr
        for pkt in stream.encode(af):
            out.mux(pkt)
    for pkt in stream.encode():
        out.mux(pkt)
    out.close()
    return path


_AUDIO_EXTS = {".wav", ".flac", ".ogg", ".aiff", ".aif", ".mp3", ".m4a", ".aac", ".opus", ".webm"}


def save_auto(path: str | Path, data: np.ndarray, sr: int) -> Path:
    """Save as WAV/FLAC natively; compressed formats via PyAV."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in {".wav", ".flac", ".ogg", ".aiff", ".aif"}:
        return save_audio(path, data, sr)
    if ext in _AUDIO_EXTS:
        return encode_audio(path, data, sr, codec=ext.lstrip("."))
    return save_audio(path.with_suffix(".wav"), data, sr)


# ------------------------------------------------------------------ editing

def apply_gain(data: np.ndarray, gain_db: float) -> np.ndarray:
    return data * (10.0 ** (gain_db / 20.0))


def normalize(data: np.ndarray, target_db: float = -1.0) -> np.ndarray:
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak <= 1e-9:
        return data
    target = 10.0 ** (target_db / 20.0)
    return data * (target / peak)


def normalize_loudness(data: np.ndarray, sr: int, target_lufs: float = -16.0) -> np.ndarray:
    """Approximate loudness normalisation using RMS relative to a -23 LUFS ref."""
    if data.size == 0:
        return data
    rms = float(np.sqrt(np.mean(data ** 2)))
    if rms <= 1e-9:
        return data
    current_lufs = 20.0 * math.log10(rms) - 0.691
    gain_db = target_lufs - current_lufs
    out = apply_gain(data, gain_db)
    return normalize(out, -1.0)


def fade(data: np.ndarray, sr: int, fade_in: float = 0.0, fade_out: float = 0.0) -> np.ndarray:
    out = np.array(data, copy=True)
    n = len(out)
    fi = min(int(fade_in * sr), n)
    fo = min(int(fade_out * sr), n)
    if fi > 0:
        out[:fi] *= np.linspace(0, 1, fi).reshape((-1,) + (1,) * (out.ndim - 1))
    if fo > 0:
        out[-fo:] *= np.linspace(1, 0, fo).reshape((-1,) + (1,) * (out.ndim - 1))
    return out


def trim(data: np.ndarray, sr: int, start: float, end: float) -> np.ndarray:
    a = max(0, int(start * sr))
    b = min(len(data), int(end * sr))
    return data[a:b]


def reverse(data: np.ndarray) -> np.ndarray:
    return data[::-1].copy()


def change_speed(data: np.ndarray, factor: float) -> np.ndarray:
    """Resample-based speed change (pitch shifts, like tape speed)."""
    factor = max(0.05, min(20.0, float(factor)))
    n = int(len(data) / factor)
    idx = np.linspace(0, len(data) - 1, max(1, n))
    flat = np.asarray(data)
    return np.interp(idx, np.arange(len(flat)), flat).astype(np.float32)


def crossfade(a: np.ndarray, b: np.ndarray, sr: int, duration: float = 0.5) -> np.ndarray:
    frames = max(1, int(duration * sr))
    frames = min(frames, len(a), len(b))
    a = np.asarray(a)
    b = np.asarray(b)
    out = np.concatenate([a[:len(a) - frames], a[len(a) - frames:] * np.linspace(1, 0, frames) +
                          b[:frames] * np.linspace(0, 1, frames), b[frames:]])
    return out.astype(np.float32)


def mix(tracks: list[dict], *, total_duration: float | None = None) -> tuple[np.ndarray, int]:
    """Mix ``tracks`` = [{data, sr, start, volume}] into one buffer.

    Enables automatic ducking when a track dict has ``duck_under`` = <track index>.
    """
    if not tracks:
        raise CreativeEngineError("No audio tracks to mix.", "EMPTY_INPUT")
    sr = max(int(t.get("sr", 44100)) for t in tracks)
    end = 0.0
    norm: list[tuple[np.ndarray, int, float]] = []
    for t in tracks:
        data = np.asarray(t["data"], dtype=np.float32)
        tsr = int(t.get("sr", sr))
        if tsr != sr and len(data):
            n = int(len(data) * sr / tsr)
            idx = np.linspace(0, len(data) - 1, max(1, n))
            data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        start = float(t.get("start", 0.0))
        norm.append((data, int(start * sr), float(t.get("volume", 1.0))))
        end = max(end, start + (len(data) / sr if sr else 0))

    length = int((total_duration or end) * sr) + sr
    out = np.zeros(length, dtype=np.float32)
    # simple ducking pass: compute a gain envelope from the trigger tracks
    for i, t in enumerate(tracks):
        duck_idx = t.get("duck_under")
        data, off, vol = norm[i]
        seg = data * vol
        if duck_idx is not None and 0 <= duck_idx < len(norm):
            trig = np.zeros(length, np.float32)
            tdata, toff, _ = norm[duck_idx]
            trig[toff:toff + len(tdata)] = np.abs(tdata)
            win = max(1, int(0.2 * sr))
            env = np.convolve(trig, np.ones(win) / win, mode="same")
            env = np.clip(env, 0, 1)
            duck = 1.0 - 0.7 * (env > 0.05)
            ducked = np.convolve(duck.astype(np.float32), np.ones(win) / win, mode="same")
            seg = seg * ducked[off:off + len(seg)] if off + len(seg) <= len(ducked) else seg
        out[off:off + len(seg)] += seg
    return np.clip(out, -1.0, 1.0), sr


# --------------------------------------------------------------------- DSP

def noise_reduce(data: np.ndarray, sr: int, strength: float = 0.7) -> np.ndarray:
    """Spectral-gate noise reduction (STFT magnitude thresholding)."""
    from scipy import signal

    if data.size == 0:
        return data
    mono = data if data.ndim == 1 else data.mean(axis=1)
    f, t, Z = signal.stft(mono, fs=sr, nperseg=1024)
    mag = np.abs(Z)
    # estimate the noise floor from the quietest 10% of frames
    frame_energy = mag.mean(axis=0)
    thresh_idx = max(1, int(len(frame_energy) * 0.1))
    noise = np.median(mag[:, np.argsort(frame_energy)[:thresh_idx]], axis=1, keepdims=True)
    gate = np.clip((mag - strength * noise) / np.maximum(mag, 1e-9), 0, 1)
    Zc = Z * gate
    _, clean = signal.istft(Zc, fs=sr)
    clean = clean[:len(mono)].astype(np.float32)
    if data.ndim == 2:
        return np.stack([clean, clean], axis=1)
    return clean


def equalize(data: np.ndarray, sr: int, bands: list[dict]) -> np.ndarray:
    """Parametric EQ. ``bands`` = [{freq, gain_db, q}]. Peaking biquads chain."""
    from scipy import signal

    out = np.asarray(data, dtype=np.float32)
    for band in bands or []:
        freq = float(band.get("freq", 1000))
        q = float(band.get("q", 1.0))
        gain_db = float(band.get("gain_db", 0.0))
        if abs(gain_db) < 1e-3:
            continue
        A = 10 ** (gain_db / 40.0)
        w0 = 2 * math.pi * freq / sr
        alpha = math.sin(w0) / (2 * q)
        b = [1 + alpha * A, -2 * math.cos(w0), 1 - alpha * A]
        a = [1 + alpha / A, -2 * math.cos(w0), 1 - alpha / A]
        b = [v / a[0] for v in b]
        a = [v / a[0] for v in a]
        if out.ndim == 1:
            out = signal.lfilter(b, a, out).astype(np.float32)
        else:
            out = np.stack([signal.lfilter(b, a, out[:, c]) for c in range(out.shape[1])], axis=1).astype(np.float32)
    return out


def compress(data: np.ndarray, *, threshold_db: float = -18.0, ratio: float = 3.0,
             makeup_db: float = 6.0) -> np.ndarray:
    """Simple feed-forward dynamics compressor."""
    out = np.asarray(data, dtype=np.float32)
    thr = 10 ** (threshold_db / 20.0)
    env = np.abs(out)
    over = env > thr
    gain = np.ones_like(out)
    gain[over] = (thr + (env[over] - thr) / ratio) / np.maximum(env[over], 1e-9)
    out = out * gain
    return apply_gain(out, makeup_db)


def detect_beats(data: np.ndarray, sr: int) -> list[float]:
    """Onset/beat detection via spectral-flux energy peaks -> times (seconds)."""
    mono = data if data.ndim == 1 else data.mean(axis=1)
    if mono.size == 0:
        return []
    hop = 512
    win = 1024
    frames = 1 + (len(mono) - win) // hop
    energy = np.empty(max(0, frames), dtype=np.float32)
    for i in range(frames):
        seg = mono[i * hop:i * hop + win]
        energy[i] = np.sqrt(np.mean(seg ** 2))
    if energy.size == 0:
        return []
    diff = np.diff(energy, prepend=energy[0])
    thresh = diff.mean() + diff.std()
    beats = []
    last = -1
    for i, d in enumerate(diff):
        if d > thresh and (i - last) > 3:
            beats.append(round(i * hop / sr, 3))
            last = i
    return beats


def peaks(data: np.ndarray, buckets: int = 800) -> list[float]:
    mono = data if data.ndim == 1 else data.mean(axis=1)
    if mono.size == 0:
        return []
    idx = np.linspace(0, len(mono), buckets + 1).astype(int)
    out = [float(np.max(np.abs(mono[idx[i]:max(idx[i] + 1, idx[i + 1])]))) for i in range(buckets)]
    m = max(out) or 1.0
    return [round(v / m, 4) for v in out]
