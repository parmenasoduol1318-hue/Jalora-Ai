"""Add persistent, owner-scoped idea projects."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "v1l_idea_projects"
down_revision: str | Sequence[str] | None = "v1k_windows_companion_manifest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "idea_projects",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("goals", sa.Text(), nullable=False),
        sa.Column("next_steps", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("milestones", sa.JSON(), nullable=False),
        sa.Column("task_ids", sa.JSON(), nullable=False),
        sa.Column("conversation_ids", sa.JSON(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_idea_projects_owner_id"), "idea_projects", ["owner_id"], unique=False
    )
    op.create_index(
        op.f("ix_idea_projects_archived_at"),
        "idea_projects",
        ["archived_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_idea_projects_archived_at"), table_name="idea_projects")
    op.drop_index(op.f("ix_idea_projects_owner_id"), table_name="idea_projects")
    op.drop_table("idea_projects")
