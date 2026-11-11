"""Add user-approved local Windows app registrations."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "v1c_local_device_apps"
down_revision: Union[str, Sequence[str], None] = "v1b_device_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_device_apps",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("executable_path", sa.String(length=1024), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "executable_path", name="uq_local_app_user_path"),
    )
    op.create_index("ix_local_device_apps_user_id", "local_device_apps", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_local_device_apps_user_id", table_name="local_device_apps")
    op.drop_table("local_device_apps")
