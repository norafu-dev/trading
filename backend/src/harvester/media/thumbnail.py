"""图片信息和缩略图（spec 0011 AC-5）。同步函数，调用方放进 `asyncio.to_thread`。

先读尺寸再决定要不要解码：超过 2500 万像素的只存原图，不解码，防止压缩炸弹。
不修改 `Image.MAX_IMAGE_PIXELS`，保留 Pillow 的默认值当兜底。
"""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

THUMB_MAX_PIXELS = 25_000_000
THUMB_LONG_EDGE = 800
_JPEG_DRAFT = (1600, 1600)
_FORMAT_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "BMP": "image/bmp",
}


@dataclass(frozen=True)
class ThumbInfo:
    width: int
    height: int
    size_bytes: int


@dataclass(frozen=True)
class ImageInfo:
    """Pillow 认出的图片：以它识别的格式为准（扩展名是 .png 实际是 JPEG 的情况）。"""

    content_type: str
    width: int
    height: int
    thumb: ThumbInfo | None


def needs_thumbnail(width: int, height: int) -> bool:
    return max(width, height) > THUMB_LONG_EDGE and width * height <= THUMB_MAX_PIXELS


def inspect_image(source: Path, thumb_target: Path) -> ImageInfo | None:
    """不是可处理的图片（SVG、视频、解不开）返回 None；需要缩略图时写到 `thumb_target`。"""
    try:
        with Image.open(source) as image:
            content_type = _FORMAT_TYPES.get(image.format or "")
            if content_type is None:
                return None
            width, height = image.size
            if not needs_thumbnail(width, height):
                return ImageInfo(content_type, width, height, None)
            thumb = _write_thumbnail(image, thumb_target)
            return ImageInfo(content_type, width, height, thumb)
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        # 解不开不算失败：只存原图。
        return None


def _write_thumbnail(image: Image.Image, target: Path) -> ThumbInfo | None:
    try:
        if image.format == "JPEG":
            image.draft("RGB", _JPEG_DRAFT)  # 解码时直接缩小，省内存
        image.seek(0)  # GIF 取第一帧
        frame = image.convert("RGBA" if "A" in image.getbands() or image.mode == "P" else "RGB")
        frame.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE))
        frame.save(target, format="WEBP", quality=80)
    except (OSError, ValueError, EOFError):
        return None
    return ThumbInfo(frame.width, frame.height, target.stat().st_size)
