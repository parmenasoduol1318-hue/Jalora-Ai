"""Timeline -> file renderer for the Creative Studio.

Composes a project's multitrack timeline (video, image, text and audio layers)
into a real encoded output using PyAV for video and the audio engine for the
soundtrack. No system ``ffmpeg`` binary is required.

Supported render kinds: ``video`` (full timeline), ``image`` (still frame at a
given time), ``audio`` (mixed soundtrack only) and ``subtitle`` (transcript as
SRT burned into no video, returned as text asset).
"""
from __future__ import annotations

import logging
import math
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.services.creative import CreativeEngineError, audio_engine, image_engine, media_engine, storage

logger = logging.getLogger("jalora.creative.render")

_RATIO_SIZES = {
    "16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1080, 1080),
    "4:3": (1024, 768), "3:4": (768, 1024), "3:2": (1280, 854), "2:3": (854, 1280),
}
_RES_PRESETS = {"720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160}


def render_project(db: Session, *, user_id: str, project_id: str | None,
                   render_job_id: str, settings: Settings, progress,
                   kind: str = "video", options: dict | None = None) -> Path:
    from app.models.creative import CreativeProject, MediaAsset

    options = options or {}
    if project_id is None:
        raise CreativeEngineError("Render requires a project.", "INVALID_INPUT")
    project = db.get(CreativeProject, project_id)
    if project is None or project.user_id != user_id:
        raise CreativeEngineError("Project not found.", "NOT_FOUND")

    layers = _collect_layers(db, project)
    fps = float(options.get("fps", 30))
    size = _resolve_size(project.aspect_ratio, options, project)
    duration = max([ly["start"] + ly["duration"] for ly in layers], default=0.0)
    duration = min(duration, float(settings.creative_max_render_seconds))
    if duration <= 0:
        raise CreativeEngineError("Timeline is empty; add clips before rendering.", "EMPTY_TIMELINE")
    progress(0.02, "preparing")

    renders_dir = storage.creative_dir(user_id) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)

    if kind == "subtitle":
        return _render_subtitles(db, project, user_id, renders_dir)
    if kind == "image":
        return _render_still(layers, size, float(options.get("time", 0.0)),
                             renders_dir / f"{render_job_id}.png", settings)
    if kind == "audio":
        return _render_audio(layers, duration, renders_dir / f"{render_job_id}.wav",
                             settings, progress)

    audio_path = _render_audio(layers, duration, renders_dir / f"{render_job_id}_mix.wav",
                               settings, progress, report_range=(0.05, 0.30))
    dest = renders_dir / f"{render_job_id}.mp4"
    _render_video(layers, size, fps, duration, dest, audio_path, settings, progress,
                  report_range=(0.30, 1.0))
    return dest


# --------------------------------------------------------------- layers

def _collect_layers(db: Session, project) -> list[dict]:
    from app.models.creative import MediaAsset

    layers: list[dict] = []
    for track in project.tracks:
        if track.is_hidden and track.track_type in ("video", "image"):
            continue
        for clip in track.clips:
            asset = db.get(MediaAsset, clip.asset_id) if clip.asset_id else None
            path = None
            if asset is not None:
                try:
                    path = storage.resolve(asset.user_id, asset.file_path)
                except Exception:
                    path = None
            layers.append({
                "type": clip.clip_type,
                "track_type": track.track_type,
                "path": path,
                "start": float(clip.start_time),
                "duration": float(clip.duration),
                "trim_start": float(clip.trim_start),
                "speed": float(clip.speed or 1.0),
                "opacity": float(clip.opacity if clip.opacity is not None else 1.0),
                "z": int(clip.z_index or 0),
                "track_index": int(track.track_index or 0),
                "muted": bool(track.is_muted) or bool((clip.effects_json or {}).get("mute")),
                "volume": float(track.volume or 1.0) * float((clip.effects_json or {}).get("volume", 1.0)),
                "transform": clip.transform_json or {},
                "effects": clip.effects_json or {},
                "text": clip.text_json or {},
                "clip": clip,
            })
    layers.sort(key=lambda ly: (ly["z"], ly["track_index"]))
    return layers


def _resolve_size(aspect: str, options: dict, project) -> tuple[int, int]:
    if options.get("width") and options.get("height"):
        return int(options["width"]), int(options["height"])
    base = _RATIO_SIZES.get(aspect, (1280, 720))
    preset = options.get("resolution")
    if preset in _RES_PRESETS:
        height = _RES_PRESETS[preset]
        width = int(round(height * base[0] / base[1]))
        if width % 2:
            width += 1
        height = height + (height % 2)
        return width, height
    return base


# --------------------------------------------------------------- video

def _render_video(layers, size, fps, duration, dest, audio_path, settings, progress, *, report_range):
    writer = media_engine.VideoWriter(dest, size, fps, audio=audio_path)
    readers: dict[int, "_ClipReader"] = {}
    image_cache: dict[str, object] = {}
    total = max(1, int(math.ceil(duration * fps)))
    lo, hi = report_range

    def frames():
        try:
            for i in range(total):
                t = i / fps
                canvas = image_engine.open_image(_black(size))
                for layer in layers:
                    if layer["track_type"] not in ("video", "image") and layer["type"] not in ("image", "video", "media"):
                        continue
                    if not (layer["start"] <= t < layer["start"] + layer["duration"]):
                        continue
                    if not layer["path"]:
                        continue
                    img = _clip_frame(layer, t, readers, image_cache, size)
                    if img is None:
                        continue
                    img = _apply_clip_look(img, layer)
                    img = media_engine._fit(img, size, layer["transform"].get("fit", "cover"))
                    if layer["opacity"] < 1.0:
                        alpha = img.convert("RGBA")
                        a = alpha.getchannel("A").point(lambda v: int(v * layer["opacity"]))
                        alpha.putalpha(a)
                        img = alpha
                    canvas = image_engine.composite(canvas, img, mode=layer["transform"].get("blend", "normal"))
                for layer in layers:
                    if layer["type"] != "text" and layer["track_type"] != "text":
                        continue
                    if layer["start"] <= t < layer["start"] + layer["duration"]:
                        canvas = image_engine.draw_text(canvas, layer["text"])
                if total:
                    progress(lo + (hi - lo) * (i / total), f"frame {i + 1}/{total}")
                yield canvas
        finally:
            for reader in readers.values():
                reader.close()

    writer.write(frames())
    return dest


def _clip_frame(layer, t, readers, image_cache, size):
    if layer["track_type"] == "image" or layer["type"] == "image":
        key = str(layer["path"])
        if key not in image_cache:
            image_cache[key] = image_engine.open_image(layer["path"])
        base = image_cache[key]
        # subtle Ken Burns via transform scale keyframes optional
        return base.copy()
    src_time = layer["trim_start"] + (t - layer["start"]) * layer["speed"]
    key = id(layer)
    reader = readers.get(key)
    if reader is None:
        reader = _ClipReader(layer["path"])
        readers[key] = reader
    return reader.frame_at(src_time)


def _apply_clip_look(img, layer):
    effects = layer["effects"]
    if effects.get("filter"):
        try:
            img = image_engine.apply_filter(img, effects["filter"])
        except Exception:
            pass
    params = effects.get("adjust") or {}
    if params:
        img = image_engine.apply_adjustments(img, params)
    transform = layer["transform"]
    if transform.get("scale") or transform.get("rotation") or transform.get("flip"):
        img = image_engine.transform(img, {
            "scale": transform.get("scale", 1.0),
            "degrees": transform.get("rotation", 0),
            "flip": transform.get("flip"),
        })
    return img


class _ClipReader:
    """Sequential frame reader with backward seek for a single video asset."""

    def __init__(self, path: Path):
        import av

        self.container = av.open(str(path))
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        self._current_t = -1.0
        self._generator = self.container.decode(self.stream)

    def frame_at(self, t: float):
        import av

        if t < self._current_t - 0.001:
            self.container.seek(int(t * av.time_base), backward=True, any_frame=False)
            self._generator = self.container.decode(self.stream)
        for frame in self._generator:
            ft = float(frame.pts * self.stream.time_base) if frame.pts is not None else (self._current_t + 0.033)
            self._current_t = ft
            if ft + 1e-3 >= t:
                return frame.to_image()
        return None

    def close(self):
        try:
            self.container.close()
        except Exception:
            pass


def _black(size):
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", size, (0, 0, 0, 255)).save(buf, "PNG")
    return buf


# --------------------------------------------------------------- audio

def _render_audio(layers, duration, dest, settings, progress, report_range=(0.0, 1.0)) -> Path:
    tracks = []
    for layer in layers:
        if layer["muted"] or layer["volume"] <= 0:
            continue
        if not layer["path"] or layer["path"].suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        trimmed = _layer_audio_clip(layer)
        if trimmed is None:
            continue
        data, sr = trimmed
        tracks.append({"data": data, "sr": sr, "start": layer["start"], "volume": layer["volume"]})
    lo, hi = report_range
    if not tracks:
        return _silent_wav(duration, dest)
    mixed, sr = audio_engine.mix(tracks, total_duration=duration)
    audio_engine.save_audio(dest, mixed, sr)
    progress(hi, "audio mixed")
    return dest


def _layer_audio_clip(layer):
    try:
        if layer["path"].suffix.lower() in (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"):
            tmp = Path(tempfile.mkdtemp()) / "a.wav"
            media_engine.extract_audio(layer["path"], tmp)
            data, sr = audio_engine.load_audio(tmp)
        else:
            data, sr = audio_engine.load_audio(layer["path"])
    except Exception:
        return None
    start = layer["trim_start"]
    end = start + layer["duration"] * layer["speed"]
    data = audio_engine.trim(data, sr, start, end)
    if layer["speed"] != 1.0:
        data = audio_engine.change_speed(data, layer["speed"])
    return data, sr


def _silent_wav(duration, dest):
    import numpy as np
    n = max(1, int(duration * 44100))
    return audio_engine.save_audio(dest, np.zeros(n, np.float32), 44100)


# --------------------------------------------------------------- stills/subtitles

def _render_still(layers, size, t, dest, settings) -> Path:
    readers: dict[int, _ClipReader] = {}
    cache: dict[str, object] = {}
    try:
        canvas = image_engine.open_image(_black(size))
        for layer in layers:
            if layer["track_type"] not in ("video", "image") and layer["type"] not in ("image", "video", "media"):
                continue
            if layer["start"] <= t < layer["start"] + layer["duration"] and layer["path"]:
                img = _clip_frame(layer, t, readers, cache, size)
                if img is not None:
                    img = _apply_clip_look(img, layer)
                    img = media_engine._fit(img, size, layer["transform"].get("fit", "cover"))
                    canvas = image_engine.composite(canvas, img)
        for layer in layers:
            if layer["type"] == "text" or layer["track_type"] == "text":
                if layer["start"] <= t < layer["start"] + layer["duration"]:
                    canvas = image_engine.draw_text(canvas, layer["text"])
        return image_engine.export(canvas, dest, fmt="png")
    finally:
        for reader in readers.values():
            reader.close()


def _render_subtitles(db, project, user_id, renders_dir) -> Path:
    from app.services.creative import transcribe

    segments = [{"start": s.start_time, "end": s.end_time, "text": s.text}
                for s in project.transcripts]
    dest = renders_dir / f"{project.id}.srt"
    dest.write_text(transcribe.to_srt(segments), encoding="utf-8")
    return dest
