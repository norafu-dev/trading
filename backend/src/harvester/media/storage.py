# boto3 没有随包的类型；带类型的外壳就是这个文件里的 R2Store，其余代码不碰 boto3。
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""文件存储（spec 0011）：协议加几个实现，由进程入口按配置构造后显式传入。

`LocalStore` 给本地开发，`MemoryStore` 给测试，`R2Store` 给线上。boto3 只在这个文件里用，
对外是带类型的方法。
"""

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import quote

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


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


# boto3 是同步库：上传放进线程；签发签名链接是纯本地计算，直接调用。
_UPLOAD_CONFIG = TransferConfig(max_concurrency=2)


@dataclass(frozen=True)
class R2Store:
    """Cloudflare R2（S3 兼容）上的私有桶。api 拿只读 token，collector 拿读写 token。"""

    client: "S3Client"
    bucket: str

    async def put_file(self, key: str, source: Path, content_type: str) -> None:
        # upload_file 按文件大小自动分段，不把整个文件读进内存；键是内容哈希，重复上传结果一样。
        await asyncio.to_thread(
            self.client.upload_file,
            str(source),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
            Config=_UPLOAD_CONFIG,
        )

    async def exists(self, key: str) -> bool:
        def head() -> bool:
            try:
                self.client.head_object(Bucket=self.bucket, Key=key)
            except self.client.exceptions.ClientError:
                return False
            return True

        return await asyncio.to_thread(head)

    def presign_get(
        self, key: str, *, content_type: str, content_disposition: str, expires: int
    ) -> str | None:
        # 响应头由 api 决定，不依赖上传时写的元数据（AC-14）。
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": content_disposition,
            },
            ExpiresIn=expires,
        )

    def local_path(self, key: str) -> Path | None:
        return None


def build_r2_client(endpoint_url: str, access_key_id: str, secret_access_key: str) -> "S3Client":
    """R2 要 s3v4 签名和 region "auto"；校验和只在需要时计算，R2 不支持新版默认的校验和。"""
    return cast(
        "S3Client",
        boto3.client(  # pyright: ignore[reportUnknownMemberType]
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        ),
    )
