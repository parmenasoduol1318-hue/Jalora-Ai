"""add contacts, calendar_events and drive_files (Phase I)

Revision ID: e5b7c9d1f3a5
Revises: d1e4f8a2c6b3
Create Date: 2026-10-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "e5b7c9d1f3a5"
down_revision = "d1e4f8a2c6b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("name", sa.String(length=255), nullable=False, index=True),
        sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("company", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="local"),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("attendees", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="mock"),
        sa.Column("external_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="confirmed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "drive_files",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("name", sa.String(length=500), nullable=False, index=True),
        sa.Column("mime_type", sa.String(length=150), nullable=False, server_default="text/plain"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("storage_path", sa.String(length=1000), nullable=False, default=""),
        sa.Column("parent_id", sa.String(length=255), nullable=False, default=""),
        sa.Column("provider", sa.String(length=20), nullable=False, default="mock"),
        sa.Column("external_id", sa.String(length=255), nullable=False, default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("drive_files")
    op.drop_table("calendar_events")
    op.drop_table("contacts")
