"""SQLAlchemy models for Jalora Creative Studio.

Covers creative projects, project versions, media assets, multitrack timelines
(tracks/clips/keyframes), render jobs, transcripts, generation jobs (image/video/
audio), templates, brand kits, effect presets and collaboration shares.

Large binary media is never stored here: :class:`MediaAsset.file_path` holds a
path relative to the owner's artifact root (``data/uploads/<user_id>/``) and is
served by the owner-only ``/api/artifacts`` route. Structured project state is
kept in versioned JSON documents so it can be validated and snapshotted.
"""
from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.base import Base


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


class CreativeProject(Base):
    __tablename__ = "creative_projects"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    project_type = Column(String(50), default="video")  # video, image, audio, mixed
    aspect_ratio = Column(String(16), default="16:9")
    status = Column(String(30), default="draft")  # draft, active, archived
    brand_kit_id = Column(String(36), ForeignKey("creative_brand_kits.id"), nullable=True)
    settings_json = Column(JSON, nullable=True, default=dict)
    thumbnail_url = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    assets = relationship("MediaAsset", back_populates="project", cascade="all, delete-orphan")
    tracks = relationship("TimelineTrack", back_populates="project", cascade="all, delete-orphan",
                          order_by="TimelineTrack.track_index")
    render_jobs = relationship("RenderJob", back_populates="project", cascade="all, delete-orphan")
    transcripts = relationship("TranscriptSegment", back_populates="project", cascade="all, delete-orphan")
    versions = relationship("CreativeProjectVersion", back_populates="project", cascade="all, delete-orphan")
    shares = relationship("CreativeProjectShare", back_populates="project", cascade="all, delete-orphan")
    generations = relationship("GenerationJob", back_populates="project")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(String(36), ForeignKey("creative_projects.id"), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    # Path relative to the owner's artifact root, e.g. "creative/ab12.png".
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(50), nullable=False)  # image, video, audio, text
    source = Column(String(30), default="upload")  # upload, generated, render
    size_bytes = Column(Integer, default=0)
    duration = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    thumbnail_path = Column(String(512), nullable=True)
    checksum = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=_utcnow)

    project = relationship("CreativeProject", back_populates="assets")


class TimelineTrack(Base):
    __tablename__ = "creative_timeline_tracks"

    id = Column(String(36), primary_key=True, index=True)
    project_id = Column(String(36), ForeignKey("creative_projects.id"), nullable=False, index=True)
    track_index = Column(Integer, default=0)
    track_type = Column(String(50), default="video")  # video, audio, text, image, effect
    name = Column(String(100), nullable=False)
    is_muted = Column(Boolean, default=False)
    is_locked = Column(Boolean, default=False)
    is_hidden = Column(Boolean, default=False)
    is_solo = Column(Boolean, default=False)
    volume = Column(Float, default=1.0)
    created_at = Column(DateTime, default=_utcnow)

    project = relationship("CreativeProject", back_populates="tracks")
    clips = relationship("TimelineClip", back_populates="track", cascade="all, delete-orphan",
                         order_by="TimelineClip.start_time")


class TimelineClip(Base):
    __tablename__ = "creative_timeline_clips"

    id = Column(String(36), primary_key=True, index=True)
    track_id = Column(String(36), ForeignKey("creative_timeline_tracks.id"), nullable=False, index=True)
    asset_id = Column(String(36), ForeignKey("media_assets.id"), nullable=True, index=True)
    clip_type = Column(String(30), default="media")  # media, text, shape, image, audio, adjustment
    name = Column(String(255), nullable=True)
    start_time = Column(Float, default=0.0)      # position on the timeline (s)
    duration = Column(Float, default=5.0)        # timeline duration (s)
    trim_start = Column(Float, default=0.0)      # in-point within source (s)
    trim_end = Column(Float, default=5.0)        # out-point within source (s)
    speed = Column(Float, default=1.0)
    opacity = Column(Float, default=1.0)
    z_index = Column(Integer, default=0)
    link_group = Column(String(36), nullable=True)  # linked audio+video group id
    transform_json = Column(JSON, nullable=True, default=dict)
    effects_json = Column(JSON, nullable=True, default=dict)
    keyframes_json = Column(JSON, nullable=True, default=dict)
    text_json = Column(JSON, nullable=True, default=dict)  # title/caption content + style
    created_at = Column(DateTime, default=_utcnow)

    track = relationship("TimelineTrack", back_populates="clips")
    asset = relationship("MediaAsset")


class RenderJob(Base):
    __tablename__ = "creative_render_jobs"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(String(36), ForeignKey("creative_projects.id"), nullable=True, index=True)
    output_asset_id = Column(String(36), ForeignKey("media_assets.id"), nullable=True)
    kind = Column(String(30), default="video")  # video, image, audio, subtitle
    status = Column(String(50), default="queued")  # queued, running, completed, failed, cancelled
    progress_pct = Column(Float, default=0.0)
    message = Column(String(255), nullable=True)
    output_url = Column(String(512), nullable=True)
    error_message = Column(Text, nullable=True)
    settings_json = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    project = relationship("CreativeProject", back_populates="render_jobs")


class TranscriptSegment(Base):
    __tablename__ = "creative_transcript_segments"

    id = Column(String(36), primary_key=True, index=True)
    project_id = Column(String(36), ForeignKey("creative_projects.id"), nullable=False, index=True)
    asset_id = Column(String(36), ForeignKey("media_assets.id"), nullable=True, index=True)
    start_time = Column(Float, default=0.0)
    end_time = Column(Float, default=0.0)
    text = Column(Text, nullable=False)
    speaker = Column(String(100), default="Speaker 1")
    words_json = Column(JSON, nullable=True, default=list)  # [{word,start,end,prob}]
    is_filler = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)

    project = relationship("CreativeProject", back_populates="transcripts")


class GenerationJob(Base):
    __tablename__ = "creative_generation_jobs"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(String(36), ForeignKey("creative_projects.id"), nullable=True, index=True)
    kind = Column(String(30), nullable=False)  # image, video, audio, text
    provider = Column(String(50), nullable=True)
    model = Column(String(120), nullable=True)
    prompt = Column(Text, nullable=True)
    negative_prompt = Column(Text, nullable=True)
    params_json = Column(JSON, nullable=True, default=dict)
    status = Column(String(50), default="queued")  # queued, running, completed, failed, cancelled
    progress_pct = Column(Float, default=0.0)
    message = Column(String(255), nullable=True)
    outputs_json = Column(JSON, nullable=True, default=list)  # [{asset_id, url, ...}]
    error_code = Column(String(60), nullable=True)
    error_message = Column(Text, nullable=True)
    cost_estimate_usd = Column(Float, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    project = relationship("CreativeProject", back_populates="generations")


class CreativeProjectVersion(Base):
    __tablename__ = "creative_project_versions"

    id = Column(String(36), primary_key=True, index=True)
    project_id = Column(String(36), ForeignKey("creative_projects.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    version_no = Column(Integer, default=1)
    label = Column(String(255), nullable=True)
    snapshot_json = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=_utcnow)

    project = relationship("CreativeProject", back_populates="versions")


class CreativeProjectShare(Base):
    __tablename__ = "creative_project_shares"

    id = Column(String(36), primary_key=True, index=True)
    project_id = Column(String(36), ForeignKey("creative_projects.id"), nullable=False, index=True)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    shared_with_user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    shared_with_email = Column(String(255), nullable=True)
    role = Column(String(20), default="viewer")  # viewer, editor, owner
    token = Column(String(64), nullable=True, unique=True)
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=True)

    project = relationship("CreativeProject", back_populates="shares")


class CreativeTemplate(Base):
    __tablename__ = "creative_templates"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    category = Column(String(100), default="social")  # social, cinematic, commercial, tutorial
    kind = Column(String(30), default="video")  # video, image, audio
    aspect_ratio = Column(String(16), default="16:9")
    preview_url = Column(String(512), nullable=True)
    tags_json = Column(JSON, nullable=True, default=list)
    is_public = Column(Boolean, default=True)
    template_data_json = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=_utcnow)


class BrandKit(Base):
    __tablename__ = "creative_brand_kits"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    colors_json = Column(JSON, nullable=True, default=list)  # ["#RRGGBB", ...]
    fonts_json = Column(JSON, nullable=True, default=list)   # [{name, family, url}]
    # Soft reference to a MediaAsset id (plain column to keep the schema acyclic;
    # validated in the API layer).
    logo_asset_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class EffectPreset(Base):
    __tablename__ = "creative_effect_presets"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    kind = Column(String(30), default="video")  # video, image, audio
    category = Column(String(60), default="filter")  # filter, transition, grade, effect
    params_json = Column(JSON, nullable=True, default=dict)
    is_builtin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)
