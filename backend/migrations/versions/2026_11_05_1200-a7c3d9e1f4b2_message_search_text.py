"""message search text

Revision ID: a7c3d9e1f4b2
Revises: e2b6b15de00c
Create Date: 2026-11-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3d9e1f4b2"
down_revision: str | Sequence[str] | None = "e2b6b15de00c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 以后修改这个函数的定义，必须在同一个迁移里重建 ix_message_search_bigrams。
_BIGRAMS_FUNCTION = """
CREATE FUNCTION harvester_bigrams(t text) RETURNS text[]
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT coalesce(array_agg(DISTINCT substr(t, i, 2)), '{}'::text[])
    FROM generate_series(1, char_length(t) - 1) AS i
$$
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(_BIGRAMS_FUNCTION)
    op.add_column("message", sa.Column("search_text", sa.Text(), nullable=True))
    # 现在数据量小，普通 CREATE INDEX 就行；以后重建要用 CONCURRENTLY。
    op.create_index(
        "ix_message_search_bigrams",
        "message",
        [sa.text("harvester_bigrams(search_text)")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_message_search_pending",
        "message",
        [sa.text("id DESC")],
        postgresql_where=sa.text("search_text IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_message_search_pending", table_name="message")
    op.drop_index("ix_message_search_bigrams", table_name="message")
    op.drop_column("message", "search_text")
    op.execute("DROP FUNCTION harvester_bigrams(text)")
