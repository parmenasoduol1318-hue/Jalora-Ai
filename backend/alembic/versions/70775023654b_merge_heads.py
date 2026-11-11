"""merge heads

Revision ID: 70775023654b
Revises: j1b_job_search_and_application_tables, o1a_media_files_table
Create Date: 2026-10-20 20:10:15.484605

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70775023654b'
down_revision: Union[str, Sequence[str], None] = ('j1b_job_search_and_application_tables', 'o1a_media_files_table')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
