"""Track command polling independently from device heartbeats."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v1m_device_command_poll_health"
down_revision: str | Sequence[str] | None = "v1l_idea_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_connections",
        sa.Column("last_command_poll_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_connections", "last_command_poll_at")
