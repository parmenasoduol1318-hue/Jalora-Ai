"""add task step columns for dependencies, tool, arguments

Revision ID: f2a3b4c5d6e7
Revises: q1a
Create Date: 2026-10-22 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'q1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    if not inspect(op.get_bind()).has_table("task_steps"):
        return
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("task_steps")}
    with op.batch_alter_table('task_steps', schema=None) as batch_op:
        if "dependencies" not in columns:
            batch_op.add_column(sa.Column('dependencies', sa.JSON(), nullable=True))
        if "tool_id" not in columns:
            batch_op.add_column(sa.Column('tool_id', sa.String(length=128), nullable=True))
        if "arguments" not in columns:
            batch_op.add_column(sa.Column('arguments', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    if not inspect(op.get_bind()).has_table("task_steps"):
        return
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("task_steps")}
    with op.batch_alter_table('task_steps', schema=None) as batch_op:
        if "arguments" in columns:
            batch_op.drop_column('arguments')
        if "tool_id" in columns:
            batch_op.drop_column('tool_id')
        if "dependencies" in columns:
            batch_op.drop_column('dependencies')
