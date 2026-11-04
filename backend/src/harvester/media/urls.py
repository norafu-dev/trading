"""Discord CDN 链接和存储路径的纯函数（spec 0011）。"""

import re
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

DISCORD_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})
_HOSTED_PATH_PREFIXES = ("/attachments/", "/ephemeral-attachments/")
_ATTACHMENT_PATH = re.compile(r"^/(?:ephemeral-)?attachments/\d+/(\d+)/")
_EXPIRES = re.compile(r"(?:^|&)ex=([0-9a-fA-F]{1,16})(?:&|$)")
_EXTENSION_MAX = 10


def is_discord_hosted(url: str) -> bool:
    """https，主机名正好是 Discord 的 CDN，路径以 /attachments/ 等开头（AC-2）。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname in DISCORD_HOSTS
        and parts.path.startswith(_HOSTED_PATH_PREFIXES)
    )


def strip_query(url: str) -> str:
    """去掉签名参数：判断「同一张图」和记日志都只看主机名加路径。"""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def link_expires_at(url: str) -> datetime | None:
    """`ex` 参数是十六进制 Unix 秒；没有就当作没有过期时间。"""
    match = _EXPIRES.search(urlsplit(url).query)
    if match is None:
        return None
    try:
        return datetime.fromtimestamp(int(match.group(1), 16), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def discord_attachment_id(url: str) -> int | None:
    match = _ATTACHMENT_PATH.match(urlsplit(url).path)
    return int(match.group(1)) if match else None


def filename_from_url(url: str) -> str:
    """embed 图没有文件名，用链接路径最后一段（URL 解码）。"""
    last = urlsplit(url).path.rsplit("/", 1)[-1]
    return unquote(last) or "image"


def file_prefix(channel_id: int, created_at: datetime) -> str:
    """`<频道 ID>/<UTC 年月>`，也是去重的范围（AC-4）。"""
    utc = created_at.astimezone(UTC)
    return f"{channel_id}/{utc.year:04d}-{utc.month:02d}"


def _extension(filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1] if "." in filename else ""
    cleaned = "".join(c for c in suffix.lower() if c.isascii() and c.isalnum())
    return cleaned[:_EXTENSION_MAX]


def object_key(prefix: str, sha256_hex: str, filename: str) -> str:
    extension = _extension(filename)
    return f"{prefix}/{sha256_hex}.{extension}" if extension else f"{prefix}/{sha256_hex}"


def thumb_key(prefix: str, sha256_hex: str) -> str:
    return f"{prefix}/thumbs/{sha256_hex}.webp"
