"""add user credentials table and approvals kind column

Revision ID: a1b2c3d4e5f6
Revises: c3d8f1a52b94
Create Date: 2026-10-08 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c3d8f1a52b94'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'user_credentials',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=False),
        sa.Column('secret', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'domain', 'username',
                            name='uq_cred_user_domain_username'),
    )
    with op.batch_alter_table('user_credentials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_credentials_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=16), nullable=False,
                                      server_default='tool'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.drop_column('kind')

    with op.batch_alter_table('user_credentials', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_credentials_user_id'))

    op.drop_table('user_credentials')