"""Link approval decisions to their planned execution step."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "v1f_approval_plan_step"
down_revision: Union[str, Sequence[str], None] = "v1e_remote_browser_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column("plan_step_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approvals", "plan_step_id")
