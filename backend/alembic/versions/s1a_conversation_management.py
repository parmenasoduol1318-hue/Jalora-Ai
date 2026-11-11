"""Add archive state to conversations for persistent chat management."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s1a_conversation_management"
down_revision: Union[str, Sequence[str], None] = "r1a_registered_capability_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "archived" not in columns:
        with op.batch_alter_table("conversations") as batch:
            batch.add_column(sa.Column("archived", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
            batch.create_index("ix_conversations_archived", ["archived"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "archived" in columns:
        with op.batch_alter_table("conversations") as batch:
            batch.drop_index("ix_conversations_archived")
            batch.drop_column("archived")
