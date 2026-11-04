"""附件接口（spec 0011）。读文件只在登录后给出：R2 返回 302 跳到 1 小时有效的签名链接。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from harvester.api.errors import ApiError
from harvester.api.message_routes import MediaOut, media_out
from harvester.db import SessionFactoryDep
from harvester.db import media_api as media_api_store
from harvester.media import policy
from harvester.media.storage import MediaStore

media_router = APIRouter(prefix="/media", tags=["media"])

MAX_FILE_MB_MIN, MAX_FILE_MB_MAX = media_api_store.MAX_FILE_MB_RANGE
QUOTA_GB_MIN, QUOTA_GB_MAX = media_api_store.QUOTA_GB_RANGE
MediaId = Annotated[int, Path(ge=1, le=2**63 - 1)]
THUMB_CONTENT_TYPE = "image/webp"


class MediaCountsOut(BaseModel):
    pending: int
    stored: int
    failed: int
    too_large: int
    no_space: int
    gone: int


class MediaSettingsOut(BaseModel):
    max_file_mb: int
    quota_gb: int
    used_bytes: int
    counts: MediaCountsOut
    pending_channel_off: int
    storage_full: bool


class MediaSettingsIn(BaseModel):
    max_file_mb: int = Field(
        ge=media_api_store.MAX_FILE_MB_RANGE[0], le=media_api_store.MAX_FILE_MB_RANGE[1]
    )
    quota_gb: int = Field(
        ge=media_api_store.QUOTA_GB_RANGE[0], le=media_api_store.QUOTA_GB_RANGE[1]
    )


class RequeuedOut(BaseModel):
    requeued: int


def _settings_out(usage: media_api_store.MediaUsage) -> MediaSettingsOut:
    return MediaSettingsOut(
        max_file_mb=usage.max_file_mb,
        quota_gb=usage.quota_gb,
        used_bytes=usage.used_bytes,
        counts=MediaCountsOut(**usage.counts),
        pending_channel_off=usage.pending_channel_off,
        storage_full=usage.storage_full,
    )


class MediaNotRetryableError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT,
            "media_not_retryable",
            "只有保存失败或已失效的附件可以重试；超过上限和空间不足要先改设置",
        )


class MediaNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, "media_not_found", "附件不存在或还没保存")


def get_media_store(connection: HTTPConnection) -> MediaStore:
    store: MediaStore | None = connection.app.state.media_store
    if store is None:  # 配置没接上是部署错误，不是请求的问题。
        raise RuntimeError("media store is not configured")
    return store


MediaStoreDep = Annotated[MediaStore, Depends(get_media_store)]


def _cache_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"Cache-Control": f"private, max-age={policy.BROWSER_CACHE_SECONDS}", **(extra or {})}


# 固定路径要放在 /{media_id} 之前，否则 "settings" 会被当成 ID。
@media_router.get("/settings")
async def get_media_settings(factory: SessionFactoryDep) -> MediaSettingsOut:
    async with factory() as session:
        return _settings_out(await media_api_store.get_usage(session))


@media_router.put("/settings")
async def put_media_settings(body: MediaSettingsIn, factory: SessionFactoryDep) -> MediaSettingsOut:
    async with factory() as session, session.begin():
        usage = await media_api_store.update_limits(session, body.max_file_mb, body.quota_gb)
    return _settings_out(usage)


@media_router.post("/retry-failed")
async def retry_failed(factory: SessionFactoryDep) -> RequeuedOut:
    async with factory() as session, session.begin():
        return RequeuedOut(requeued=await media_api_store.retry_all_failed(session))


@media_router.post("/{media_id}/retry")
async def retry_media(media_id: MediaId, factory: SessionFactoryDep) -> MediaOut:
    async with factory() as session, session.begin():
        result = await media_api_store.retry_one(session, media_id)
    if result == "not_found":
        raise MediaNotFoundError
    if result == "not_retryable":
        raise MediaNotRetryableError
    return media_out(result)


@media_router.get(
    "/{media_id}",
    responses={
        302: {"description": "跳到存储里这个文件的签名链接"},
        200: {"description": "本地模式：直接返回文件内容"},
    },
    response_class=Response,
)
async def get_media(
    media_id: MediaId,
    factory: SessionFactoryDep,
    store: MediaStoreDep,
    variant: Annotated[Literal["original", "thumb"], Query()] = "original",
    download: Annotated[bool, Query()] = False,
) -> Response:
    async with factory() as session:
        target = await media_api_store.get_stored_target(session, media_id)
    if target is None:
        raise MediaNotFoundError
    use_thumb = variant == "thumb" and target.thumb_object_key is not None
    key = target.thumb_object_key if use_thumb and target.thumb_object_key else target.object_key
    content_type = THUMB_CONTENT_TYPE if use_thumb else target.content_type
    disposition = policy.content_disposition(content_type, target.filename, download=download)

    signed = store.presign_get(
        key,
        content_type=content_type,
        content_disposition=disposition,
        expires=policy.PRESIGN_SECONDS,
    )
    if signed is not None:
        return RedirectResponse(signed, status_code=status.HTTP_302_FOUND, headers=_cache_headers())

    path = store.local_path(key)
    if path is None or not path.is_file():
        raise MediaNotFoundError
    # 本地模式同源返回文件：禁止嗅探并用 sandbox 的 CSP，防止附件被当成网页执行。
    return FileResponse(
        path,
        media_type=content_type,
        headers=_cache_headers(
            {
                "Content-Disposition": disposition,
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox",
            }
        ),
    )
