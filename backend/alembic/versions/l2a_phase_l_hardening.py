"""Phase L: refresh tokens, audit completeness, global rate limiting (§58, §74, §75)

Revision ID: l2a_phase_l_hardening
Revises: k1f2e3d4c5b6
Create Date: 2026-10-10
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "l2a_phase_l_hardening"
down_revision = "k1f2e3d4c5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- refresh_tokens table (server-side revocation, hashed at rest) ----------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("token_hash", sa.Text(), nullable=False, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    # -- audit completeness: provider + error_code on activity_logs (§75) ------
    op.add_column("activity_logs", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("activity_logs", sa.Column("error_code", sa.String(length=32), nullable=True))
    op.create_index("ix_activity_logs_provider", "activity_logs", ["provider"])
    op.create_index("ix_activity_logs_error_code", "activity_logs", ["error_code"])


def downgrade() -> None:
    op.drop_index("ix_activity_logs_error_code", table_name="activity_logs")
    op.drop_index("ix_activity_logs_provider", table_name="activity_logs")
    op.drop_column("activity_logs", "error_code")
    op.drop_column("activity_logs", "provider")
    op.drop_table("refresh_tokens")
