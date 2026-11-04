"""attachment media storage

Revision ID: dd45eddae24d
Revises: 3c5a9e7b1d2f
Create Date: 2026-11-04 19:46:20.442035

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dd45eddae24d"
down_revision: str | Sequence[str] | None = "3c5a9e7b1d2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 链接里的 `ex` 是十六进制 Unix 秒；没有 `ex` 视为未过期。过期或不到 5 分钟就直接标成需要换链接。
def _needs_refresh(url: str) -> str:
    return (
        f"COALESCE(to_timestamp((('x' || lpad(substring({url} from '[?&]ex=([0-9a-fA-F]{{1,16}})'),"
        f" 16, '0'))::bit(64))::bigint) < now() + interval '5 minutes', false)"
    )


_HOSTED = (
    r"^https://(cdn\.discordapp\.com|media\.discordapp\.net)/(attachments|ephemeral-attachments)/"
)
_DISCORD_ID = r"/[a-z-]*attachments/[0-9]+/([0-9]+)/"

_BACKFILL_ATTACHMENTS = f"""
INSERT INTO media (message_id, kind, attachment_id, discord_attachment_id, filename,
                   declared_content_type, declared_size_bytes, declared_width, declared_height,
                   source_url, needs_refresh)
SELECT a.message_id, 'attachment', a.id, a.id, a.filename, a.content_type, a.size_bytes,
       a.width, a.height, a.url, {_needs_refresh("a.url")}
FROM attachment a
ON CONFLICT DO NOTHING
"""

_BACKFILL_EMBEDS = f"""
WITH e AS (
    SELECT m.id AS message_id, (t.ord - 1)::int AS slot, t.elem
    FROM message m, jsonb_array_elements(m.embeds) WITH ORDINALITY AS t(elem, ord)
    WHERE jsonb_typeof(m.embeds) = 'array'
), u AS (
    SELECT message_id, slot, 'embed_image' AS kind, elem->>'image_url' AS url FROM e
    UNION ALL
    SELECT message_id, slot, 'embed_thumbnail', elem->>'thumbnail_url' FROM e
)
INSERT INTO media (message_id, kind, slot, discord_attachment_id, filename, source_url,
                   needs_refresh)
SELECT u.message_id, u.kind, u.slot,
       (substring(u.url from '{_DISCORD_ID}'))::bigint,
       COALESCE(NULLIF(substring(split_part(u.url, '?', 1) from '[^/]+$'), ''), 'image'),
       u.url, {_needs_refresh("u.url")}
FROM u
WHERE u.url ~ '{_HOSTED}'
ON CONFLICT DO NOTHING
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "media_settings",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column(
            "max_file_bytes", sa.BigInteger(), server_default=sa.text("52428800"), nullable=False
        ),
        sa.Column(
            "quota_bytes", sa.BigInteger(), server_default=sa.text("53687091200"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_media_settings_single_row")),
        sa.CheckConstraint(
            "max_file_bytes BETWEEN 1048576 AND 1073741824",
            name=op.f("ck_media_settings_max_file_bytes_range"),
        ),
        sa.CheckConstraint(
            "quota_bytes BETWEEN 1073741824 AND 10737418240000",
            name=op.f("ck_media_settings_quota_bytes_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_settings")),
    )
    op.create_table(
        "stored_file",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("thumb_object_key", sa.Text(), nullable=True),
        sa.Column("thumb_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("thumb_width", sa.Integer(), nullable=True),
        sa.Column("thumb_height", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            (
                "(thumb_object_key IS NULL AND thumb_size_bytes IS NULL"
                " AND thumb_width IS NULL AND thumb_height IS NULL)"
                " OR (thumb_object_key IS NOT NULL AND thumb_size_bytes IS NOT NULL"
                " AND thumb_width IS NOT NULL AND thumb_height IS NOT NULL)"
            ),
            name=op.f("ck_stored_file_thumb_all_or_none"),
        ),
        sa.CheckConstraint("octet_length(sha256) = 32", name=op.f("ck_stored_file_sha256_length")),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_stored_file_size_not_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stored_file")),
        sa.UniqueConstraint("object_key", name=op.f("uq_stored_file_object_key")),
        sa.UniqueConstraint("prefix", "sha256", name="uq_stored_file_prefix_sha256"),
    )
    op.create_table(
        "media",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("attachment_id", sa.BigInteger(), nullable=True),
        sa.Column("slot", sa.Integer(), nullable=True),
        sa.Column("discord_attachment_id", sa.BigInteger(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("declared_content_type", sa.Text(), nullable=True),
        sa.Column("declared_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("declared_width", sa.Integer(), nullable=True),
        sa.Column("declared_height", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("needs_refresh", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("refreshed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("aborted_at_bytes", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("stored_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            (
                "(kind = 'attachment' AND attachment_id IS NOT NULL AND slot IS NULL)"
                " OR (kind <> 'attachment' AND attachment_id IS NULL AND slot IS NOT NULL"
                " AND slot >= 0)"
            ),
            name=op.f("ck_media_kind_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'stored') = (file_id IS NOT NULL)", name=op.f("ck_media_file_pair")
        ),
        sa.CheckConstraint(
            "kind IN ('attachment', 'forwarded_attachment', 'embed_image', 'embed_thumbnail')",
            name=op.f("ck_media_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'stored', 'failed', 'too_large', 'no_space', 'gone')",
            name=op.f("ck_media_status"),
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"], ["attachment.id"], name=op.f("fk_media_attachment_id_attachment")
        ),
        sa.ForeignKeyConstraint(
            ["file_id"], ["stored_file.id"], name=op.f("fk_media_file_id_stored_file")
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["message.id"], name=op.f("fk_media_message_id_message")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media")),
        sa.UniqueConstraint("attachment_id", name="uq_media_attachment"),
    )
    op.create_index("ix_media_file", "media", ["file_id"], unique=False)
    op.create_index("ix_media_message", "media", ["message_id"], unique=False)
    op.create_index(
        "ix_media_problem",
        "media",
        ["status"],
        unique=False,
        postgresql_where=sa.text("status IN ('too_large', 'no_space', 'failed')"),
    )
    op.create_index(
        "ix_media_queue",
        "media",
        [sa.literal_column("message_id DESC")],
        unique=False,
        postgresql_where=sa.text("status = 'pending' AND claimed_at IS NULL AND NOT needs_refresh"),
    )
    op.create_index(
        "ix_media_refresh",
        "media",
        [sa.literal_column("message_id DESC")],
        unique=False,
        postgresql_where=sa.text("status = 'pending' AND needs_refresh"),
    )
    op.create_index(
        "uq_media_message_slot",
        "media",
        ["message_id", "kind", "slot"],
        unique=True,
        postgresql_where=sa.text("kind <> 'attachment'"),
    )
    op.add_column(
        "collector_heartbeat",
        sa.Column("media_worker_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "collector_heartbeat",
        sa.Column("media_paused_until", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute("INSERT INTO media_settings (id) VALUES (1)")
    op.execute(_BACKFILL_ATTACHMENTS)
    op.execute(_BACKFILL_EMBEDS)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("collector_heartbeat", "media_paused_until")
    op.drop_column("collector_heartbeat", "media_worker_seen_at")
    op.drop_index(
        "uq_media_message_slot",
        table_name="media",
        postgresql_where=sa.text("kind <> 'attachment'"),
    )
    op.drop_index(
        "ix_media_refresh",
        table_name="media",
        postgresql_where=sa.text("status = 'pending' AND needs_refresh"),
    )
    op.drop_index(
        "ix_media_queue",
        table_name="media",
        postgresql_where=sa.text("status = 'pending' AND claimed_at IS NULL AND NOT needs_refresh"),
    )
    op.drop_index(
        "ix_media_problem",
        table_name="media",
        postgresql_where=sa.text("status IN ('too_large', 'no_space', 'failed')"),
    )
    op.drop_index("ix_media_message", table_name="media")
    op.drop_index("ix_media_file", table_name="media")
    op.drop_table("media")
    op.drop_table("stored_file")
    op.drop_table("media_settings")
