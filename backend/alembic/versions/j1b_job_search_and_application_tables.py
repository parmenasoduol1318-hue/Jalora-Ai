"""create job search and application tables (spec §J)

These tables enable the job search agent functionality:
- job_postings: stores searched and saved job postings
- job_applications: tracks submitted applications
- cv_templates: user's CV/resume templates
- job_qualifications: user's qualifications/skills
- application_drafts: draft application emails

Revision ID: j1b_job_search_and_application_tables
Revises: p1a_creative_studio
Create Date: 2026-10-20 20:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "j1b_job_search_and_application_tables"
down_revision: Union[str, Sequence[str], None] = "p1a_creative_studio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Upgrade schema."""
    # job_postings table
    if not _has_table("job_postings"):
        op.create_table(
            "job_postings",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("company", sa.String(length=255), nullable=False),
            sa.Column("location", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("url", sa.String(length=1024), nullable=False, unique=True, index=True),
            sa.Column("source", sa.String(length=64), nullable=False, server_default="web"),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("requirements", sa.JSON, nullable=True),
            sa.Column("qualifications", sa.JSON, nullable=True),
            sa.Column("salary_range", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("is_remote", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("application_deadline", sa.DateTime(timezone=True), nullable=True),
            sa.Column("applied", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )

    # job_applications table
    if not _has_table("job_applications"):
        op.create_table(
            "job_applications",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
            sa.Column("job_posting_id", sa.String(length=32), nullable=False, index=True),
            sa.Column("company", sa.String(length=255), nullable=False),
            sa.Column("position", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="applied"),
            sa.Column("resume_version", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("cover_letter", sa.Text, nullable=True),
            sa.Column("email_sent_to", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("email_message_id", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("application_date", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("interview_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("response_received", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["job_posting_id"], ["job_postings.id"], ondelete="CASCADE"),
        )

    # cv_templates table
    if not _has_table("cv_templates"):
        op.create_table(
            "cv_templates",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("version", sa.String(length=32), nullable=False, server_default="1"),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )

    # job_qualifications table
    if not _has_table("job_qualifications"):
        op.create_table(
            "job_qualifications",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("years", sa.Integer(), nullable=True),
            sa.Column("is_verified", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )

    # application_drafts table
    if not _has_table("application_drafts"):
        op.create_table(
            "application_drafts",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=32), nullable=False, index=True),
            sa.Column("job_posting_id", sa.String(length=32), nullable=False, index=True),
            sa.Column("subject", sa.String(length=255), nullable=False),
            sa.Column("body", sa.Text, nullable=True),
            sa.Column("cv_content", sa.Text, nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["job_posting_id"], ["job_postings.id"], ondelete="CASCADE"),
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table("application_drafts"):
        op.drop_table("application_drafts")

    if _has_table("job_qualifications"):
        op.drop_table("job_qualifications")

    if _has_table("cv_templates"):
        op.drop_table("cv_templates")

    if _has_table("job_applications"):
        op.drop_table("job_applications")

    if _has_table("job_postings"):
        op.drop_table("job_postings")
