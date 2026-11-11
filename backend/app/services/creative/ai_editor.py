"""AI auto-editor: natural-language -> a *validated* edit plan -> real output.

Two planners feed one strict schema:

* an LLM planner (via the shared :class:`ModelRouter`) that must return a JSON
  op list which is validated against :data:`OP_SPECS`; and
* a deterministic heuristic planner used when no model is available.

Plans are only ever executed through :func:`execute_plan`, which applies ops with
the real image/audio/media engines and writes an actual file. Unknown or
malformed ops are rejected (never silently ignored).
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
from pathlib import Path

from app.services.creative import CreativeEngineError

logger = logging.getLogger("jalora.creative.ai_editor")

IMAGE_OPS = {"adjust", "filter", "curves", "transform", "text", "background_remove"}
STRUCT_OPS = {"trim", "speed", "reverse"}
AUDIO_OPS = {"volume", "audio_fade", "normalize_audio", "denoise", "eq", "compress", "mute"}
GEN_OPS = {"voiceover", "music", "captions"}
TIMELINE_OPS = {"overlay_image", "transition", "broll", "scene_split"}

OP_SPECS: dict[str, dict] = {
    "adjust": {"keys": ["params"], "desc": "tone/colour: brightness, contrast, saturation, exposure, temperature, vignette, sharpen, blur, grayscale, sepia"},
    "filter": {"keys": ["name"], "enum": ["vivid", "cinematic_warm", "cyberpunk_neon", "noir", "vintage", "soft_portrait", "high_key", "low_key", "rec709"]},
    "curves": {"keys": ["points"], "desc": "]]x,y] control points 0-255"},
    "transform": {"keys": ["params"], "desc": "crop{x,y,width,height} | degrees | flip(horizontal|vertical) | resize{width,height} | scale"},
    "text": {"keys": ["texts", "shapes"], "desc": "texts:[{text,x,y,size,color,anchor,start,end}]"},
    "background_remove": {"keys": ["mode", "tolerance"], "enum": ["edge", "color", "rembg"]},
    "trim": {"keys": ["start", "end"]},
    "speed": {"keys": ["factor"]},
    "reverse": {"keys": []},
    "mute": {"keys": []},
    "volume": {"keys": ["db"]},
    "audio_fade": {"keys": ["in", "out"]},
    "normalize_audio": {"keys": []},
    "denoise": {"keys": ["strength"]},
    "eq": {"keys": ["bands"]},
    "compress": {"keys": ["threshold_db", "ratio", "makeup_db"]},
    "voiceover": {"keys": ["text", "voice"]},
    "music": {"keys": ["prompt", "duration"]},
    "captions": {"keys": ["language", "model", "style"], "desc": "transcribe + burn subtitles"},
    "overlay_image": {"keys": ["asset_id", "position", "scale", "opacity", "start", "end"]},
    "transition": {"keys": ["type", "duration"], "enum": ["fade", "cut", "crossfade"]},
    "broll": {"keys": ["prompt", "at", "duration"]},
    "scene_split": {"keys": ["threshold"]},
}


def validate_ops(ops: object) -> tuple[list[dict], list[str]]:
    """Return ``(valid_ops, errors)``. Rejects unknown ops and bad shapes."""
    errors: list[str] = []
    valid: list[dict] = []
    if not isinstance(ops, list):
        return [], ["ops must be a list"]
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or "op" not in op:
            errors.append(f"op #{i}: not an object with an 'op' field")
            continue
        name = op["op"]
        if name not in OP_SPECS:
            errors.append(f"op #{i}: unknown op '{name}'")
            continue
        spec = OP_SPECS[name]
        if "enum" in spec:
            value = op.get("name", op.get("mode", op.get("type")))
            if value is not None and value not in spec["enum"]:
                errors.append(f"op #{i} ({name}): '{value}' not in {spec['enum']}")
                continue
        valid.append(op)
    return valid, errors


# ------------------------------------------------------------------ planners

_SYSTEM = (
    "You are Jalora's video auto-editor. Convert a natural-language editing "
    "request into a JSON object: {\"ops\": [...], \"explanation\": \"...\"}. "
    "Each op is {\"op\": <name>, ...params}. Allowed ops and params: "
    + json.dumps({k: v.get("desc", k) for k, v in OP_SPECS.items()})
    + ". Only use the allowed ops. Respond with JSON only."
)


async def plan_edits(instruction: str, *, context: dict | None = None,
                     disabled=None, provider_override: str | None = None,
                     model_override: str | None = None) -> dict:
    """Plan edits via the shared model router; fall back to heuristics on error."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from app.services.model_router import get_model_router

        router = get_model_router()
        route = router.route(message=instruction, complexity="complex",
                             provider_override=provider_override,
                             model_override=model_override, disabled=disabled)
        model = router.get_model(route)
        user = instruction
        if context:
            user += f"\n\nContext: {json.dumps(context)[:1500]}"
        resp = await model.ainvoke([SystemMessage(content=_SYSTEM), HumanMessage(content=user)])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        plan = _extract_json(text)
        ops, errors = validate_ops(plan.get("ops"))
        if ops:
            return {"ops": ops, "planner": "llm", "errors": errors,
                    "explanation": plan.get("explanation", "")}
        logger.warning("LLM plan invalid (%s); using heuristic planner", errors)
    except Exception as exc:  # noqa: BLE001
        logger.info("AI edit planner unavailable (%s); using heuristic planner", exc)
    ops = heuristic_plan(instruction)
    return {"ops": ops, "planner": "heuristic", "errors": [],
            "explanation": "Heuristic plan (no model response validated)."}


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise CreativeEngineError("Planner did not return JSON.", "PLAN_PARSE")
    return json.loads(text[start:end + 1])


def heuristic_plan(instruction: str) -> list[dict]:
    """Deterministic keyword-driven edits; real ops, honestly non-AI."""
    low = instruction.lower()
    ops: list[dict] = []

    def num(pattern, default=None):
        m = re.search(pattern, low)
        return float(m.group(1)) if m else default

    trim = re.search(r"(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\s*(?:to|-|until)\s*(\d+(?:\.\d+)?)", low)
    if trim:
        ops.append({"op": "trim", "start": float(trim.group(1)), "end": float(trim.group(2))})
    if "slow motion" in low or "slow down" in low or "slowmo" in low:
        ops.append({"op": "speed", "factor": num(r"(\d+(?:\.\d+)?)\s*x", 0.5)})
    elif "speed up" in low or "faster" in low or "timelapse" in low:
        ops.append({"op": "speed", "factor": num(r"(\d+(?:\.\d+)?)\s*x", 2.0)})
    if "reverse" in low:
        ops.append({"op": "reverse"})
    if "black and white" in low or "noir" in low or "grayscale" in low:
        ops.append({"op": "adjust", "params": {"grayscale": True, "contrast": 0.15}})
    if "cinematic" in low:
        ops.append({"op": "filter", "name": "cinematic_warm"})
    if "vivid" in low or "vibrant" in low:
        ops.append({"op": "filter", "name": "vivid"})
    if "vintage" in low or "retro" in low:
        ops.append({"op": "filter", "name": "vintage"})
    bright = num(r"(?:brighten|brightness)[^-\d]*(\d+)")
    if "brighten" in low and bright is None:
        ops.append({"op": "adjust", "params": {"brightness": 0.08}})
    elif bright is not None:
        ops.append({"op": "adjust", "params": {"brightness": bright / 100.0}})
    sat = num(r"saturat\w*[^-\d]*(\d+)")
    if sat is not None:
        ops.append({"op": "adjust", "params": {"saturation": sat / 100.0}})
    if "mute" in low or "remove audio" in low or "silent" in low:
        ops.append({"op": "mute"})
    vol = num(r"volume[^-\d]*(-?\d+)")
    if vol is not None:
        ops.append({"op": "volume", "db": vol})
    if "fade in" in low or "fade out" in low:
        ops.append({"op": "audio_fade", "in": 1.0 if "fade in" in low else 0.0,
                    "out": 1.0 if "fade out" in low else 0.0})
    if "caption" in low or "subtitle" in low:
        ops.append({"op": "captions"})
    if "crop" in low:
        ops.append({"op": "transform", "params": {"scale": 0.9}})
    if not ops:
        ops.append({"op": "adjust", "params": {"contrast": 0.05}})
    return ops


# ------------------------------------------------------------- execution

def execute_plan(media_path: str | Path, ops: list[dict], dest: str | Path, *,
                 is_image: bool, resolve_asset=None, progress=None,
                 settings=None, size: tuple[int, int] = (1280, 720),
                 fps: float = 30.0) -> dict:
    """Apply a validated plan to a single media file, writing ``dest``."""
    from app.config import get_settings

    settings = settings or get_settings()
    media_path, dest = Path(media_path), Path(dest)
    valid, errors = validate_ops(ops)
    if errors:
        raise CreativeEngineError("; ".join(errors), "INVALID_PLAN")

    applied: list[str] = []
    if is_image:
        dest = _execute_image(media_path, valid, dest)
        applied = [op["op"] for op in valid if op["op"] in IMAGE_OPS]
        if progress:
            progress(1.0)
        return {"output": str(dest), "applied": applied, "skipped": [op["op"] for op in valid if op["op"] not in IMAGE_OPS]}

    tmp = Path(tempfile.mkdtemp(prefix="jalora_edit_"))
    current = media_path
    audio_wav: Path | None = None

    # 1. structural video ops
    for op in [o for o in valid if o["op"] in STRUCT_OPS]:
        if op["op"] == "trim":
            out = tmp / f"trim_{len(applied)}.mp4"
            _guard_duration(op.get("start", 0), op.get("end"), settings)
            from app.services.creative import media_engine
            media_engine.trim(current, out, start=float(op.get("start", 0)),
                              end=float(op.get("end", op.get("start", 0) + 30)))
            current = out
        elif op["op"] == "speed":
            from app.services.creative import media_engine
            out = tmp / f"speed_{len(applied)}.mp4"
            media_engine.change_speed(current, out, float(op.get("factor", 1.0)))
            current = out
        elif op["op"] == "reverse":
            current = _reverse_video(current, tmp / "reverse.mp4")
        applied.append(op["op"])
    if progress:
        progress(0.2)

    # 2. collect audio (from the current clip) before the visual re-encode
    try:
        from app.services.creative import media_engine
        audio_wav = media_engine.extract_audio(current, tmp / "audio.wav")
        if audio_wav and not Path(audio_wav).exists():
            audio_wav = None
    except Exception:
        audio_wav = None

    # 3. visual + text pass
    img_ops = [o for o in valid if o["op"] in IMAGE_OPS]
    if img_ops:
        vis = tmp / "visual.mp4"
        _apply_visual_video(current, vis, img_ops, size=size, fps=fps,
                            progress=lambda p: progress and progress(0.2 + 0.5 * p))
        current = vis
        applied += [o["op"] for o in img_ops]

    # 4. audio ops (denoise/eq/volume/fade/mute/normalize/compress)
    audio_ops = [o for o in valid if o["op"] in AUDIO_OPS]
    gen_ops = [o for o in valid if o["op"] in GEN_OPS]
    if audio_wav or gen_ops:
        final_audio = _apply_audio_ops(audio_wav, audio_ops, tmp, settings,
                                       duration=_duration(current, settings))
        final_audio = _apply_gen_ops(final_audio, gen_ops, current, tmp, settings, progress)
        applied += [o["op"] for o in (audio_ops + gen_ops)]
        from app.services.creative import media_engine
        media_engine._mux_audio(current, final_audio, dest)
    else:
        from app.services.creative import media_engine
        media_engine._mux_audio(current, None, dest)
    if progress:
        progress(1.0)
    return {"output": str(dest), "applied": applied,
            "skipped": [o["op"] for o in valid if o["op"] in TIMELINE_OPS]}


def _guard_duration(start: float, end: float | None, settings) -> None:
    if end is not None and end <= start:
        raise CreativeEngineError("trim end must be after start.", "INVALID_PLAN")


def _duration(path: str | Path, settings) -> float:
    try:
        from app.services.creative import media_engine
        info = media_engine.probe(path)
        return float(info.get("duration") or 0.0)
    except Exception:
        return 0.0


def _execute_image(src: Path, ops: list[dict], dest: Path) -> Path:
    from app.services.creative import image_engine

    img = image_engine.open_image(src)
    for op in ops:
        if op["op"] not in IMAGE_OPS:
            continue
        if op["op"] == "adjust":
            img = image_engine.apply_adjustments(img, op.get("params", {}))
        elif op["op"] == "filter":
            img = image_engine.apply_filter(img, op.get("name", "none"))
        elif op["op"] == "curves":
            img = image_engine.apply_curves(img, op.get("points", []))
        elif op["op"] == "transform":
            img = image_engine.transform(img, op.get("params", {}))
        elif op["op"] == "text":
            img = image_engine.draw_text(img, op)
        elif op["op"] == "background_remove":
            img = image_engine.remove_background(img, mode=op.get("mode", "edge"),
                                                 tolerance=int(op.get("tolerance", 30)))
    fmt = dest.suffix.lower().lstrip(".") or "png"
    return image_engine.export(img, dest, fmt=fmt)


def _apply_visual_video(src: Path, dest: Path, ops: list[dict], *, size, fps, progress=None) -> None:
    import av

    from app.services.creative import image_engine, media_engine

    texts = []
    for op in ops:
        if op["op"] == "text":
            for t in op.get("texts", []) or []:
                texts.append(t)
    frame_image_ops = [op for op in ops if op["op"] != "text"]

    out_w, out_h = size
    writer = media_engine.VideoWriter(dest, (out_w, out_h), fps)
    with av.open(str(src)) as container:
        stream = container.streams.video[0]
        total = stream.frames or 0

        def frames():
            for i, frame in enumerate(container.decode(stream)):
                img = frame.to_image().convert("RGBA")
                for op in frame_image_ops:
                    if op["op"] == "adjust":
                        img = image_engine.apply_adjustments(img, op.get("params", {}))
                    elif op["op"] == "filter":
                        img = image_engine.apply_filter(img, op.get("name", "none"))
                    elif op["op"] == "curves":
                        img = image_engine.apply_curves(img, op.get("points", []))
                    elif op["op"] == "transform":
                        img = image_engine.transform(img, op.get("params", {}))
                if texts:
                    t = i / fps
                    active = {"texts": [tx for tx in texts
                                        if float(tx.get("start", 0)) <= t <= float(tx.get("end", 1e9))]}
                    if active["texts"]:
                        img = image_engine.draw_text(img, active)
                img = media_engine._fit(img, (out_w, out_h), "contain")
                if progress and total:
                    progress(min(1.0, i / total))
                yield img

        writer.write(frames())


def _reverse_video(src: Path, dest: Path) -> Path:
    import av

    from app.services.creative import media_engine

    frames = []
    audio = None
    with av.open(str(src)) as container:
        vstream = container.streams.video[0]
        size = (vstream.codec_context.width, vstream.codec_context.height)
        fps = float(vstream.average_rate) if vstream.average_rate else 30.0
        for frame in container.decode(vstream):
            frames.append(frame.to_image())
    try:
        tmp_audio = dest.with_suffix(".src.wav")
        audio = media_engine.extract_audio(src, tmp_audio)
        if audio and Path(audio).exists():
            import numpy as np
            from app.services.creative import audio_engine
            data, sr = audio_engine.load_audio(audio)
            audio_engine.save_audio(tmp_audio, data[::-1].copy(), sr)
            audio = tmp_audio
        else:
            audio = None
    except Exception:
        audio = None
    writer = media_engine.VideoWriter(dest, size, fps, audio=audio)
    writer.write(reversed(frames))
    return dest


def _apply_audio_ops(audio_wav: Path | None, ops: list[dict], tmp: Path, settings, *, duration: float) -> Path | None:
    from app.services.creative import audio_engine

    if not ops:
        return audio_wav
    if any(op["op"] == "mute" for op in ops):
        return None
    if audio_wav is None:
        return None
    data, sr = audio_engine.load_audio(audio_wav)
    for op in ops:
        name = op["op"]
        if name == "mute":
            return None
        if name == "volume":
            data = audio_engine.apply_gain(data, float(op.get("db", 0)))
        elif name == "audio_fade":
            data = audio_engine.fade(data, sr, float(op.get("in", 0)), float(op.get("out", 0)))
        elif name == "normalize_audio":
            data = audio_engine.normalize_loudness(data, sr)
        elif name == "denoise":
            data = audio_engine.noise_reduce(data, sr, float(op.get("strength", 0.7)))
        elif name == "eq":
            data = audio_engine.equalize(data, sr, op.get("bands", []))
        elif name == "compress":
            data = audio_engine.compress(data, threshold_db=float(op.get("threshold_db", -18)),
                                         ratio=float(op.get("ratio", 3)), makeup_db=float(op.get("makeup_db", 6)))
    out = tmp / "audio_processed.wav"
    audio_engine.save_audio(out, data, sr)
    return out


def _apply_gen_ops(audio_wav: Path | None, ops: list[dict], video: Path, tmp: Path,
                   settings, progress) -> Path | None:
    from app.services.creative import audio_engine, providers, transcribe

    if not ops:
        return audio_wav
    base = audio_wav
    for i, op in enumerate(ops):
        name = op["op"]
        if name == "captions":
            try:
                result = transcribe.transcribe(video, model_size=settings.creative_stt_model,
                                               language=op.get("language"))
                _burn_captions(video, result["segments"], op.get("style"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("caption burn failed: %s", exc)
        elif name in ("voiceover", "music"):
            req = providers.GenRequest(kind="audio", prompt=op.get("text") or op.get("prompt", ""),
                                       duration=float(op.get("duration", _duration(video, settings) or 5)),
                                       voice=op.get("voice"))
            provider = providers.resolve_provider("audio", None, settings)
            out = provider.generate(req)[0]
            gen_path = tmp / f"gen_{name}_{i}{out.extension}"
            gen_path.write_bytes(out.data)
            base = _mix_audio(base, gen_path, tmp / f"mix_{name}_{i}.wav")
    return base


def _mix_audio(a: Path | None, b: Path, dest: Path) -> Path:
    from app.services.creative import audio_engine

    if a is None or not Path(a).exists():
        import shutil
        shutil.copy(b, dest)
        return dest
    da, sr = audio_engine.load_audio(a)
    db, _ = audio_engine.load_audio(b)
    mixed, sr = audio_engine.mix([{"data": da, "sr": sr, "volume": 1.0},
                                  {"data": db, "sr": sr, "volume": 0.6}])
    return audio_engine.save_audio(dest, mixed, sr)


def _burn_captions(video: Path, segments: list[dict], style: dict | None) -> None:
    from app.services.creative import media_engine

    style = style or {}
    texts = []
    for seg in segments:
        if seg.get("is_filler"):
            continue
        texts.append({"text": seg["text"], "position": "bottom_center",
                      "start": seg["start"], "end": seg["end"],
                      "size": style.get("size", 42), "color": style.get("color", "#ffffff")})
    if texts:
        tmp_out = video.with_suffix(".captioned.mp4")
        media_engine.burn_text(video, tmp_out, texts)
        import shutil
        shutil.move(str(tmp_out), str(video))
