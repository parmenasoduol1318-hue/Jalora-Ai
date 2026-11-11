"""Store the intended IANA timezone for calendar events."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "t1a_calendar_event_timezone"
down_revision: Union[str, Sequence[str], None] = "s1a_conversation_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("calendar_events")}
    if "timezone" not in columns:
        with op.batch_alter_table("calendar_events") as batch:
            batch.add_column(sa.Column(
                "timezone", sa.String(length=64), nullable=False,
                server_default="Africa/Nairobi"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("calendar_events")}
    if "timezone" in columns:
        with op.batch_alter_table("calendar_events") as batch:
            batch.drop_column("timezone")
