"""channel management

Revision ID: 64f4a92e67c9
Revises: c53b5c4eddd5
Create Date: 2026-11-01 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "64f4a92e67c9"
down_revision: str | Sequence[str] | None = "c53b5c4eddd5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "discord_account",
        sa.Column("channels_synced_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("discord_account", "channels_synced_at")
