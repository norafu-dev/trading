"""文件存储（spec 0011）：协议加几个实现，由进程入口按配置构造后显式传入。

`LocalStore` 给本地开发，`MemoryStore` 给测试，`R2Store` 在里程碑 5 加。
"""

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import quote


class MediaStore(Protocol):
    async def put_file(self, key: str, source: Path, content_type: str) -> None:
        """上传一个本地文件；键就是内容哈希，重复上传结果一样。"""
        ...

    async def exists(self, key: str) -> bool: ...

    def presign_get(
        self, key: str, *, content_type: str, content_disposition: str, expires: int
    ) -> str | None:
        """签名链接；本地存储没有，返回 None，由 api 直接读文件。"""
        ...

    def local_path(self, key: str) -> Path | None:
        """本地存储里文件的路径；远端存储返回 None。"""
        ...


@dataclass(frozen=True)
class LocalStore:
    root: Path

    def local_path(self, key: str) -> Path | None:
        target = (self.root / key).resolve()
        # 键由我们自己生成，这里仍然防一手路径穿越。
        return target if target.is_relative_to(self.root.resolve()) else None

    async def put_file(self, key: str, source: Path, content_type: str) -> None:
        del content_type
        target = self.local_path(key)
        if target is None:
            raise ValueError("object key escapes the media directory")

        def copy() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            shutil.copyfile(source, partial)
            partial.replace(target)

        await asyncio.to_thread(copy)

    async def exists(self, key: str) -> bool:
        target = self.local_path(key)
        return target is not None and await asyncio.to_thread(target.is_file)

    def presign_get(
        self, key: str, *, content_type: str, content_disposition: str, expires: int
    ) -> str | None:
        return None


@dataclass(frozen=True)
class MemoryStore:
    """测试用：文件放在传入的字典里（字典是调用方的测试夹具，不是模块状态）。"""

    objects: dict[str, bytes] = field(default_factory=dict[str, bytes])

    async def put_file(self, key: str, source: Path, content_type: str) -> None:
        del content_type
        self.objects[key] = await asyncio.to_thread(source.read_bytes)

    async def exists(self, key: str) -> bool:
        return key in self.objects

    def presign_get(
        self, key: str, *, content_type: str, content_disposition: str, expires: int
    ) -> str | None:
        return (
            f"memory://{key}?type={content_type}"
            f"&disposition={quote(content_disposition)}&expires={expires}"
        )

    def local_path(self, key: str) -> Path | None:
        return None


def build_local_store(root: Path) -> LocalStore:
    return LocalStore(root=root)
