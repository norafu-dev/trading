"""thread load request

Revision ID: 7d2e8f1a4b6c
Revises: 64f4a92e67c9
Create Date: 2026-11-02 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d2e8f1a4b6c"
down_revision: str | Sequence[str] | None = "64f4a92e67c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "guild",
        sa.Column("thread_load_requested_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("guild", "thread_load_requested_at")
