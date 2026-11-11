"""Merge professional-profile and calendar migration heads."""

from typing import Sequence, Union

from alembic import op


revision: str = "v1a_merge_people_profiles_calendar_heads"
down_revision: Union[str, Sequence[str], None] = (
    "t1a_calendar_event_timezone",
    "u1a_professional_profiles",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
