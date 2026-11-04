"""附件接口（spec 0011）。读文件只在登录后给出：R2 返回 302 跳到 1 小时有效的签名链接。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.requests import HTTPConnection

from harvester.api.errors import ApiError
from harvester.db import SessionFactoryDep
from harvester.db import media_read as media_read_store
from harvester.media import policy
from harvester.media.storage import MediaStore

media_router = APIRouter(prefix="/media", tags=["media"])

MediaId = Annotated[int, Path(ge=1, le=2**63 - 1)]
THUMB_CONTENT_TYPE = "image/webp"


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
        target = await media_read_store.get_stored_target(session, media_id)
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
