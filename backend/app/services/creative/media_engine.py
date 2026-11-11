"""Video & audio engine built on PyAV (a bundled FFmpeg binding).

The environment has no system ``ffmpeg`` binary, so every media operation here
uses PyAV directly: probing, frame extraction, trimming, concatenation, scaling,
speed changes, overlays, chroma keying, text burn-in, image→video (Ken Burns),
slideshows, audio extraction and audio muxing. All outputs are real, playable
files; nothing is stubbed.
"""
from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np

from app.services.creative import CreativeEngineError

try:  # PyAV is the rendering engine; report honestly if it is missing.
    import av

    _AV_OK = True
except Exception:  # pragma: no cover - dependency presence
    av = None  # type: ignore
    _AV_OK = False


def _require_av() -> None:
    if not _AV_OK:
        raise CreativeEngineError(
            "PyAV is not installed; video/audio processing is unavailable.",
            "CODEC_UNAVAILABLE",
        )


VIDEO_CODECS = ("libx264", "libvpx-vp9", "mpeg4")


def _container_format(dest: str | Path, codec: str) -> str:
    ext = Path(dest).suffix.lower()
    if ext == ".webm" or codec == "libvpx-vp9":
        return "webm"
    if ext == ".mkv":
        return "matroska"
    if ext == ".gif":
        return "gif"
    return "mp4"


def _pick_codec(preferred: str | None) -> str:
    _require_av()
    for codec in ([preferred] if preferred else []) + list(VIDEO_CODECS):
        if codec is None:
            continue
        try:
            av.codec.Codec(codec, "w")
            return codec
        except Exception:
            continue
    raise CreativeEngineError("No usable H.264/VP9 encoder is available.",
                              "CODEC_UNAVAILABLE")


# ------------------------------------------------------------------ probing

def probe(path: str | Path) -> dict:
    """Return media metadata: duration, size, fps, codecs, has_audio."""
    _require_av()
    path = str(path)
    info: dict = {"path": path, "duration": 0.0, "width": None, "height": None,
                  "fps": None, "video_codec": None, "audio_codec": None,
                  "has_video": False, "has_audio": False}
    with av.open(path) as c:
        if c.duration:
            info["duration"] = round(c.duration / av.time_base, 3)
        if c.streams.video:
            v = c.streams.video[0]
            info["has_video"] = True
            info["width"] = v.codec_context.width
            info["height"] = v.codec_context.height
            if v.average_rate:
                info["fps"] = round(float(v.average_rate), 3)
            info["video_codec"] = v.codec_context.name
            if not info["duration"] and v.duration and v.time_base:
                info["duration"] = round(float(v.duration * v.time_base), 3)
        if c.streams.audio:
            a = c.streams.audio[0]
            info["has_audio"] = True
            info["audio_codec"] = a.codec_context.name
            if not info["duration"] and c.duration:
                info["duration"] = round(c.duration / av.time_base, 3)
    return info


def is_video(path: str | Path) -> bool:
    try:
        return probe(path)["has_video"]
    except Exception:
        return False


# -------------------------------------------------------------- frame utils

def _fit(img, size: tuple[int, int], mode: str = "contain"):
    """Resize a PIL image to ``size`` using contain (letterbox) or cover (crop)."""
    from PIL import Image

    tw, th = size
    w, h = img.size
    if w == 0 or h == 0:
        return Image.new("RGB", size, (0, 0, 0))
    if mode == "cover":
        scale = max(tw / w, th / h)
    else:
        scale = min(tw / w, th / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (0, 0, 0))
    canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2))
    return canvas


def iter_video_frames(path: str | Path, *, start: float = 0.0, end: float | None = None,
                      size: tuple[int, int] | None = None,
                      fit: str = "contain") -> Iterator[tuple[float, object]]:
    """Yield ``(timestamp_seconds, PIL.Image)`` for a source, honouring a range."""
    _require_av()
    with av.open(str(path)) as c:
        if not c.streams.video:
            raise CreativeEngineError(f"{path} has no video stream.", "NO_VIDEO_STREAM")
        stream = c.streams.video[0]
        if start > 0 and stream.time_base:
            c.seek(int(start / stream.time_base), stream=stream)
        tb = float(stream.time_base) if stream.time_base else 1.0
        for frame in c.decode(video=0):
            t = (frame.pts or 0) * tb
            if t < start - 1e-3:
                continue
            if end is not None and t > end + 1e-3:
                break
            img = frame.to_image()
            if size:
                img = _fit(img, size, fit)
            yield t, img


def extract_frame(path: str | Path, t: float = 0.0, size: tuple[int, int] | None = None):
    """Return a single PIL frame at (or just after) time ``t``."""
    _require_av()
    with av.open(str(path)) as c:
        if not c.streams.video:
            raise CreativeEngineError(f"{path} has no video stream.", "NO_VIDEO_STREAM")
        stream = c.streams.video[0]
        tb = float(stream.time_base) if stream.time_base else 1.0
        if t > 0 and stream.time_base:
            c.seek(int(t / stream.time_base), stream=stream)
        last = None
        for frame in c.decode(video=0):
            last = frame.to_image()
            if (frame.pts or 0) * tb >= t:
                break
        if last is None:
            raise CreativeEngineError("Could not decode a frame.", "DECODE_FAILED")
        if size:
            last = _fit(last, size, "contain")
        return last


def make_thumbnail(path: str | Path, dest: str | Path, *,
                   at: float = 1.0, size: tuple[int, int] = (480, 480)) -> Path:
    img = extract_frame(path, at)
    img.thumbnail(size)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img.save(dest, "JPEG", quality=85)
    return dest


# ------------------------------------------------------------- audio encode

def extract_audio(path: str | Path, dest_wav: str | Path, *, sample_rate: int = 44100) -> Path:
    """Decode a file's audio track to a mono/stereo WAV via soundfile."""
    _require_av()
    import soundfile as sf

    dest_wav = Path(dest_wav)
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[np.ndarray] = []
    rate = sample_rate
    with av.open(str(path)) as c:
        if not c.streams.audio:
            raise CreativeEngineError(f"{path} has no audio stream.", "NO_AUDIO_STREAM")
        for frame in c.decode(audio=0):
            arr = frame.to_ndarray()
            # PyAV returns (channels, samples) for planar, (samples,) otherwise
            if arr.ndim == 2:
                arr = arr.T
            chunks.append(arr)
            rate = frame.sample_rate
    if not chunks:
        raise CreativeEngineError("No audio samples decoded.", "DECODE_FAILED")
    data = np.concatenate(chunks, axis=0).astype(np.float32)
    if data.ndim == 2 and data.shape[1] > 1:
        data = data.mean(axis=1)  # downmix to mono for editing simplicity
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        data = data / peak
    sf.write(str(dest_wav), data, rate)
    return dest_wav


def waveform_peaks(path: str | Path, buckets: int = 800) -> list[float]:
    """Return normalised peak amplitude per bucket (for timeline waveforms)."""
    _require_av()
    values: list[float] = []
    with av.open(str(path)) as c:
        if not c.streams.audio:
            return []
        for frame in c.decode(audio=0):
            arr = frame.to_ndarray()
            if arr.ndim == 2:
                arr = arr.mean(axis=0)
            values.append(float(np.max(np.abs(arr))))
    if not values:
        return []
    arr = np.asarray(values, dtype=np.float32)
    idx = np.linspace(0, len(arr), buckets + 1).astype(int)
    peaks = [float(arr[idx[i]:max(idx[i] + 1, idx[i + 1])].max()) for i in range(buckets)]
    m = max(peaks) or 1.0
    return [round(p / m, 4) for p in peaks]


# -------------------------------------------------------------- video encode

class VideoWriter:
    """Encode an iterable of PIL frames to H.264/VP9, optionally muxing audio."""

    def __init__(self, dest: str | Path, size: tuple[int, int], fps: float = 30.0,
                 codec: str | None = None, crf: int = 23, audio: str | Path | None = None):
        self.dest = Path(dest)
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        self.size = size
        self.fps = float(fps or 30.0)
        self.codec = _pick_codec(codec)
        self.crf = int(crf)
        self.audio = Path(audio) if audio else None
        self._tmp_video = str(self.dest) if not self.audio else str(
            self.dest.with_suffix(".videoonly" + self.dest.suffix))

    def write(self, frames: Iterable) -> Path:
        from fractions import Fraction

        from PIL import Image

        out = av.open(self._tmp_video, mode="w", format=_container_format(self.dest, self.codec))
        stream = out.add_stream(self.codec, rate=Fraction(self.fps).limit_denominator(1000000))
        stream.width, stream.height = self.size
        stream.pix_fmt = "yuv420p"
        opts = {"crf": str(self.crf)}
        if self.codec == "libvpx-vp9":
            opts = {"crf": str(min(63, self.crf + 10)), "b": "0"}
        stream.options = opts
        count = 0
        for img in frames:
            if img is None:
                continue
            if not isinstance(img, Image.Image):
                img = Image.fromarray(np.asarray(img, dtype=np.uint8))
            if img.mode != "RGB":
                img = img.convert("RGB")
            if img.size != self.size:
                img = _fit(img, self.size, "contain")
            for pkt in stream.encode(av.VideoFrame.from_image(img)):
                out.mux(pkt)
            count += 1
        for pkt in stream.encode():
            out.mux(pkt)
        out.close()
        if count == 0:
            raise CreativeEngineError("No frames were produced for encoding.", "EMPTY_RENDER")
        if self.audio:
            _mux_audio(self._tmp_video, str(self.audio), str(self.dest))
            try:
                os.remove(self._tmp_video)
            except OSError:
                pass
        return self.dest


def _mux_audio(video_in: str, audio_in: str | None, dest: str) -> None:
    _require_av()
    iv = av.open(video_in)
    ia = av.open(audio_in) if audio_in else None
    out = av.open(dest, "w", format=_container_format(dest, "libx264"))
    if not iv.streams.video:
        raise CreativeEngineError("No video stream to mux.", "NO_VIDEO_STREAM")
    vs = out.add_stream_from_template(iv.streams.video[0])
    asr = None
    if ia is not None and ia.streams.audio:
        asr = out.add_stream_from_template(ia.streams.audio[0])
    for pkt in iv.demux(iv.streams.video[0]):
        if pkt.dts is None:
            continue
        pkt.stream = vs
        out.mux(pkt)
    if asr is not None:
        for pkt in ia.demux(ia.streams.audio[0]):
            if pkt.dts is None:
                continue
            pkt.stream = asr
            out.mux(pkt)
    out.close()
    iv.close()
    if ia is not None:
        ia.close()


# --------------------------------------------------------------- operations

def image_to_video(image_path: str | Path, dest: str | Path, *, duration: float = 5.0,
                   fps: float = 24.0, size: tuple[int, int] = (1280, 720),
                   ken_burns: bool = True, zoom_from: float = 1.0, zoom_to: float = 1.15,
                   audio: str | Path | None = None, crf: int = 21,
                   pan: str = "auto", motion_strength: float = 1.0) -> Path:
    """Animate a still image into a video (Ken Burns pan/zoom + subtle rotation).

    Improvements over the basic pan/zoom:
    - 24 fps cinematic default + CRF 21 (better quality than the old 30 fps / 23 crf).
    - Ease-in-out (cosine) motion instead of linear, for natural acceleration.
    - pan="auto" cycles pan direction deterministically per image; "center" still honored.
    - motion_strength scales zoom/rotate amplitude (0 = static frame, 1 = full motion).
    - A few degrees of rocking rotation gives dynamic parallax on stills.
    """
    import math
    from PIL import Image

    base = Image.open(image_path)
    base = base.convert("RGB")
    strength = max(0.0, float(motion_strength))
    eff_zoom_to = 1.0 + (zoom_to - 1.0) * strength
    eff_zoom_from = 1.0 + (zoom_from - 1.0) * strength
    # upscale source so zooming never samples beyond the frame
    big = _fit(base, (int(size[0] * max(eff_zoom_to, 1.0) * 1.3),
                      int(size[1] * max(eff_zoom_to, 1.0) * 1.3)), "contain")
    total = max(1, int(round(duration * fps)))

    pan_dirs = ["left", "right", "center"]
    pan_dir = pan if pan in pan_dirs else pan_dirs[int(big.width * 0.618) % 3]
    max_rotate = math.radians(2.0) * strength
    bg = tuple(int(c) for c in base.getpixel((0, 0)))

    def frames():
        for i in range(total):
            t = i / max(1, total - 1)
            # ease-in-out (cosine) for smooth acceleration / deceleration
            e = 0.5 - 0.5 * math.cos(math.pi * t)
            z = eff_zoom_from + (eff_zoom_to - eff_zoom_from) * e if ken_burns else 1.0
            cw, ch = int(size[0] * z), int(size[1] * z)
            if pan_dir == "left":
                ox = int((big.width - cw) * (1 - e))
            elif pan_dir == "right":
                ox = int((big.width - cw) * e)
            else:
                ox = (big.width - cw) // 2
            oy = (big.height - ch) // 2
            crop = big.crop((max(0, ox), max(0, oy), max(0, ox) + cw, max(0, oy) + ch))
            crop = _fit(crop, size, "cover")
            if max_rotate > 0:
                # rocking rotation, easing toward +/-max_rotate mid-way through each half
                angle = math.degrees(max_rotate) * (0.5 - abs(0.5 - t)) * 2.0
                crop = crop.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=bg)
            yield crop

    return VideoWriter(dest, size, fps, crf=crf, audio=audio).write(frames())


def slideshow(images: list, dest: str | Path, *, per_image: float = 2.5,
              fps: float = 30.0, size: tuple[int, int] = (1280, 720),
              transition: float = 0.0, audio: str | Path | None = None,
              crf: int = 23) -> Path:
    """Build a slideshow video from a list of image paths with optional fades."""
    from PIL import Image

    loaded = [_fit(Image.open(p).convert("RGB"), size, "cover") for p in images]
    if not loaded:
        raise CreativeEngineError("No images supplied for the slideshow.", "EMPTY_INPUT")
    per_frames = max(1, int(round(per_image * fps)))
    trans_frames = max(0, int(round(transition * fps)))

    def frames():
        for idx, img in enumerate(loaded):
            for _ in range(per_frames):
                yield img
            nxt = loaded[idx + 1] if idx + 1 < len(loaded) else None
            if nxt is not None and trans_frames:
                for k in range(trans_frames):
                    yield Image.blend(img, nxt, (k + 1) / (trans_frames + 1))

    return VideoWriter(dest, size, fps, crf=crf, audio=audio).write(frames())


def trim(src: str | Path, dest: str | Path, *, start: float, end: float,
         size: tuple[int, int] | None = None, fps: float | None = None,
         crf: int = 23, keep_audio: bool = True) -> Path:
    """Trim a clip to [start, end] (re-encode for frame accuracy)."""
    info = probe(src)
    if size is None and info["width"]:
        size = (info["width"], info["height"])
    if fps is None:
        fps = info["fps"] or 30.0
    audio = None
    if keep_audio and info["has_audio"]:
        audio = str(Path(dest).with_suffix(".wav"))
        extract_audio_range(src, audio, start, end)
    frames = (img for _, img in iter_video_frames(src, start=start, end=end, size=size))
    return VideoWriter(dest, size, fps, crf=crf, audio=audio).write(frames())


def extract_audio_range(src: str | Path, dest_wav: str | Path, start: float, end: float,
                        sample_rate: int = 44100) -> Path:
    import soundfile as sf

    chunks: list[np.ndarray] = []
    rate = sample_rate
    with av.open(str(src)) as c:
        if not c.streams.audio:
            raise CreativeEngineError("No audio stream.", "NO_AUDIO_STREAM")
        stream = c.streams.audio[0]
        tb = float(stream.time_base) if stream.time_base else 1.0
        if start > 0 and stream.time_base:
            c.seek(int(start / stream.time_base), stream=stream)
        for frame in c.decode(audio=0):
            t = (frame.pts or 0) * tb
            if t < start - 0.05:
                continue
            if t > end + 0.05:
                break
            arr = frame.to_ndarray()
            if arr.ndim == 2:
                arr = arr.T
            chunks.append(arr)
            rate = frame.sample_rate
    data = np.concatenate(chunks, axis=0).astype(np.float32) if chunks else np.zeros(1, np.float32)
    if data.ndim == 2 and data.shape[1] > 1:
        data = data.mean(axis=1)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        data = data / peak
    Path(dest_wav).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dest_wav), data, rate)
    return Path(dest_wav)


def concat(parts: list, dest: str | Path, *, size: tuple[int, int] = (1280, 720),
           fps: float = 30.0, crf: int = 23, audio: str | Path | None = None) -> Path:
    """Concatenate clips (each scaled to ``size``) into one file."""
    def frames():
        for part in parts:
            for _, img in iter_video_frames(part, size=size):
                yield img

    return VideoWriter(dest, size, fps, crf=crf, audio=audio).write(frames())


def change_speed(src: str | Path, dest: str | Path, factor: float, *,
                 size: tuple[int, int] | None = None, fps: float | None = None,
                 crf: int = 23) -> Path:
    """Change playback speed (factor>1 faster, <1 slower) by frame resampling."""
    factor = max(0.05, min(20.0, float(factor)))
    info = probe(src)
    if size is None and info["width"]:
        size = (info["width"], info["height"])
    out_fps = fps or info["fps"] or 30.0

    def frames():
        src_iter = iter_video_frames(src, size=size)
        idx = 0
        # target source index advances by `factor` each output frame
        produced = 0
        last = None
        cache: dict[int, object] = {}
        next_needed = 0
        for t, img in src_iter:
            cache[idx] = img
            while next_needed in cache:
                yield cache.pop(next_needed)
                produced += 1
                next_needed = round(produced * factor)
                if next_needed < produced - 1:
                    next_needed = produced  # guard against <1 factors
            idx += 1
            last = img
        # pad with the last frame if the mapping ran out
        while len(cache) == 0 and last is not None and produced < idx / factor:
            yield last
            produced += 1

    return VideoWriter(dest, size, out_fps, crf=crf).write(frames())


def overlay(base: str | Path, overlay_path: str | Path, dest: str | Path, *,
            position: str = "bottom_right", scale: float = 0.3,
            opacity: float = 1.0, size: tuple[int, int] | None = None,
            fps: float | None = None, crf: int = 23) -> Path:
    """Picture-in-picture / watermark: overlay one clip on another."""
    from PIL import Image

    base_info = probe(base)
    size = size or (base_info["width"], base_info["height"])
    fps = fps or base_info["fps"] or 30.0
    ow, oh = int(size[0] * scale), int(size[1] * scale)
    margin = int(size[0] * 0.02)

    def frames():
        ov_iter = iter_video_frames(overlay_path, size=(ow, oh), fit="cover")
        for _, bimg in iter_video_frames(base, size=size):
            try:
                oimg = next(ov_iter)
            except StopIteration:
                oimg = None
            if oimg is not None:
                oimg = oimg.convert("RGBA")
                if opacity < 1.0:
                    a = oimg.split()[-1].point(lambda v: int(v * opacity))
                    oimg.putalpha(a)
                bimg = bimg.convert("RGBA")
                x, y = _position_xy(position, size, (ow, oh), margin)
                bimg.alpha_composite(oimg, (x, y))
            yield bimg

    return VideoWriter(dest, size, fps, crf=crf).write(frames())


def _position_xy(position: str, size, box, margin) -> tuple[int, int]:
    bw, bh = size
    ow, oh = box
    return {
        "top_left": (margin, margin),
        "top_right": (bw - ow - margin, margin),
        "bottom_left": (margin, bh - oh - margin),
        "bottom_right": (bw - ow - margin, bh - oh - margin),
        "center": ((bw - ow) // 2, (bh - oh) // 2),
    }.get(position, (bw - ow - margin, bh - oh - margin))


def burn_text(src: str | Path, dest: str | Path, texts: list[dict], *,
              size: tuple[int, int] | None = None, fps: float | None = None,
              crf: int = 23) -> Path:
    """Burn styled text/captions onto every frame of a clip."""
    from app.services.creative import image_engine

    info = probe(src)
    size = size or (info["width"], info["height"])
    fps = fps or info["fps"] or 30.0

    def frames():
        for _, img in iter_video_frames(src, size=size):
            yield image_engine.draw_text(img.convert("RGBA"), {"texts": texts}).convert("RGB")

    return VideoWriter(dest, size, fps, crf=crf).write(frames())


def chroma_key(src: str | Path, dest: str | Path, *, key_color: str = "#00ff00",
               similarity: float = 0.3, smoothness: float = 0.1,
               background: str | None = None, size: tuple[int, int] | None = None,
               fps: float | None = None, crf: int = 23) -> Path:
    """Remove a key colour from each frame (green screen) and composite a bg."""
    from PIL import Image

    info = probe(src)
    size = size or (info["width"], info["height"])
    fps = fps or info["fps"] or 30.0
    key = _hex_rgb(key_color)
    bg_img = None
    if background:
        if Path(background).is_file():
            bg_img = _fit(Image.open(background).convert("RGB"), size, "cover")
        else:
            bg_img = Image.new("RGB", size, _hex_rgb(background))

    def frames():
        for _, img in iter_video_frames(src, size=size):
            rgba = img.convert("RGBA")
            arr = np.asarray(rgba, dtype=np.float32)
            d = np.sqrt(((arr[..., :3] - np.array(key)) ** 2).sum(-1)) / 441.67
            alpha = np.clip((d - similarity) / max(1e-3, smoothness), 0, 1) * 255
            arr[..., 3] = np.where(d < similarity, 0, alpha)
            keyed = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")
            if bg_img is not None:
                base = bg_img.convert("RGBA")
                base.alpha_composite(keyed)
                yield base.convert("RGB")
            else:
                yield keyed

    return VideoWriter(dest, size, fps, crf=crf).write(frames())


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore
