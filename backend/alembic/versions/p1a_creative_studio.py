"""add creative studio tables (projects, media, timelines, generation, render,
transcripts, templates, brand kits, effect presets, versions, shares).

The creative models were declared earlier but never migrated, so every
``/api/creative`` endpoint failed against a migrated database. This revision
creates the full schema. Creation is guarded by inspector checks so it is safe
on databases where the tables were already materialised via ``create_all``.

Revision ID: p1a_creative_studio
Revises: n1b_call_mode
Create Date: 2026-10-17 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "p1a_creative_studio"
down_revision: Union[str, Sequence[str], None] = "n1b_call_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has_table("creative_brand_kits"):
        op.create_table(
            "creative_brand_kits",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("colors_json", sa.JSON(), nullable=True),
            sa.Column("fonts_json", sa.JSON(), nullable=True),
            sa.Column("logo_asset_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_projects"):
        op.create_table(
            "creative_projects",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("project_type", sa.String(length=50), nullable=True),
            sa.Column("aspect_ratio", sa.String(length=16), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=True),
            sa.Column("brand_kit_id", sa.String(length=36), sa.ForeignKey("creative_brand_kits.id"), nullable=True),
            sa.Column("settings_json", sa.JSON(), nullable=True),
            sa.Column("thumbnail_url", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("media_assets"):
        op.create_table(
            "media_assets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("creative_projects.id"), nullable=True, index=True),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("file_path", sa.String(length=512), nullable=False),
            sa.Column("file_type", sa.String(length=50), nullable=False),
            sa.Column("source", sa.String(length=30), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column("duration", sa.Float(), nullable=True),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("thumbnail_path", sa.String(length=512), nullable=True),
            sa.Column("checksum", sa.String(length=64), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_timeline_tracks"):
        op.create_table(
            "creative_timeline_tracks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("creative_projects.id"), nullable=False, index=True),
            sa.Column("track_index", sa.Integer(), nullable=True),
            sa.Column("track_type", sa.String(length=50), nullable=True),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("is_muted", sa.Boolean(), nullable=True),
            sa.Column("is_locked", sa.Boolean(), nullable=True),
            sa.Column("is_hidden", sa.Boolean(), nullable=True),
            sa.Column("is_solo", sa.Boolean(), nullable=True),
            sa.Column("volume", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_timeline_clips"):
        op.create_table(
            "creative_timeline_clips",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("track_id", sa.String(length=36), sa.ForeignKey("creative_timeline_tracks.id"), nullable=False, index=True),
            sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("media_assets.id"), nullable=True, index=True),
            sa.Column("clip_type", sa.String(length=30), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("start_time", sa.Float(), nullable=True),
            sa.Column("duration", sa.Float(), nullable=True),
            sa.Column("trim_start", sa.Float(), nullable=True),
            sa.Column("trim_end", sa.Float(), nullable=True),
            sa.Column("speed", sa.Float(), nullable=True),
            sa.Column("opacity", sa.Float(), nullable=True),
            sa.Column("z_index", sa.Integer(), nullable=True),
            sa.Column("link_group", sa.String(length=36), nullable=True),
            sa.Column("transform_json", sa.JSON(), nullable=True),
            sa.Column("effects_json", sa.JSON(), nullable=True),
            sa.Column("keyframes_json", sa.JSON(), nullable=True),
            sa.Column("text_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_render_jobs"):
        op.create_table(
            "creative_render_jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("creative_projects.id"), nullable=True, index=True),
            sa.Column("output_asset_id", sa.String(length=36), sa.ForeignKey("media_assets.id"), nullable=True),
            sa.Column("kind", sa.String(length=30), nullable=True),
            sa.Column("status", sa.String(length=50), nullable=True),
            sa.Column("progress_pct", sa.Float(), nullable=True),
            sa.Column("message", sa.String(length=255), nullable=True),
            sa.Column("output_url", sa.String(length=512), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("settings_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_transcript_segments"):
        op.create_table(
            "creative_transcript_segments",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("creative_projects.id"), nullable=False, index=True),
            sa.Column("asset_id", sa.String(length=36), sa.ForeignKey("media_assets.id"), nullable=True, index=True),
            sa.Column("start_time", sa.Float(), nullable=True),
            sa.Column("end_time", sa.Float(), nullable=True),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("speaker", sa.String(length=100), nullable=True),
            sa.Column("words_json", sa.JSON(), nullable=True),
            sa.Column("is_filler", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_generation_jobs"):
        op.create_table(
            "creative_generation_jobs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("creative_projects.id"), nullable=True, index=True),
            sa.Column("kind", sa.String(length=30), nullable=False),
            sa.Column("provider", sa.String(length=50), nullable=True),
            sa.Column("model", sa.String(length=120), nullable=True),
            sa.Column("prompt", sa.Text(), nullable=True),
            sa.Column("negative_prompt", sa.Text(), nullable=True),
            sa.Column("params_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=50), nullable=True),
            sa.Column("progress_pct", sa.Float(), nullable=True),
            sa.Column("message", sa.String(length=255), nullable=True),
            sa.Column("outputs_json", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=60), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("cost_estimate_usd", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_project_versions"):
        op.create_table(
            "creative_project_versions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("creative_projects.id"), nullable=False, index=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("version_no", sa.Integer(), nullable=True),
            sa.Column("label", sa.String(length=255), nullable=True),
            sa.Column("snapshot_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_project_shares"):
        op.create_table(
            "creative_project_shares",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("project_id", sa.String(length=36), sa.ForeignKey("creative_projects.id"), nullable=False, index=True),
            sa.Column("owner_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("shared_with_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True, index=True),
            sa.Column("shared_with_email", sa.String(length=255), nullable=True),
            sa.Column("role", sa.String(length=20), nullable=True),
            sa.Column("token", sa.String(length=64), nullable=True, unique=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_templates"):
        op.create_table(
            "creative_templates",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True, index=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("category", sa.String(length=100), nullable=True),
            sa.Column("kind", sa.String(length=30), nullable=True),
            sa.Column("aspect_ratio", sa.String(length=16), nullable=True),
            sa.Column("preview_url", sa.String(length=512), nullable=True),
            sa.Column("tags_json", sa.JSON(), nullable=True),
            sa.Column("is_public", sa.Boolean(), nullable=True),
            sa.Column("template_data_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    if not _has_table("creative_effect_presets"):
        op.create_table(
            "creative_effect_presets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("kind", sa.String(length=30), nullable=True),
            sa.Column("category", sa.String(length=60), nullable=True),
            sa.Column("params_json", sa.JSON(), nullable=True),
            sa.Column("is_builtin", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    for name in (
        "creative_effect_presets",
        "creative_templates",
        "creative_project_shares",
        "creative_project_versions",
        "creative_generation_jobs",
        "creative_transcript_segments",
        "creative_render_jobs",
        "creative_timeline_clips",
        "creative_timeline_tracks",
        "media_assets",
        "creative_projects",
        "creative_brand_kits",
    ):
        if _has_table(name):
            op.drop_table(name)
