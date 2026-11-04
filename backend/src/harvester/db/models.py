"""Feature 3 tables (spec 0002). Later features add tables in additive migrations only.

Every CHECK and every composite or partial index carries an explicit `name=` so the
model and the migration always agree under `NAMING_CONVENTION`.
"""

from datetime import datetime
from typing import get_args

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TSTZRANGE, Range
from sqlalchemy.orm import Mapped, mapped_column

from harvester.db.base import Base
from harvester.db.snapshots import (
    COLLECTABLE_CHANNEL_TYPES,
    STORED_MESSAGE_TYPES,
    AccountStatus,
    BackupError,
    BackupStatus,
    ChannelStatus,
    CoverageSource,
    DeleteSource,
    EmbedData,
    ForwardedData,
    GuildStatus,
    LoginOutcome,
    MediaKind,
    MediaStatus,
    MessageSource,
    UserKind,
)


def _in(column: str, values: tuple[object, ...]) -> str:
    """SQL for `column IN (...)` from a Literal's values or an int tuple."""
    rendered = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in values)
    return f"{column} IN ({rendered})"


def _now() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


# Embeds are stored as SQL NULL when empty, never JSON 'null' or '[]'.
_JSONB = JSONB(none_as_null=True)


class DiscordAccount(Base):
    __tablename__ = "discord_account"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    discord_user_id: Mapped[int | None] = mapped_column(unique=True)
    label: Mapped[str]
    token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    token_key_version: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[AccountStatus] = mapped_column(Text, server_default="connecting")
    status_changed_at: Mapped[datetime] = _now()
    last_error: Mapped[str | None]
    last_heartbeat_at: Mapped[datetime | None]
    discord_username: Mapped[str | None]
    retry_at: Mapped[datetime | None]
    config_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    # collector 上次把服务器和频道对账写库的时间；换身份时清空（spec 0006）。
    channels_synced_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint(_in("status", get_args(AccountStatus)), name="status"),
        CheckConstraint("(status = 'login_paused') = (retry_at IS NOT NULL)", name="retry_at_pair"),
        # 这一版最多一个账号（spec 0005）；做多账号时用迁移删掉。
        Index("uq_discord_account_single", text("(true)"), unique=True),
    )


class DiscordLoginAttempt(Base):
    __tablename__ = "discord_login_attempt"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("discord_account.id", ondelete="CASCADE"))
    attempted_at: Mapped[datetime] = _now()

    __table_args__ = (
        Index("ix_discord_login_attempt_account_id_attempted_at", "account_id", "attempted_at"),
    )


class Guild(Base):
    __tablename__ = "guild"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str]
    icon_hash: Mapped[str | None]
    status: Mapped[GuildStatus] = mapped_column(Text, server_default="active")
    status_changed_at: Mapped[datetime] = _now()
    # api 点「加载线程」时写入，collector 发出订阅后清空（spec 0008）。
    thread_load_requested_at: Mapped[datetime | None]
    first_seen_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (CheckConstraint(_in("status", get_args(GuildStatus)), name="status"),)


class Channel(Base):
    __tablename__ = "channel"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    guild_id: Mapped[int] = mapped_column(ForeignKey("guild.id"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("channel.id"), index=True)
    type: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str]
    position: Mapped[int | None] = mapped_column(Integer)
    collect_enabled: Mapped[bool] = mapped_column(server_default=text("false"))
    collector_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("discord_account.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[ChannelStatus] = mapped_column(Text, server_default="active")
    status_changed_at: Mapped[datetime] = _now()
    first_seen_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        # Target of the composite foreign key on message (channel_id, guild_id).
        UniqueConstraint("id", "guild_id", name="uq_channel_id_guild_id"),
        CheckConstraint(_in("status", get_args(ChannelStatus)), name="status"),
        CheckConstraint(
            f"NOT collect_enabled OR {_in('type', COLLECTABLE_CHANNEL_TYPES)}",
            name="collectable_type",
        ),
    )


class AccountChannelAccess(Base):
    __tablename__ = "account_channel_access"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("discord_account.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id"), primary_key=True, index=True)
    can_read: Mapped[bool]
    checked_at: Mapped[datetime] = _now()


class DiscordUser(Base):
    __tablename__ = "discord_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    kind: Mapped[UserKind] = mapped_column(Text)
    username: Mapped[str]
    global_name: Mapped[str | None]
    avatar_hash: Mapped[str | None]
    first_seen_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (CheckConstraint(_in("kind", get_args(UserKind)), name="kind"),)


class Message(Base):
    __tablename__ = "message"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    channel_id: Mapped[int]
    guild_id: Mapped[int]
    author_id: Mapped[int] = mapped_column(ForeignKey("discord_user.id"))
    author_display_name: Mapped[str]
    type: Mapped[int] = mapped_column(SmallInteger)
    content: Mapped[str]
    embeds: Mapped[list[EmbedData] | None] = mapped_column(_JSONB)
    forwarded: Mapped[ForwardedData | None] = mapped_column(_JSONB)
    reply_to_id: Mapped[int | None]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime]
    edited_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    delete_source: Mapped[DeleteSource | None] = mapped_column(Text)
    source: Mapped[MessageSource] = mapped_column(Text)
    ingested_at: Mapped[datetime] = _now()

    __table_args__ = (
        ForeignKeyConstraint(
            ["channel_id", "guild_id"],
            ["channel.id", "channel.guild_id"],
            name="fk_message_channel_id_guild_id_channel",
        ),
        CheckConstraint(_in("type", STORED_MESSAGE_TYPES), name="type"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("(deleted_at IS NULL) = (delete_source IS NULL)", name="deleted_pair"),
        CheckConstraint(_in("source", get_args(MessageSource)), name="source"),
        CheckConstraint(_in("delete_source", get_args(DeleteSource)), name="delete_source"),
    )


# Newest first per channel, author and guild (dashboard pagination).
# Query with ORDER BY id DESC: the snowflake ID is the creation time. ORDER BY created_at
# cannot use these indexes and adds a Sort over every matching row.
Index("ix_message_channel_id_id", Message.channel_id, Message.id.desc())
Index("ix_message_author_id_id", Message.author_id, Message.id.desc())
Index("ix_message_guild_id_id", Message.guild_id, Message.id.desc())


class MessageRevision(Base):
    __tablename__ = "message_revision"

    message_id: Mapped[int] = mapped_column(ForeignKey("message.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str]
    embeds: Mapped[list[EmbedData] | None] = mapped_column(_JSONB)
    replaced_at: Mapped[datetime]


class Attachment(Base):
    __tablename__ = "attachment"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("message.id"), index=True)
    filename: Mapped[str]
    content_type: Mapped[str | None]
    size_bytes: Mapped[int]
    url: Mapped[str]
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    removed_at: Mapped[datetime | None]


class ChannelCoverage(Base):
    __tablename__ = "channel_coverage"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channel.id"), index=True)
    span: Mapped[Range[datetime]] = mapped_column(TSTZRANGE)
    source: Mapped[CoverageSource] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint("NOT isempty(span)", name="span_not_empty"),
        CheckConstraint(_in("source", get_args(CoverageSource)), name="source"),
        # At most one open realtime segment per channel.
        Index(
            "uq_channel_coverage_open_realtime",
            "channel_id",
            unique=True,
            postgresql_where=text("source = 'realtime' AND upper_inf(span)"),
        ),
    )


# Feature 5 tables (spec 0004).
class DashboardCredential(Base):
    __tablename__ = "dashboard_credential"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    password_hash: Mapped[str]
    totp_secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    totp_last_step: Mapped[int | None]
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class DashboardSession(Base):
    __tablename__ = "dashboard_session"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    last_seen_at: Mapped[datetime] = _now()
    ip: Mapped[str] = mapped_column(INET)
    user_agent: Mapped[str | None]

    __table_args__ = (CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),)


class LoginEvent(Base):
    __tablename__ = "login_event"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = _now()
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None]
    outcome: Mapped[LoginOutcome] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_in("outcome", get_args(LoginOutcome)), name="outcome"),
        Index("ix_login_event_ip_occurred_at", "ip", text("occurred_at DESC")),
        Index("ix_login_event_occurred_at", text("occurred_at DESC")),
    )


class DashboardSetupCode(Base):
    __tablename__ = "dashboard_setup_code"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    code_hash: Mapped[bytes] = mapped_column(LargeBinary)
    pending_totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = _now()

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


# Feature 9 tables (spec 0009).
class CollectorHeartbeat(Base):
    """collector 进程心跳，最多一行；和账号状态无关。"""

    __tablename__ = "collector_heartbeat"

    id: Mapped[bool] = mapped_column(
        Boolean, primary_key=True, server_default=text("true"), autoincrement=False
    )
    last_seen_at: Mapped[datetime]
    # 下载任务每 30 秒写一次；暂停结束时间在没有暂停时为空（spec 0011）。
    media_worker_seen_at: Mapped[datetime | None]
    media_paused_until: Mapped[datetime | None]

    __table_args__ = (CheckConstraint("id", name="single"),)


class BackupRun(Base):
    """每次备份一条，由 deploy/backup.sh 写入，api 只读。"""

    __tablename__ = "backup_run"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None]
    status: Mapped[BackupStatus] = mapped_column(Text)
    size_bytes: Mapped[int | None]
    object_key: Mapped[str | None]
    error: Mapped[BackupError | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_in("status", get_args(BackupStatus)), name="status"),
        CheckConstraint(_in("error", get_args(BackupError)), name="error"),
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND size_bytes IS NULL"
            " AND object_key IS NULL AND error IS NULL)"
            " OR (status = 'succeeded' AND finished_at IS NOT NULL AND size_bytes IS NOT NULL"
            " AND object_key IS NOT NULL AND error IS NULL)"
            " OR (status = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL"
            " AND size_bytes IS NULL AND object_key IS NULL)",
            name="shape",
        ),
        Index("ix_backup_run_started_at", text("started_at DESC")),
    )


class ChannelReadState(Base):
    """每个频道最多一行已读位置（spec 0010）。没有行等于 0 条未读。"""

    __tablename__ = "channel_read_state"

    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channel.id"), primary_key=True, autoincrement=False
    )
    # 已读位置：消息雪花 ID，比它新的消息算未读；只增不减。
    last_read_message_id: Mapped[int]
    updated_at: Mapped[datetime] = _now()


# Feature 12 tables (spec 0011).
class StoredFile(Base):
    """存储里的一个真实文件，同一频道同一个月里按内容只存一份。"""

    __tablename__ = "stored_file"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    prefix: Mapped[str]
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int]
    content_type: Mapped[str]
    object_key: Mapped[str] = mapped_column(unique=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    thumb_object_key: Mapped[str | None]
    thumb_size_bytes: Mapped[int | None]
    thumb_width: Mapped[int | None] = mapped_column(Integer)
    thumb_height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()

    __table_args__ = (
        UniqueConstraint("prefix", "sha256", name="uq_stored_file_prefix_sha256"),
        CheckConstraint("octet_length(sha256) = 32", name="sha256_length"),
        CheckConstraint("size_bytes >= 0", name="size_not_negative"),
        CheckConstraint(
            "(thumb_object_key IS NULL AND thumb_size_bytes IS NULL"
            " AND thumb_width IS NULL AND thumb_height IS NULL)"
            " OR (thumb_object_key IS NOT NULL AND thumb_size_bytes IS NOT NULL"
            " AND thumb_width IS NOT NULL AND thumb_height IS NOT NULL)",
            name="thumb_all_or_none",
        ),
    )


class Media(Base):
    """每个要下载的附件或 embed 图一行。从不删除，只改状态。"""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("message.id"))
    kind: Mapped[MediaKind] = mapped_column(Text)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachment.id"))
    slot: Mapped[int | None] = mapped_column(Integer)
    discord_attachment_id: Mapped[int | None]
    filename: Mapped[str]
    declared_content_type: Mapped[str | None]
    declared_size_bytes: Mapped[int | None]
    declared_width: Mapped[int | None] = mapped_column(Integer)
    declared_height: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str]
    status: Mapped[MediaStatus] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = _now()
    claimed_at: Mapped[datetime | None]
    needs_refresh: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    refreshed_at: Mapped[datetime | None]
    aborted_at_bytes: Mapped[int | None]
    last_error: Mapped[str | None]
    file_id: Mapped[int | None] = mapped_column(ForeignKey("stored_file.id"))
    created_at: Mapped[datetime] = _now()
    stored_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint(_in("kind", get_args(MediaKind)), name="kind"),
        CheckConstraint(_in("status", get_args(MediaStatus)), name="status"),
        CheckConstraint(
            "(kind = 'attachment' AND attachment_id IS NOT NULL AND slot IS NULL)"
            " OR (kind <> 'attachment' AND attachment_id IS NULL AND slot IS NOT NULL"
            " AND slot >= 0)",
            name="kind_shape",
        ),
        CheckConstraint("(status = 'stored') = (file_id IS NOT NULL)", name="file_pair"),
        UniqueConstraint("attachment_id", name="uq_media_attachment"),
        Index(
            "uq_media_message_slot",
            "message_id",
            "kind",
            "slot",
            unique=True,
            postgresql_where=text("kind <> 'attachment'"),
        ),
        Index(
            "ix_media_queue",
            text("message_id DESC"),
            postgresql_where=text(
                "status = 'pending' AND claimed_at IS NULL AND NOT needs_refresh"
            ),
        ),
        Index(
            "ix_media_refresh",
            text("message_id DESC"),
            postgresql_where=text("status = 'pending' AND needs_refresh"),
        ),
        Index(
            "ix_media_problem",
            "status",
            postgresql_where=text("status IN ('too_large', 'no_space', 'failed')"),
        ),
        Index("ix_media_message", "message_id"),
        Index("ix_media_file", "file_id"),
    )


class MediaSettings(Base):
    """单行：单个文件上限和总容量上限，字节。"""

    __tablename__ = "media_settings"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    max_file_bytes: Mapped[int] = mapped_column(server_default=text("52428800"))
    quota_bytes: Mapped[int] = mapped_column(server_default=text("53687091200"))
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint(
            "max_file_bytes BETWEEN 1048576 AND 1073741824", name="max_file_bytes_range"
        ),
        CheckConstraint(
            "quota_bytes BETWEEN 1073741824 AND 10737418240000", name="quota_bytes_range"
        ),
    )
