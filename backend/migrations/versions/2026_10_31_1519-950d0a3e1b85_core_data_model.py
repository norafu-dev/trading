"""core data model

Revision ID: 950d0a3e1b85
Revises: 0b91396a7850
Create Date: 2026-10-31 15:19:56.360713

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "950d0a3e1b85"
down_revision: str | Sequence[str] | None = "0b91396a7850"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "discord_account",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("token_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("token_key_version", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.Text(), server_default="connecting", nullable=False),
        sa.Column(
            "status_changed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_heartbeat_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('connecting', 'online', 'reconnecting', 'token_invalid', 'banned', "
            "'disabled')",
            name=op.f("ck_discord_account_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discord_account")),
        sa.UniqueConstraint("discord_user_id", name=op.f("uq_discord_account_discord_user_id")),
    )
    op.create_table(
        "discord_user",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("global_name", sa.Text(), nullable=True),
        sa.Column("avatar_hash", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('user', 'bot', 'webhook')", name=op.f("ck_discord_user_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_discord_user")),
    )
    op.create_table(
        "guild",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("icon_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "status_changed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'left', 'deleted')", name=op.f("ck_guild_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guild")),
    )
    op.create_table(
        "channel",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("type", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("collect_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("collector_account_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "status_changed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')", name=op.f("ck_channel_status")
        ),
        sa.CheckConstraint(
            "NOT collect_enabled OR type IN (0, 5, 10, 11, 12)",
            name=op.f("ck_channel_collectable_type"),
        ),
        sa.ForeignKeyConstraint(
            ["collector_account_id"],
            ["discord_account.id"],
            name=op.f("fk_channel_collector_account_id_discord_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["guild_id"], ["guild.id"], name=op.f("fk_channel_guild_id_guild")),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["channel.id"], name=op.f("fk_channel_parent_id_channel")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel")),
        sa.UniqueConstraint("id", "guild_id", name="uq_channel_id_guild_id"),
    )
    op.create_index(
        op.f("ix_channel_collector_account_id"), "channel", ["collector_account_id"], unique=False
    )
    op.create_index(op.f("ix_channel_guild_id"), "channel", ["guild_id"], unique=False)
    op.create_index(op.f("ix_channel_parent_id"), "channel", ["parent_id"], unique=False)
    op.create_table(
        "account_channel_access",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("can_read", sa.Boolean(), nullable=False),
        sa.Column(
            "checked_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["discord_account.id"],
            name=op.f("fk_account_channel_access_account_id_discord_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channel.id"],
            name=op.f("fk_account_channel_access_channel_id_channel"),
        ),
        sa.PrimaryKeyConstraint("account_id", "channel_id", name=op.f("pk_account_channel_access")),
    )
    op.create_index(
        op.f("ix_account_channel_access_channel_id"),
        "account_channel_access",
        ["channel_id"],
        unique=False,
    )
    op.create_table(
        "channel_coverage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("span", postgresql.TSTZRANGE(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('realtime', 'history')", name=op.f("ck_channel_coverage_source")
        ),
        sa.CheckConstraint("NOT isempty(span)", name=op.f("ck_channel_coverage_span_not_empty")),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["channel.id"], name=op.f("fk_channel_coverage_channel_id_channel")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_coverage")),
    )
    op.create_index(
        op.f("ix_channel_coverage_channel_id"), "channel_coverage", ["channel_id"], unique=False
    )
    op.create_index(
        "uq_channel_coverage_open_realtime",
        "channel_coverage",
        ["channel_id"],
        unique=True,
        postgresql_where=sa.text("source = 'realtime' AND upper_inf(span)"),
    )
    op.create_table(
        "message",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("author_display_name", sa.Text(), nullable=False),
        sa.Column("type", sa.SmallInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "embeds", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "forwarded", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("reply_to_id", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("edited_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("delete_source", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "delete_source IN ('realtime', 'reconciled')", name=op.f("ck_message_delete_source")
        ),
        sa.CheckConstraint("source IN ('realtime', 'history')", name=op.f("ck_message_source")),
        sa.CheckConstraint(
            "(deleted_at IS NULL) = (delete_source IS NULL)", name=op.f("ck_message_deleted_pair")
        ),
        sa.CheckConstraint("type IN (0, 19)", name=op.f("ck_message_type")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_message_version_positive")),
        sa.ForeignKeyConstraint(
            ["author_id"], ["discord_user.id"], name=op.f("fk_message_author_id_discord_user")
        ),
        sa.ForeignKeyConstraint(
            ["channel_id", "guild_id"],
            ["channel.id", "channel.guild_id"],
            name="fk_message_channel_id_guild_id_channel",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message")),
    )
    op.create_index(
        "ix_message_author_id_id",
        "message",
        ["author_id", sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_message_channel_id_id",
        "message",
        ["channel_id", sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_message_guild_id_id",
        "message",
        ["guild_id", sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_table(
        "attachment",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["message_id"], ["message.id"], name=op.f("fk_attachment_message_id_message")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attachment")),
    )
    op.create_index(op.f("ix_attachment_message_id"), "attachment", ["message_id"], unique=False)
    op.create_table(
        "message_revision",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "embeds", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("replaced_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["message.id"], name=op.f("fk_message_revision_message_id_message")
        ),
        sa.PrimaryKeyConstraint("message_id", "version", name=op.f("pk_message_revision")),
    )


def downgrade() -> None:
    """Drop tables in reverse foreign key order."""
    op.drop_table("message_revision")
    op.drop_index(op.f("ix_attachment_message_id"), table_name="attachment")
    op.drop_table("attachment")
    op.drop_index("ix_message_guild_id_id", table_name="message")
    op.drop_index("ix_message_channel_id_id", table_name="message")
    op.drop_index("ix_message_author_id_id", table_name="message")
    op.drop_table("message")
    op.drop_index(
        "uq_channel_coverage_open_realtime",
        table_name="channel_coverage",
        postgresql_where=sa.text("source = 'realtime' AND upper_inf(span)"),
    )
    op.drop_index(op.f("ix_channel_coverage_channel_id"), table_name="channel_coverage")
    op.drop_table("channel_coverage")
    op.drop_index(op.f("ix_account_channel_access_channel_id"), table_name="account_channel_access")
    op.drop_table("account_channel_access")
    op.drop_index(op.f("ix_channel_parent_id"), table_name="channel")
    op.drop_index(op.f("ix_channel_guild_id"), table_name="channel")
    op.drop_index(op.f("ix_channel_collector_account_id"), table_name="channel")
    op.drop_table("channel")
    op.drop_table("guild")
    op.drop_table("discord_user")
    op.drop_table("discord_account")
