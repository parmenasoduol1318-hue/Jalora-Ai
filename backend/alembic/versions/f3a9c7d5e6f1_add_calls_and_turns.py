"""add calls and call turns tables

Revision ID: f3a9c7d5e6f1
Revises: e2f4a6c8d0b1
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c7d5e6f1'
down_revision: Union[str, Sequence[str], None] = 'e2f4a6c8d0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'calls',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('task_id', sa.String(length=32), nullable=True),
        sa.Column('conversation_id', sa.String(length=32), nullable=True),
        sa.Column('to_phone', sa.String(length=32), nullable=False),
        sa.Column('from_phone', sa.String(length=32), nullable=True),
        sa.Column('provider', sa.String(length=16), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('sid', sa.String(length=64), nullable=True),
        sa.Column('script', sa.JSON(), nullable=True),
        sa.Column('answers', sa.JSON(), nullable=True),
        sa.Column('transcript', sa.JSON(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('max_turns', sa.Integer(), nullable=False),
        sa.Column('max_duration_seconds', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ),
        sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], ),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_calls_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_calls_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_calls_task_id'), ['task_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_calls_conversation_id'), ['conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_calls_status'), ['status'], unique=False)

    op.create_table(
        'call_turns',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('call_id', sa.String(length=32), nullable=False),
        sa.Column('role', sa.String(length=8), nullable=False),
        sa.Column('kind', sa.String(length=12), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('ts', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('call_turns', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_call_turns_call_id'), ['call_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('call_turns', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_call_turns_call_id'))

    op.drop_table('call_turns')

    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_calls_status'))
        batch_op.drop_index(batch_op.f('ix_calls_conversation_id'))
        batch_op.drop_index(batch_op.f('ix_calls_task_id'))
        batch_op.drop_index(batch_op.f('ix_calls_run_id'))
        batch_op.drop_index(batch_op.f('ix_calls_user_id'))

    op.drop_table('calls')