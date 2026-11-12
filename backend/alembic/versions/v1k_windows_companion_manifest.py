"""Store the capability manifest reported by a paired Windows companion."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v1k_windows_companion_manifest"
down_revision: str | Sequence[str] | None = "v1j_device_command_protocol"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_connections",
        sa.Column("capability_manifest", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_connections", "capability_manifest")
