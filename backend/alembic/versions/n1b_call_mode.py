"""add call mode (script|live) for Twilio Media Streams conversations

Revision ID: n1b_call_mode
Revises: n1a_call_kind
Create Date: 2026-10-13 09:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n1b_call_mode'
down_revision: Union[str, Sequence[str], None] = 'n1a_call_kind'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'mode', sa.String(length=8), nullable=False,
                server_default='script',
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.drop_column('mode')
