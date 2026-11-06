"""channel read state

Revision ID: 3c5a9e7b1d2f
Revises: 263fe4d3e2e2
Create Date: 2026-11-04 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c5a9e7b1d2f"
down_revision: str | Sequence[str] | None = "263fe4d3e2e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "channel_read_state",
        sa.Column("channel_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("last_read_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channel.id"], name=op.f("fk_channel_read_state_channel_id_channel")
        ),
        sa.PrimaryKeyConstraint("channel_id", name=op.f("pk_channel_read_state")),
    )
    # 回填：已开启采集或已有消息的频道，已读位置是当前最新消息，上线那一刻没有历史未读。
    op.execute(
        """
        INSERT INTO channel_read_state (channel_id, last_read_message_id)
        SELECT c.id, COALESCE(MAX(m.id), 0)
        FROM channel c LEFT JOIN message m ON m.channel_id = c.id
        GROUP BY c.id
        HAVING c.collect_enabled OR MAX(m.id) IS NOT NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("channel_read_state")
