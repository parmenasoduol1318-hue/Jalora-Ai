"""Templates, brand kits and effect presets."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.creative._helpers import iso, owned_asset, require_creative
from app.api.creative._schemas import (
    BrandKitCreateRequest,
    BrandKitUpdateRequest,
    EffectPresetCreateRequest,
    TemplateApplyRequest,
    TemplateCreateRequest,
)
from app.api.deps import get_current_user
from app.database.session import get_db
from app.errors import NotFoundError
from app.models.creative import (
    BrandKit,
    CreativeProject,
    CreativeTemplate,
    EffectPreset,
    TimelineClip,
    TimelineTrack,
)
from app.models.user import User

router = APIRouter(dependencies=[Depends(require_creative)])

_BUILTIN_TEMPLATES = [
    {"title": "Cinematic Travel Vlog", "category": "cinematic", "kind": "video",
     "aspect_ratio": "16:9", "tags": ["cinematic", "travel"],
     "template_data": {"tracks": [{"track_type": "video", "name": "Video 1"},
                                  {"track_type": "audio", "name": "Music"},
                                  {"track_type": "text", "name": "Titles"}]}},
    {"title": "TikTok Viral Promo", "category": "social", "kind": "video",
     "aspect_ratio": "9:16", "tags": ["social", "short"],
     "template_data": {"tracks": [{"track_type": "video", "name": "Main"},
                                  {"track_type": "text", "name": "Captions"}]}},
    {"title": "Product Showcase Ad", "category": "commercial", "kind": "video",
     "aspect_ratio": "1:1", "tags": ["commercial", "product"],
     "template_data": {"tracks": [{"track_type": "video", "name": "Product"},
                                  {"track_type": "audio", "name": "VO"}]}},
    {"title": "YouTube Tutorial", "category": "tutorial", "kind": "video",
     "aspect_ratio": "16:9", "tags": ["tutorial", "education"],
     "template_data": {"tracks": [{"track_type": "video", "name": "Screen"},
                                  {"track_type": "audio", "name": "Voice"},
                                  {"track_type": "text", "name": "Lower Thirds"}]}},
]

_BUILTIN_EFFECTS = [
    {"name": "Cinematic Warm", "kind": "video", "category": "filter",
     "params": {"filter": "cinematic_warm"}},
    {"name": "Noir", "kind": "video", "category": "grade", "params": {"filter": "noir"}},
    {"name": "Neon Cyberpunk", "kind": "video", "category": "filter",
     "params": {"filter": "cyberpunk_neon"}},
    {"name": "Soft Portrait", "kind": "image", "category": "filter",
     "params": {"filter": "soft_portrait"}},
    {"name": "Vivid Punch", "kind": "video", "category": "grade", "params": {"filter": "vivid"}},
]


def _serialize_template(t: CreativeTemplate) -> dict:
    return {
        "id": t.id, "title": t.title, "category": t.category, "kind": t.kind,
        "aspect_ratio": t.aspect_ratio, "preview_url": t.preview_url,
        "tags": t.tags_json or [], "is_public": t.is_public, "data": t.template_data_json or {},
        "created_at": iso(t.created_at),
    }


@router.get("/templates")
def list_templates(db: Session = Depends(get_db), user: User = Depends(get_current_user),
                   kind: str | None = None, category: str | None = None):
    query = db.query(CreativeTemplate).filter(
        (CreativeTemplate.user_id == user.id) | (CreativeTemplate.user_id.is_(None)))
    if kind:
        query = query.filter(CreativeTemplate.kind == kind)
    if category:
        query = query.filter(CreativeTemplate.category == category)
    templates = query.all()
    if not templates:
        for spec in _BUILTIN_TEMPLATES:
            db.add(CreativeTemplate(
                id=uuid.uuid4().hex, user_id=None, title=spec["title"],
                category=spec["category"], kind=spec["kind"], aspect_ratio=spec["aspect_ratio"],
                tags_json=spec["tags"], is_public=True, template_data_json=spec["template_data"],
            ))
        db.commit()
        templates = db.query(CreativeTemplate).all()
    return {"success": True, "templates": [_serialize_template(t) for t in templates]}


@router.post("/templates", status_code=status.HTTP_201_CREATED)
def create_template(req: TemplateCreateRequest, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    template = CreativeTemplate(
        id=uuid.uuid4().hex, user_id=user.id, title=req.title, category=req.category,
        kind=req.kind, aspect_ratio=req.aspect_ratio, tags_json=req.tags or [],
        is_public=False, template_data_json=req.template_data or {},
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return {"success": True, "template": _serialize_template(template)}


@router.post("/templates/{template_id}/apply", status_code=status.HTTP_201_CREATED)
def apply_template(template_id: str, req: TemplateApplyRequest,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    template = db.get(CreativeTemplate, template_id)
    if template is None or (template.user_id not in (None, user.id)):
        raise NotFoundError("Template not found.", "TEMPLATE_NOT_FOUND")
    project = CreativeProject(
        id=uuid.uuid4().hex, user_id=user.id,
        title=req.title or template.title,
        project_type=template.kind if template.kind != "audio" else "video",
        aspect_ratio=template.aspect_ratio, settings_json={},
    )
    db.add(project)
    data = template.template_data_json or {}
    tracks = data.get("tracks") or [{"track_type": "video", "name": "Video 1"}]
    for idx, tdata in enumerate(tracks):
        track = TimelineTrack(
            id=uuid.uuid4().hex, project_id=project.id, track_index=idx,
            track_type=tdata.get("track_type", "video"),
            name=tdata.get("name", f"Track {idx + 1}"),
        )
        db.add(track)
        db.flush()
        for cdata in tdata.get("clips", []):
            db.add(TimelineClip(
                id=uuid.uuid4().hex, track_id=track.id, asset_id=cdata.get("asset_id"),
                clip_type=cdata.get("clip_type", "media"), start_time=cdata.get("start_time", 0.0),
                duration=cdata.get("duration", 5.0), transform_json=cdata.get("transform", {}),
                effects_json=cdata.get("effects", {}), text_json=cdata.get("text", {}),
            ))
    db.commit()
    from app.api.creative._helpers import serialize_project
    db.refresh(project)
    return {"success": True, "project": serialize_project(project, detail=True)}


# --------------------------------------------------------------- brand kits

def _serialize_brand(kit: BrandKit) -> dict:
    return {
        "id": kit.id, "name": kit.name, "colors": kit.colors_json or [],
        "fonts": kit.fonts_json or [], "logo_asset_id": kit.logo_asset_id,
        "created_at": iso(kit.created_at),
    }


@router.get("/brand-kits")
def list_brand_kits(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    kits = db.query(BrandKit).filter(BrandKit.user_id == user.id).all()
    return {"success": True, "brand_kits": [_serialize_brand(k) for k in kits]}


@router.post("/brand-kits", status_code=status.HTTP_201_CREATED)
def create_brand_kit(req: BrandKitCreateRequest, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    if req.logo_asset_id:
        owned_asset(db, user, req.logo_asset_id)
    kit = BrandKit(
        id=uuid.uuid4().hex, user_id=user.id, name=req.name,
        colors_json=req.colors or [], fonts_json=req.fonts or [], logo_asset_id=req.logo_asset_id,
    )
    db.add(kit)
    db.commit()
    db.refresh(kit)
    return {"success": True, "brand_kit": _serialize_brand(kit)}


@router.patch("/brand-kits/{kit_id}")
def update_brand_kit(kit_id: str, req: BrandKitUpdateRequest,
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    kit = db.get(BrandKit, kit_id)
    if kit is None or kit.user_id != user.id:
        raise NotFoundError("Brand kit not found.", "BRAND_KIT_NOT_FOUND")
    if req.name is not None:
        kit.name = req.name
    if req.colors is not None:
        kit.colors_json = req.colors
    if req.fonts is not None:
        kit.fonts_json = req.fonts
    if req.logo_asset_id is not None:
        owned_asset(db, user, req.logo_asset_id)
        kit.logo_asset_id = req.logo_asset_id
    db.commit()
    db.refresh(kit)
    return {"success": True, "brand_kit": _serialize_brand(kit)}


# ------------------------------------------------------------ effect presets

def _serialize_effect(p: EffectPreset) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "category": p.category,
            "params": p.params_json or {}, "is_builtin": p.is_builtin}


@router.get("/effect-presets")
def list_effect_presets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    presets = db.query(EffectPreset).filter(
        (EffectPreset.user_id == user.id) | (EffectPreset.is_builtin.is_(True))).all()
    if not any(p.is_builtin for p in presets):
        for spec in _BUILTIN_EFFECTS:
            db.add(EffectPreset(
                id=uuid.uuid4().hex, user_id=user.id, name=spec["name"], kind=spec["kind"],
                category=spec["category"], params_json=spec["params"], is_builtin=True,
            ))
        db.commit()
        presets = db.query(EffectPreset).filter(EffectPreset.user_id == user.id).all()
    return {"success": True, "presets": [_serialize_effect(p) for p in presets]}


@router.post("/effect-presets", status_code=status.HTTP_201_CREATED)
def create_effect_preset(req: EffectPresetCreateRequest, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    preset = EffectPreset(
        id=uuid.uuid4().hex, user_id=user.id, name=req.name, kind=req.kind,
        category=req.category, params_json=req.params or {}, is_builtin=False,
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return {"success": True, "preset": _serialize_effect(preset)}
