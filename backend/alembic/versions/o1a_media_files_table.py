"""create media_files table for chat attachments (spec §42-43)

The MediaFile model (app/models/media.py) was defined and used by
app/api/media.py and app/tools/media_tools.py, but was never imported in
app/models/__init__.py and no migration created its table. This revision
creates the media_files table so image/audio/video uploads work in
Alembic-managed databases.

Revision ID: o1a_media_files_table
Revises: p1a_creative_studio
Create Date: 2026-10-17 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "o1a_media_files_table"
down_revision: Union[str, Sequence[str], None] = "p1a_creative_studio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Upgrade schema."""
    if _has_table("media_files"):
        return
    op.create_table(
        "media_files",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="image"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_name", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    if not _has_table("media_files"):
        return
    op.drop_table("media_files")
