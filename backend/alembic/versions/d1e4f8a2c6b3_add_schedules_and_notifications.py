"""add schedules and notifications tables

Revision ID: d1e4f8a2c6b3
Revises: f3a9c7d5e6f1
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1e4f8a2c6b3'
down_revision: Union[str, Sequence[str], None] = 'f3a9c7d5e6f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'schedules',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('spec', sa.JSON(), nullable=False),
        sa.Column('task_template', sa.Text(), nullable=False),
        sa.Column('notify_only', sa.Boolean(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('next_run', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_schedules_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_schedules_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_schedules_enabled'), ['enabled'], unique=False)

    op.create_table(
        'notifications',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('link', sa.String(length=255), nullable=True),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('task_id', sa.String(length=32), nullable=True),
        sa.Column('schedule_id', sa.String(length=32), nullable=True),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_read_at'), ['read_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_created_at'))
        batch_op.drop_index(batch_op.f('ix_notifications_read_at'))
        batch_op.drop_index(batch_op.f('ix_notifications_run_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_kind'))
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))

    op.drop_table('notifications')

    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_schedules_enabled'))
        batch_op.drop_index(batch_op.f('ix_schedules_kind'))
        batch_op.drop_index(batch_op.f('ix_schedules_user_id'))

    op.drop_table('schedules')