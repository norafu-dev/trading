"""频道管理接口（spec 0006）：读库、改开关、发通知，不发任何 Discord 请求。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, status
from pydantic import BaseModel

from harvester.api.errors import ApiError
from harvester.db import SessionFactoryDep
from harvester.db import channels as channel_store
from harvester.db.channels import CategoryCollectError, ChannelRow, CollectError, GuildRow
from harvester.db.snapshots import ChannelStatus, GuildStatus

# 由 routes.py 挂到 protected_router 上，登录检查从那里继承。
channel_router = APIRouter(prefix="/discord", tags=["channels"])

_COLLECT_ERRORS: dict[CollectError, tuple[int, str]] = {
    "channel_not_found": (status.HTTP_404_NOT_FOUND, "频道不存在"),
    "account_not_found": (status.HTTP_409_CONFLICT, "还没有添加账号"),
    "channel_not_collectable": (status.HTTP_409_CONFLICT, "这种类型的频道不能采集"),
    "channel_no_access": (status.HTTP_409_CONFLICT, "小号没有这个频道的读取权限"),
    "channel_unavailable": (status.HTTP_409_CONFLICT, "频道已删除或已退出这个服务器"),
}


# snowflake 超过 JS 的安全整数，路径里也用字符串；超出 bigint 的数一定不存在。
SnowflakeId = Annotated[str, Path(pattern=r"^[0-9]{1,19}$")]
_BIGINT_MAX = 2**63 - 1


def _to_id(raw: str) -> int | None:
    value = int(raw)
    return value if value <= _BIGINT_MAX else None


_CATEGORY_ERRORS: dict[CategoryCollectError, tuple[int, str]] = {
    "category_not_found": (status.HTTP_404_NOT_FOUND, "分类不存在"),
    "not_a_category": (status.HTTP_409_CONFLICT, "这个频道不是分类"),
    "account_not_found": (status.HTTP_409_CONFLICT, "还没有添加账号"),
}


class CategoryCollectFailedError(ApiError):
    def __init__(self, code: CategoryCollectError) -> None:
        http_status, message = _CATEGORY_ERRORS[code]
        super().__init__(http_status, code, message)


class GuildNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, "guild_not_found", "服务器不存在")


class CollectFailedError(ApiError):
    def __init__(self, code: CollectError) -> None:
        http_status, message = _COLLECT_ERRORS[code]
        super().__init__(http_status, code, message)


class GuildOut(BaseModel):
    # snowflake 超过 JS 的安全整数，一律用字符串。
    id: str
    name: str
    icon_hash: str | None
    status: GuildStatus
    enabled_count: int


class GuildsOut(BaseModel):
    synced_at: datetime | None
    guilds: list[GuildOut]


class ChannelOut(BaseModel):
    id: str
    guild_id: str
    parent_id: str | None
    type: int
    name: str
    position: int | None
    status: ChannelStatus
    collect_enabled: bool
    collectable: bool
    can_read: bool


class ChannelsOut(BaseModel):
    channels: list[ChannelOut]


class GuildBriefOut(BaseModel):
    id: str
    name: str
    icon_hash: str | None


class CollectedOut(BaseModel):
    guilds: list[GuildBriefOut]
    channels: list[ChannelOut]


class CategoryCollectOut(BaseModel):
    changed: int
    skipped_no_access: int


class CollectBody(BaseModel):
    enabled: bool


def _guild_out(row: GuildRow) -> GuildOut:
    return GuildOut(
        id=str(row.id),
        name=row.name,
        icon_hash=row.icon_hash,
        status=row.status,
        enabled_count=row.enabled_count,
    )


def _channel_out(row: ChannelRow) -> ChannelOut:
    return ChannelOut(
        id=str(row.id),
        guild_id=str(row.guild_id),
        parent_id=str(row.parent_id) if row.parent_id is not None else None,
        type=row.type,
        name=row.name,
        position=row.position,
        status=row.status,
        collect_enabled=row.collect_enabled,
        collectable=row.collectable,
        can_read=row.can_read,
    )


@channel_router.get("/guilds")
async def list_guilds(factory: SessionFactoryDep) -> GuildsOut:
    async with factory() as session:
        synced_at = await channel_store.get_synced_at(session)
        guilds = await channel_store.list_guilds(session)
    return GuildsOut(synced_at=synced_at, guilds=[_guild_out(g) for g in guilds])


@channel_router.get("/guilds/{guild_id}/channels")
async def list_guild_channels(guild_id: SnowflakeId, factory: SessionFactoryDep) -> ChannelsOut:
    parsed = _to_id(guild_id)
    if parsed is None:
        raise GuildNotFoundError
    async with factory() as session:
        result = await channel_store.list_guild_channels(session, parsed)
    if result == "guild_not_found":
        raise GuildNotFoundError
    return ChannelsOut(channels=[_channel_out(c) for c in result])


@channel_router.put("/channels/{channel_id}/collect")
async def set_channel_collect(
    channel_id: SnowflakeId, body: CollectBody, factory: SessionFactoryDep
) -> ChannelOut:
    parsed = _to_id(channel_id)
    if parsed is None:
        raise CollectFailedError("channel_not_found")
    async with factory() as session, session.begin():
        result = await channel_store.set_collect(session, parsed, body.enabled)
    if isinstance(result, str):
        raise CollectFailedError(result)
    return _channel_out(result)


@channel_router.put("/categories/{category_id}/collect")
async def set_category_collect(
    category_id: SnowflakeId, body: CollectBody, factory: SessionFactoryDep
) -> CategoryCollectOut:
    parsed = _to_id(category_id)
    if parsed is None:
        raise CategoryCollectFailedError("category_not_found")
    async with factory() as session, session.begin():
        result = await channel_store.set_category_collect(session, parsed, body.enabled)
    if isinstance(result, str):
        raise CategoryCollectFailedError(result)
    return CategoryCollectOut(changed=result.changed, skipped_no_access=result.skipped_no_access)


@channel_router.get("/collected")
async def list_collected(factory: SessionFactoryDep) -> CollectedOut:
    async with factory() as session:
        guilds, channels = await channel_store.list_collected(session)
    return CollectedOut(
        guilds=[GuildBriefOut(id=str(g.id), name=g.name, icon_hash=g.icon_hash) for g in guilds],
        channels=[_channel_out(c) for c in channels],
    )
