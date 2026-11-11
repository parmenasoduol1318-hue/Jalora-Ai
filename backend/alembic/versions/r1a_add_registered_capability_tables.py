"""Create ORM tables that were registered without migrations.

Several capability services shipped with ORM models but no corresponding
Alembic revision.  That made the services appear available while failing at
runtime with ``no such table`` errors.  This revision creates only the
previously missing tables from the registered metadata, leaving existing
tables and user data untouched.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "r1a_registered_capability_tables"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = (
    "faqs",
    "kb_articles",
    "support_templates",
    "task_deadlines",
    "task_preferences",
    "business_plans",
    "comparison_rankings",
    "cross_app_workflows",
    "fact_verifications",
    "feedback_records",
    "form_templates",
    "meeting_preps",
    "support_tickets",
    "user_preferences",
    "market_analyses",
    "financial_projections",
    "risk_assessments",
    "milestones",
    "form_data",
    "evidence_sources",
    "meeting_agenda_items",
    "meeting_minutes",
    "workflow_execution_records",
    "workflow_step_results",
)


def upgrade() -> None:
    # Importing the model package registers all mapped tables on Base.metadata.
    # Using create_all(checkfirst=True) keeps this revision idempotent for
    # databases that received some of these tables through an earlier deploy.
    from app.database.base import Base
    from app import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[Base.metadata.tables[name] for name in _TABLES],
        checkfirst=True,
    )


def downgrade() -> None:
    from alembic import op

    bind = op.get_bind()
    for name in reversed(_TABLES):
        op.drop_table(name, if_exists=True)
