"""Add opt-in professional profiles.

Revision ID: u1a_professional_profiles
Revises: 70775023654b
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "u1a_professional_profiles"
down_revision: Union[str, Sequence[str], None] = "70775023654b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "professional_profiles",
        sa.Column(
            "user_id",
            sa.String(length=32),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("courses", sa.JSON(), nullable=False),
        sa.Column("qualifications", sa.JSON(), nullable=False),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=False),
        sa.Column("discoverable", sa.Boolean(), nullable=False),
        sa.Column("share_email", sa.Boolean(), nullable=False),
        sa.Column("share_phone", sa.Boolean(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_professional_profiles_discoverable",
        "professional_profiles",
        ["discoverable"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_professional_profiles_discoverable",
        table_name="professional_profiles",
    )
    op.drop_table("professional_profiles")
