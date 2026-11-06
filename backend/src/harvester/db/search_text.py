"""消息的搜索文字（spec 0013）：纯函数，不碰数据库。

规则改了，必须把 `message.search_text` 置为 NULL，让补建重新生成。
"""

import re
from collections.abc import Iterable, Sequence

from harvester.db.snapshots import EmbedData, ForwardedData

# <:pepe:123> 和动图 <a:pepe:123> 只留 :pepe:
_CUSTOM_EMOJI = re.compile(r"<a?(:\w+:)\d+>")
# </cmd:123> 变成 /cmd
_SLASH_COMMAND = re.compile(r"</([^:>\s]+(?: [^:>\s]+){0,2}):\d+>")
# <@123>、<@!123>、<@&123>、<#123>、<t:123:R> 整个去掉
_DROPPED_MARKUP = re.compile(r"<(?:@[!&]?\d+|#\d+|t:-?\d+(?::[A-Za-z])?)>")
_WHITESPACE = re.compile(r"\s+")


def strip_markup(raw: str) -> str:
    """只处理 Discord 标记，保留大小写和空白；搜索和导出共用（spec 0014 AC-13）。"""
    text = _CUSTOM_EMOJI.sub(r"\1", raw)
    text = _SLASH_COMMAND.sub(r"/\1", text)
    return _DROPPED_MARKUP.sub("", text)


def clean_text(raw: str) -> str:
    """处理 Discord 标记、转小写、合并空白。"""
    return _WHITESPACE.sub(" ", strip_markup(raw).lower()).strip()


def _embed_parts(embed: EmbedData) -> Iterable[str]:
    yield embed.get("title", "")
    yield embed.get("description", "")
    for field in embed.get("fields", ()):
        yield field["name"]
        yield field["value"]
    author = embed.get("author")
    if author is not None:
        yield author.get("name", "")
    yield embed.get("footer_text", "")


def _all_parts(
    content: str,
    forwarded: ForwardedData | None,
    embeds: Sequence[EmbedData] | None,
    attachment_filenames: Sequence[str],
) -> Iterable[str]:
    yield content
    for embed in embeds or ():
        yield from _embed_parts(embed)
    if forwarded is not None:
        yield forwarded["content"]
        for embed in forwarded["embeds"]:
            yield from _embed_parts(embed)
        yield from forwarded["attachment_names"]
    yield from attachment_filenames


def build_search_text(
    content: str,
    forwarded: ForwardedData | None,
    embeds: Sequence[EmbedData] | None,
    attachment_filenames: Sequence[str],
) -> str:
    """各部分分别处理，再用换行连接，空的部分跳过（AC-6）。"""
    cleaned = (
        clean_text(part) for part in _all_parts(content, forwarded, embeds, attachment_filenames)
    )
    return "\n".join(part for part in cleaned if part)
