"""Generalize target-approved YouTube links to public browser URLs."""

from typing import Sequence, Union
from urllib.parse import parse_qs, urlencode, urlsplit

import sqlalchemy as sa
from alembic import op


revision: str = "v1e_remote_browser_commands"
down_revision: Union[str, Sequence[str], None] = "v1d_remote_device_companion"
branch_labels = None
depends_on = None

OLD_SCOPE = "remote.youtube_search"
NEW_SCOPE = "remote.browser.open_url"


def _update_scopes(old_scope: str, new_scope: str) -> None:
    connection = op.get_bind()
    for table_name in ("device_connections", "device_pairing_requests"):
        table = sa.Table(
            table_name,
            sa.MetaData(),
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("scopes", sa.JSON()),
        )
        rows = connection.execute(
            sa.select(table.c.id, table.c.scopes)
        ).mappings().all()
        for row in rows:
            scopes = list(row["scopes"] or [])
            if old_scope in scopes:
                scopes = [new_scope if scope == old_scope else scope for scope in scopes]
                connection.execute(
                    sa.update(table)
                    .where(table.c.id == row["id"])
                    .values(scopes=scopes)
                )


def upgrade() -> None:
    op.alter_column(
        "device_remote_commands",
        "query",
        new_column_name="target_url",
        existing_type=sa.Text(),
        existing_nullable=False,
    )
    commands = sa.Table(
        "device_remote_commands",
        sa.MetaData(),
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("action", sa.String(length=32)),
        sa.Column("target_url", sa.Text()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(commands.c.id, commands.c.action, commands.c.target_url)
    ).mappings().all()
    for row in rows:
        if row["action"] != "youtube_search":
            continue
        url = "https://www.youtube.com/results?" + urlencode(
            {"search_query": row["target_url"]}
        )
        connection.execute(
            sa.update(commands)
            .where(commands.c.id == row["id"])
            .values(action="browser.open_url", target_url=url)
        )
    _update_scopes(OLD_SCOPE, NEW_SCOPE)


def downgrade() -> None:
    commands = sa.Table(
        "device_remote_commands",
        sa.MetaData(),
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("action", sa.String(length=32)),
        sa.Column("target_url", sa.Text()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(commands.c.id, commands.c.action, commands.c.target_url)
    ).mappings().all()
    for row in rows:
        if row["action"] != "browser.open_url":
            continue
        parsed = urlsplit(row["target_url"])
        query = parse_qs(parsed.query).get("search_query", [])
        if parsed.hostname != "www.youtube.com" or parsed.path != "/results" or not query:
            raise RuntimeError(
                "Cannot downgrade while non-YouTube remote link commands exist."
            )
        connection.execute(
            sa.update(commands)
            .where(commands.c.id == row["id"])
            .values(action="youtube_search", target_url=query[0])
        )
    _update_scopes(NEW_SCOPE, OLD_SCOPE)
    op.alter_column(
        "device_remote_commands",
        "target_url",
        new_column_name="query",
        existing_type=sa.Text(),
        existing_nullable=False,
    )
