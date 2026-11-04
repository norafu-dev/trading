"""存储实现和配置（spec 0011 AC-14、AC-19、AC-20）。"""

from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from harvester.config import (
    ApiR2MediaSettings,
    CollectorR2MediaSettings,
    LocalMediaSettings,
    MediaStorageChoice,
    load_media_settings,
)
from harvester.media.storage import LocalStore, MemoryStore, R2Store, build_r2_client

R2_COMMON = {"MEDIA_R2_ACCOUNT_ID": "acct123", "MEDIA_R2_BUCKET": "harvester-media"}
WRITE = {
    "MEDIA_R2_WRITE_ACCESS_KEY_ID": "write-id",
    "MEDIA_R2_WRITE_SECRET_ACCESS_KEY": "write-secret",
}
READ = {"MEDIA_R2_READ_ACCESS_KEY_ID": "read-id", "MEDIA_R2_READ_SECRET_ACCESS_KEY": "read-secret"}


@pytest.fixture(autouse=True)
def _ignore_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置类默认会读仓库根目录的 .env；这里只认测试自己设的环境变量，不受本机 .env 影响。"""
    for cls in (
        MediaStorageChoice,
        LocalMediaSettings,
        ApiR2MediaSettings,
        CollectorR2MediaSettings,
    ):
        monkeypatch.setitem(cls.model_config, "env_file", None)


def set_env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_media_storage_is_required(monkeypatch: pytest.MonkeyPatch) -> None:  # covers: AC-20
    monkeypatch.delenv("MEDIA_STORAGE")
    for role in ("api", "collector"):
        with pytest.raises(ValidationError):
            load_media_settings(role)  # type: ignore[arg-type]


def test_unknown_storage_value_fails(monkeypatch: pytest.MonkeyPatch) -> None:  # covers: AC-20
    monkeypatch.setenv("MEDIA_STORAGE", "s3")
    with pytest.raises(ValidationError):
        load_media_settings("api")


def test_local_needs_no_r2_settings() -> None:  # covers: AC-20
    assert isinstance(load_media_settings("api"), LocalMediaSettings)
    assert isinstance(load_media_settings("collector"), LocalMediaSettings)


@pytest.mark.parametrize("missing", [*R2_COMMON, *READ])
def test_api_r2_needs_every_setting(  # covers: AC-20
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    set_env(monkeypatch, {"MEDIA_STORAGE": "r2", **R2_COMMON, **READ})
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        load_media_settings("api")


@pytest.mark.parametrize("missing", [*R2_COMMON, *WRITE])
def test_collector_r2_needs_every_setting(  # covers: AC-20
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    set_env(monkeypatch, {"MEDIA_STORAGE": "r2", **R2_COMMON, **WRITE})
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        load_media_settings("collector")


def test_each_process_only_sees_its_own_token(monkeypatch: pytest.MonkeyPatch) -> None:  # AC-19
    set_env(monkeypatch, {"MEDIA_STORAGE": "r2", **R2_COMMON, **READ, **WRITE})

    api = load_media_settings("api")
    collector = load_media_settings("collector")

    assert isinstance(api, ApiR2MediaSettings) and isinstance(collector, CollectorR2MediaSettings)
    assert not any("write" in field for field in ApiR2MediaSettings.model_fields)
    assert not any("read_" in field for field in CollectorR2MediaSettings.model_fields)
    assert api.endpoint_url == "https://acct123.r2.cloudflarestorage.com"


def test_secrets_are_not_in_the_repr(monkeypatch: pytest.MonkeyPatch) -> None:  # covers: AC-19
    set_env(monkeypatch, {"MEDIA_STORAGE": "r2", **R2_COMMON, **READ})

    text = repr(load_media_settings("api"))

    assert "read-secret" not in text and "read-id" not in text


def test_presigned_url_overrides_response_headers() -> None:  # covers: AC-14
    store = R2Store(
        client=build_r2_client("https://acct123.r2.cloudflarestorage.com", "id", "secret"),
        bucket="harvester-media",
    )

    url = store.presign_get(
        "201/2024-10/ab.svg",
        content_type="image/svg+xml",
        content_disposition='attachment; filename="a.svg"',
        expires=3600,
    )

    assert url is not None
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc.startswith("harvester-media.") or "harvester-media" in parts.path
    assert query["X-Amz-Expires"] == ["3600"]
    assert query["response-content-type"] == ["image/svg+xml"]
    assert query["response-content-disposition"] == ['attachment; filename="a.svg"']
    assert "X-Amz-Signature" in query
    assert "secret" not in url


class RecordingClient:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str, dict[str, Any]]] = []

    def upload_file(self, filename: str, bucket: str, key: str, **kwargs: Any) -> None:
        self.uploads.append((filename, bucket, key, kwargs))


async def test_put_file_uses_multipart_capable_upload(tmp_path: Path) -> None:  # covers: AC-18
    source = tmp_path / "f.bin"
    source.write_bytes(b"data")
    client = RecordingClient()
    store = R2Store(client=cast(Any, client), bucket="b")

    await store.put_file("k", source, "image/png")
    await store.put_file("k", source, "image/png")  # 同一个键重复上传不出错

    assert [(u[1], u[2]) for u in client.uploads] == [("b", "k"), ("b", "k")]
    assert client.uploads[0][3]["ExtraArgs"] == {"ContentType": "image/png"}
    assert client.uploads[0][3]["Config"].max_concurrency == 2


async def test_local_and_memory_store_roundtrip(tmp_path: Path) -> None:
    source = tmp_path / "src.bin"
    source.write_bytes(b"abc")
    local = LocalStore(tmp_path / "root")
    memory = MemoryStore()

    await local.put_file("a/b/c.bin", source, "x/y")
    await memory.put_file("a/b/c.bin", source, "x/y")

    assert await local.exists("a/b/c.bin") and await memory.exists("a/b/c.bin")
    assert not await local.exists("missing")
    path = local.local_path("a/b/c.bin")
    assert path is not None and path.read_bytes() == b"abc"
    assert local.local_path("../../etc/passwd") is None
