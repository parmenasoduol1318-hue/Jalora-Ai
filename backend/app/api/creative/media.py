"""Media editing, transcription and AI auto-edit endpoints."""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.creative._helpers import (
    owned_asset,
    owned_project,
    persist_bytes,
    raise_http,
    require_creative,
    serialize_asset,
)
from app.api.creative._schemas import (
    AiEditRequest,
    AudioOpsRequest,
    ImageOpsRequest,
    TranscribeRequest,
    VideoOpsRequest,
)
from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.database.session import get_db
from app.errors import BadRequestError
from app.models.creative import TranscriptSegment
from app.models.user import User
from app.services.creative import (
    ai_editor,
    audio_engine,
    image_engine,
    media_engine,
    storage,
    transcribe,
)

router = APIRouter(dependencies=[Depends(require_creative)])


@router.get("/image/filters")
def list_filters():
    return {"success": True, "filters": image_engine.list_filters()}


@router.get("/ai-edit/ops")
def list_ai_edit_ops():
    return {"success": True, "ops": ai_editor.OP_SPECS}


@router.post("/image/ops")
def apply_image_ops(req: ImageOpsRequest, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    asset = owned_asset(db, user, req.asset_id)
    path = storage.resolve(user.id, asset.file_path)
    try:
        img = image_engine.open_image(path)
        for op in req.ops:
            name = op.get("op")
            if name == "adjust":
                img = image_engine.apply_adjustments(img, op.get("params", op))
            elif name == "filter":
                img = image_engine.apply_filter(img, op.get("name", "none"))
            elif name == "curves":
                img = image_engine.apply_curves(img, op.get("points", op.get("curve", [])))
            elif name == "transform":
                img = image_engine.transform(img, op.get("params", op))
            elif name == "text":
                img = image_engine.draw_text(img, op)
            elif name == "background_remove":
                img = image_engine.remove_background(img, mode=op.get("mode", "edge"),
                                                     tolerance=int(op.get("tolerance", 30)))
            elif name == "mask":
                img = image_engine.remove_background(img, mode="edge")
            else:
                raise BadRequestError(f"Unknown image op: {name}", "UNKNOWN_OP")
        fmt = (req.output_format or "png").lower().lstrip(".")
        import io
        buf = io.BytesIO()
        image_engine.export(img, buf, fmt=fmt, quality=req.quality or 90)
        data = buf.getvalue()
    except BadRequestError:
        raise
    except ValueError as exc:
        raise BadRequestError(str(exc), "INVALID_OPS") from exc
    out = persist_bytes(db, user.id, data, f"{asset.id}_edit.{fmt}", file_type="image",
                        source="generated", subdir="edits", project_id=req.project_id or asset.project_id,
                        metadata={"source_asset": asset.id, "ops": req.ops})
    return {"success": True, "asset": serialize_asset(out)}


@router.post("/video/thumbnail")
def video_thumbnail(payload: dict, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    asset = owned_asset(db, user, payload.get("asset_id", ""))
    t = float(payload.get("time", 0.0))
    path = storage.resolve(user.id, asset.file_path)
    try:
        img = media_engine.extract_frame(path, t)
    except Exception as exc:
        raise BadRequestError(f"Frame extraction failed: {exc}", "FRAME_FAIL")
    import io
    buf = io.BytesIO()
    image_engine.export(img, buf, fmt="png")
    out = persist_bytes(db, user.id, buf.getvalue(), f"{asset.id}_frame.png",
                        file_type="image", source="generated", subdir="edits",
                        project_id=asset.project_id, metadata={"source_asset": asset.id, "time": t})
    return {"success": True, "asset": serialize_asset(out)}


@router.post("/audio/waveform")
def audio_waveform(payload: dict, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    asset = owned_asset(db, user, payload.get("asset_id", ""))
    buckets = int(payload.get("buckets", 800))
    path = storage.resolve(user.id, asset.file_path)
    try:
        if asset.file_type == "video":
            import tempfile
            wav = Path(tempfile.mkdtemp()) / "a.wav"
            media_engine.extract_audio(path, wav)
            peaks = media_engine.waveform_peaks(path, buckets)
        else:
            data, sr = audio_engine.load_audio(path)
            peaks = audio_engine.peaks(data, buckets)
    except Exception as exc:
        raise BadRequestError(f"Waveform failed: {exc}", "WAVEFORM_FAIL")
    return {"success": True, "peaks": peaks}


@router.post("/audio/ops")
def apply_audio_ops(req: AudioOpsRequest, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    asset = owned_asset(db, user, req.asset_id)
    path = storage.resolve(user.id, asset.file_path)
    try:
        if asset.file_type == "video" or path.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv", ".m4v"):
            import tempfile
            wav = Path(tempfile.mkdtemp()) / "a.wav"
            data, sr = audio_engine.load_audio(media_engine.extract_audio(path, wav))
        else:
            data, sr = audio_engine.load_audio(path)
        for op in req.ops:
            name = op.get("op")
            if name == "volume" or name == "gain":
                data = audio_engine.apply_gain(data, float(op.get("db", op.get("gain_db", 0))))
            elif name == "fade":
                data = audio_engine.fade(data, sr, float(op.get("in", 0)), float(op.get("out", 0)))
            elif name == "normalize":
                data = audio_engine.normalize_loudness(data, sr, float(op.get("target_lufs", -16)))
            elif name == "denoise":
                data = audio_engine.noise_reduce(data, sr, float(op.get("strength", 0.7)))
            elif name == "eq":
                data = audio_engine.equalize(data, sr, op.get("bands", []))
            elif name == "compress":
                data = audio_engine.compress(data, threshold_db=float(op.get("threshold_db", -18)),
                                             ratio=float(op.get("ratio", 3)),
                                             makeup_db=float(op.get("makeup_db", 6)))
            elif name == "trim":
                data = audio_engine.trim(data, sr, float(op.get("start", 0)), float(op.get("end", len(data) / sr)))
            elif name == "reverse":
                data = audio_engine.reverse(data)
            elif name == "speed":
                data = audio_engine.change_speed(data, float(op.get("factor", 1.0)))
            elif name == "fade":
                data = audio_engine.fade(data, sr, float(op.get("in", 0)), float(op.get("out", 0)))
            else:
                raise BadRequestError(f"Unknown audio op: {name}", "UNKNOWN_OP")
        import io
        buf = io.BytesIO()
        import soundfile as sf
        fmt = (req.output_format or "wav").lower().lstrip(".")
        if fmt == "wav":
            sf.write(buf, data, sr, format="WAV")
            ext, mime = ".wav", "audio/wav"
        else:
            tmp = Path(tempfile.mkdtemp()) / f"out.{fmt}"
            audio_engine.encode_audio(tmp, data, sr, codec=fmt)
            buf.write(tmp.read_bytes())
            ext, mime = f".{fmt}", "audio/mpeg"
        out = persist_bytes(db, user.id, buf.getvalue(), f"{asset.id}_audio{ext}",
                            file_type="audio", source="generated", subdir="edits",
                            project_id=req.project_id or asset.project_id,
                            metadata={"source_asset": asset.id, "ops": req.ops})
    except BadRequestError:
        raise
    except Exception as exc:
        raise BadRequestError(f"Audio processing failed: {exc}", "AUDIO_FAIL")
    return {"success": True, "asset": serialize_asset(out)}


@router.post("/transcribe")
def transcribe_asset(req: TranscribeRequest, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user),
                     settings: Settings = Depends(get_settings)):
    asset = owned_asset(db, user, req.asset_id)
    path = storage.resolve(user.id, asset.file_path)
    try:
        result = transcribe.transcribe(path, model_size=req.model or settings.creative_stt_model,
                                       language=req.language)
    except Exception as exc:
        raise BadRequestError(f"Transcription failed: {exc}", "STT_FAILED")
    saved = []
    if req.save_to_project and req.project_id:
        project = owned_project(db, user, req.project_id)
        for seg in result["segments"]:
            row = TranscriptSegment(
                id=uuid.uuid4().hex, project_id=project.id, asset_id=asset.id,
                start_time=seg["start"], end_time=seg["end"], text=seg["text"],
                words_json=seg.get("words", []), is_filler=seg.get("is_filler", False),
            )
            db.add(row)
            saved.append(row)
        db.commit()
        for row in saved:
            db.refresh(row)
    return {
        "success": True,
        "language": result["language"],
        "duration": result["duration"],
        "segments": result["segments"],
        "saved": len(saved),
    }


@router.get("/projects/{project_id}/transcripts")
def get_transcripts(project_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    return {"success": True, "transcripts": [
        {"id": ts.id, "asset_id": ts.asset_id, "start_time": ts.start_time,
         "end_time": ts.end_time, "text": ts.text, "speaker": ts.speaker,
         "words": ts.words_json or [], "is_filler": ts.is_filler}
        for ts in project.transcripts
    ]}


@router.get("/projects/{project_id}/subtitles")
def export_subtitles(project_id: str, fmt: str = "srt", db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    project = owned_project(db, user, project_id)
    segments = [{"start": ts.start_time, "end": ts.end_time, "text": ts.text}
                for ts in project.transcripts]
    if fmt == "vtt":
        return {"success": True, "format": "vtt", "content": transcribe.to_vtt(segments)}
    return {"success": True, "format": "srt", "content": transcribe.to_srt(segments)}


@router.post("/ai-edit")
async def ai_edit(req: AiEditRequest, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user),
                  settings: Settings = Depends(get_settings)):
    if req.project_id:
        owned_project(db, user, req.project_id)
    disabled = None
    if hasattr(user, "prefs") and user.prefs:
        from app.services.provider_state import normalize_disabled
        disabled = normalize_disabled(user.prefs.get("disabled_providers"))
    plan = await ai_editor.plan_edits(req.instruction, disabled=disabled,
                                      provider_override=req.provider, model_override=req.model)
    response: dict = {"success": True, "plan": plan}
    if req.execute:
        if not req.asset_id:
            raise BadRequestError("asset_id is required to execute an AI edit plan.", "MISSING_ASSET")
        asset = owned_asset(db, user, req.asset_id)
        path = storage.resolve(user.id, asset.file_path)
        is_image = asset.file_type == "image"
        ext = ".png" if is_image else ".mp4"
        import tempfile
        tmp_out = Path(tempfile.mkdtemp()) / f"ai_edit{ext}"
        try:
            result = ai_editor.execute_plan(path, plan["ops"], tmp_out, is_image=is_image,
                                            size=(req.width or 1280, req.height or 720),
                                            settings=settings)
        except Exception as exc:
            raise BadRequestError(f"AI edit execution failed: {exc}", "AI_EDIT_FAIL")
        data = Path(result["output"]).read_bytes()
        out_asset = persist_bytes(
            db, user.id, data, f"{asset.id}_ai{ext}",
            file_type="image" if is_image else "video", source="generated",
            subdir="edits", project_id=req.project_id or asset.project_id,
            metadata={"source_asset": asset.id, "plan": plan, "applied": result["applied"]},
        )
        response["applied"] = result["applied"]
        response["skipped"] = result["skipped"]
        response["asset"] = serialize_asset(out_asset)
    return response


@router.post("/video/ops")
def apply_video_ops(req: VideoOpsRequest, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user),
                    settings: Settings = Depends(get_settings)):
    asset = owned_asset(db, user, req.asset_id)
    path = storage.resolve(user.id, asset.file_path)
    import tempfile
    out = Path(tempfile.mkdtemp()) / "video_ops.mp4"
    try:
        result = ai_editor.execute_plan(
            path, req.ops, out, is_image=False,
            size=(req.width or 1280, req.height or 720), fps=req.fps or 30.0)
    except Exception as exc:
        raise BadRequestError(f"Video processing failed: {exc}", "VIDEO_FAIL")
    data = out.read_bytes()
    out_asset = persist_bytes(db, user.id, data, f"{asset.id}_video.mp4", file_type="video",
                              source="generated", subdir="edits",
                              project_id=req.project_id or asset.project_id,
                              metadata={"source_asset": asset.id, "ops": req.ops,
                                        "applied": result["applied"]})
    return {"success": True, "asset": serialize_asset(out_asset), "applied": result["applied"]}
