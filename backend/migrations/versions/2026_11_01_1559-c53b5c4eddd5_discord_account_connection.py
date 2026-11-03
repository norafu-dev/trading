"""discord account connection

Revision ID: c53b5c4eddd5
Revises: a0e104f51d41
Create Date: 2026-11-01 15:59:57.969075

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c53b5c4eddd5"
down_revision: str | Sequence[str] | None = "a0e104f51d41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_STATUSES = "'connecting', 'online', 'reconnecting', 'token_invalid', 'banned', 'disabled'"
_NEW_STATUSES = f"{_OLD_STATUSES}, 'verification_required', 'login_paused', 'account_mismatch'"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("discord_account", sa.Column("discord_username", sa.Text(), nullable=True))
    op.add_column(
        "discord_account", sa.Column("retry_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "discord_account",
        sa.Column("config_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.drop_constraint(op.f("ck_discord_account_status"), "discord_account", type_="check")
    op.create_check_constraint(
        op.f("ck_discord_account_status"), "discord_account", f"status IN ({_NEW_STATUSES})"
    )
    op.create_check_constraint(
        op.f("ck_discord_account_retry_at_pair"),
        "discord_account",
        "(status = 'login_paused') = (retry_at IS NOT NULL)",
    )
    op.create_index(
        "uq_discord_account_single", "discord_account", [sa.text("(true)")], unique=True
    )
    op.create_table(
        "discord_login_attempt",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "attempted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["discord_account.id"],
            name=op.f("fk_discord_login_attempt_account_id_discord_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discord_login_attempt")),
    )
    op.create_index(
        "ix_discord_login_attempt_account_id_attempted_at",
        "discord_login_attempt",
        ["account_id", "attempted_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_discord_login_attempt_account_id_attempted_at", table_name="discord_login_attempt"
    )
    op.drop_table("discord_login_attempt")
    op.drop_index("uq_discord_account_single", table_name="discord_account")
    op.drop_constraint(op.f("ck_discord_account_retry_at_pair"), "discord_account", type_="check")
    op.drop_constraint(op.f("ck_discord_account_status"), "discord_account", type_="check")
    # 新状态的行要先归到旧状态，否则旧 CHECK 加不回去。
    op.execute(
        "UPDATE discord_account SET status = 'connecting' "
        "WHERE status IN ('verification_required', 'login_paused', 'account_mismatch')"
    )
    op.create_check_constraint(
        op.f("ck_discord_account_status"), "discord_account", f"status IN ({_OLD_STATUSES})"
    )
    op.drop_column("discord_account", "config_version")
    op.drop_column("discord_account", "retry_at")
    op.drop_column("discord_account", "discord_username")
