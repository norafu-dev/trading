"""Feature 3 tables (spec 0002). Later features add tables in additive migrations only.

Every CHECK and every composite or partial index carries an explicit `name=` so the
model and the migration always agree under `NAMING_CONVENTION`.
"""

from datetime import datetime
from typing import get_args

from sqlalchemy import (
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
    ChannelStatus,
    CoverageSource,
    DeleteSource,
    EmbedData,
    ForwardedData,
    GuildStatus,
    LoginOutcome,
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
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    __table_args__ = (CheckConstraint(_in("status", get_args(AccountStatus)), name="status"),)


class Guild(Base):
    __tablename__ = "guild"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str]
    icon_hash: Mapped[str | None]
    status: Mapped[GuildStatus] = mapped_column(Text, server_default="active")
    status_changed_at: Mapped[datetime] = _now()
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
