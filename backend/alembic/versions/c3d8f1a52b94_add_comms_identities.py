"""add comms identities

Revision ID: c3d8f1a52b94
Revises: 9ce2119c629c
Create Date: 2026-10-07 17:45:12.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d8f1a52b94'
down_revision: Union[str, Sequence[str], None] = '9ce2119c629c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'comms_identities',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('address', sa.String(length=32), nullable=False),
        sa.Column('conversation_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('channel', 'address', name='uq_comms_channel_address'),
    )
    with op.batch_alter_table('comms_identities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_comms_identities_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('comms_identities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_comms_identities_user_id'))

    op.drop_table('comms_identities')
