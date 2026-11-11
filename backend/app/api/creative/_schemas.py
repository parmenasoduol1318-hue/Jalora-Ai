"""Pydantic request models for the Creative Studio API."""
from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    title: Union[str, int, float, None] = "Untitled Project"
    description: Optional[str] = None
    project_type: Optional[str] = "video"
    aspect_ratio: Union[str, float, int, None] = "16:9"
    brand_kit_id: Optional[str] = None
    settings_json: Optional[dict] = None


class ProjectUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    project_type: Optional[str] = None
    aspect_ratio: Optional[str] = None
    status: Optional[str] = None
    brand_kit_id: Optional[str] = None
    settings_json: Optional[dict] = None
    thumbnail_asset_id: Optional[str] = None


class TrackCreateRequest(BaseModel):
    track_type: str = "video"
    name: Optional[str] = None
    track_index: Optional[int] = None


class TrackUpdateRequest(BaseModel):
    name: Optional[str] = None
    track_index: Optional[int] = None
    is_muted: Optional[bool] = None
    is_locked: Optional[bool] = None
    is_hidden: Optional[bool] = None
    is_solo: Optional[bool] = None
    volume: Optional[float] = None
    track_type: Optional[str] = None


class ClipCreateRequest(BaseModel):
    asset_id: Optional[str] = None
    clip_type: str = "media"
    name: Optional[str] = None
    start_time: float = 0.0
    duration: float = 5.0
    trim_start: float = 0.0
    trim_end: float = 5.0
    speed: float = 1.0
    opacity: float = 1.0
    z_index: int = 0
    link_group: Optional[str] = None
    transform_json: Optional[dict] = None
    effects_json: Optional[dict] = None
    keyframes_json: Optional[dict] = None
    text_json: Optional[dict] = None


class ClipUpdateRequest(BaseModel):
    start_time: Optional[float] = None
    duration: Optional[float] = None
    trim_start: Optional[float] = None
    trim_end: Optional[float] = None
    speed: Optional[float] = None
    opacity: Optional[float] = None
    z_index: Optional[int] = None
    name: Optional[str] = None
    transform_json: Optional[dict] = None
    effects_json: Optional[dict] = None
    keyframes_json: Optional[dict] = None
    text_json: Optional[dict] = None


class TimelineSaveRequest(BaseModel):
    tracks: list[dict] = Field(default_factory=list)
    clips: list[dict] = Field(default_factory=list)


class ImageOpsRequest(BaseModel):
    asset_id: str
    ops: list[dict]
    output_format: Optional[str] = None
    quality: Optional[int] = 90
    project_id: Optional[str] = None


class VideoOpsRequest(BaseModel):
    asset_id: str
    ops: list[dict]
    project_id: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = 30.0


class AudioOpsRequest(BaseModel):
    asset_id: str
    ops: list[dict]
    project_id: Optional[str] = None
    output_format: Optional[str] = None


class TranscribeRequest(BaseModel):
    asset_id: str
    project_id: Optional[str] = None
    language: Optional[str] = None
    model: Optional[str] = None
    save_to_project: bool = True


class AiEditRequest(BaseModel):
    project_id: Optional[str] = None
    asset_id: Optional[str] = None
    instruction: str
    execute: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    save_to_project: bool = True


class ImageGenerationRequest(BaseModel):
    prompt: Union[str, int, float]
    negative_prompt: Optional[Union[str, int, float]] = None
    aspect_ratio: Union[str, float, int, None] = "16:9"
    model: Optional[str] = None
    provider: Optional[str] = "auto"
    num_variations: Union[int, float, str, None] = 1
    seed: Optional[Union[int, str]] = None
    project_id: Optional[str] = None
    reference_asset_id: Optional[str] = None
    mask_asset_id: Optional[str] = None
    strength: Optional[float] = 0.75  # img2img denoising strength (0=keep ref, 1=full regen)
    image_type: Optional[str] = None  # override auto-detected type (portrait, landscape, product, etc.)
    remove_background: Optional[bool] = False  # request transparent-background output


class VideoGenerationRequest(BaseModel):
    prompt: Union[str, int, float]
    negative_prompt: Optional[Union[str, int, float]] = None
    aspect_ratio: Union[str, float, int, None] = "16:9"
    model: Optional[str] = None
    provider: Optional[str] = "auto"
    duration: Union[float, int, str] = 5.0
    seed: Optional[Union[int, str]] = None
    project_id: Optional[str] = None
    reference_asset_id: Optional[str] = None


class AudioGenerationRequest(BaseModel):
    prompt: str
    duration: Union[float, int, str] = 10.0
    provider: Optional[str] = "auto"
    model: Optional[str] = None
    voice: Optional[str] = None
    project_id: Optional[str] = None
    kind: Optional[str] = "music"


class RenderRequest(BaseModel):
    project_id: str
    kind: Optional[str] = "video"
    options: Optional[dict] = None


class VersionCreateRequest(BaseModel):
    label: Optional[str] = None


class ShareCreateRequest(BaseModel):
    shared_with_email: Optional[str] = None
    role: Optional[str] = "viewer"
    expires_in_days: Optional[int] = None


class TemplateCreateRequest(BaseModel):
    title: str
    category: Optional[str] = "social"
    kind: Optional[str] = "video"
    aspect_ratio: Optional[str] = "16:9"
    tags: Optional[list] = None
    template_data: Optional[dict] = None


class TemplateApplyRequest(BaseModel):
    title: Optional[str] = None


class BrandKitCreateRequest(BaseModel):
    name: str
    colors: Optional[list] = None
    fonts: Optional[list] = None
    logo_asset_id: Optional[str] = None


class BrandKitUpdateRequest(BaseModel):
    name: Optional[str] = None
    colors: Optional[list] = None
    fonts: Optional[list] = None
    logo_asset_id: Optional[str] = None


class EffectPresetCreateRequest(BaseModel):
    name: str
    kind: Optional[str] = "video"
    category: Optional[str] = "filter"
    params: Optional[dict] = None
