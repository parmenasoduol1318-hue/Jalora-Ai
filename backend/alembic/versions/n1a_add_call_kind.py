"""add call kind and make to_phone nullable for in-app live calls

Revision ID: n1a_call_kind
Revises: m1a_schema_reconcile
Create Date: 2026-10-13 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n1a_call_kind'
down_revision: Union[str, Sequence[str], None] = 'm1a_schema_reconcile'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'kind', sa.String(length=12), nullable=False,
                server_default='outbound',
            )
        )
        batch_op.alter_column(
            'to_phone', existing_type=sa.String(length=32), nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.alter_column(
            'to_phone', existing_type=sa.String(length=32), nullable=False,
        )
        batch_op.drop_column('kind')
