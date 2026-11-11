"""add integrations and autoreply tables

Revision ID: e2f4a6c8d0b1
Revises: c3d8f1a52b94
Create Date: 2026-10-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f4a6c8d0b1'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'integrations',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'provider', name='uq_integration_user_provider'),
    )
    with op.batch_alter_table('integrations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_integrations_user_id'), ['user_id'], unique=False)

    op.create_table(
        'auto_reply_rules',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('sender_pattern', sa.String(length=255), nullable=False),
        sa.Column('topic_pattern', sa.String(length=255), nullable=False),
        sa.Column('time_window_start', sa.Integer(), nullable=False),
        sa.Column('time_window_end', sa.Integer(), nullable=False),
        sa.Column('tone', sa.String(length=16), nullable=False),
        sa.Column('template', sa.Text(), nullable=False),
        sa.Column('max_per_hour', sa.Integer(), nullable=False),
        sa.Column('max_per_day', sa.Integer(), nullable=False),
        sa.Column('requires_approval', sa.Boolean(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('last_hour_bucket', sa.DateTime(timezone=True), nullable=True),
        sa.Column('hourly_count', sa.Integer(), nullable=False),
        sa.Column('last_day', sa.String(length=10), nullable=True),
        sa.Column('daily_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('auto_reply_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_auto_reply_rules_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('auto_reply_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_auto_reply_rules_user_id'))

    op.drop_table('auto_reply_rules')

    with op.batch_alter_table('integrations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_integrations_user_id'))

    op.drop_table('integrations')