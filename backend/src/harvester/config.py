from functools import lru_cache
from pathlib import Path
from typing import Literal, overload
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from harvester.crypto import KeyRing, build_key_ring

# Repo root .env (local dev). In containers the variables come from compose instead.
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
# hide_input_in_errors：校验失败的报错不能把密钥原文打到启动日志里。
_ENV_CONFIG = SettingsConfigDict(env_file=_ROOT_ENV, extra="ignore", hide_input_in_errors=True)
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1"})


class Settings(BaseSettings):
    """所有进程共用：数据库和日志。migrate 只用这一组。"""

    model_config = _ENV_CONFIG

    postgres_user: str = "harvester"
    postgres_password: SecretStr = SecretStr("harvester")
    postgres_db: str = "harvester"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    # Set by compose in containers; locally it is derived from the POSTGRES_* values.
    database_url: SecretStr | None = None

    log_level: str = "INFO"
    # 不配置就只写 stdout。相对路径按进程工作目录解析（spec 0007）。
    log_dir: Path | None = None

    @field_validator("log_dir", mode="before")
    @classmethod
    def _empty_log_dir_is_unset(cls, value: object) -> object:
        # `LOG_DIR=` 写空时 Path("") 会变成当前目录，不能把它当日志目录。
        return None if value == "" else value

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url is not None:
            return self.database_url.get_secret_value()
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def asyncpg_dsn(self) -> str:
        """Plain DSN for direct asyncpg connections (LISTEN, advisory lock)."""
        return self.sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class CryptoSettings(BaseSettings):
    """主密钥，api 和 collector 启动时校验。"""

    model_config = _ENV_CONFIG

    token_encryption_key: SecretStr
    token_encryption_old_keys: SecretStr | None = None

    @model_validator(mode="after")
    def _keys_parse(self) -> "CryptoSettings":
        self.key_ring  # noqa: B018  # 解析失败时让配置校验报错。
        return self

    @property
    def key_ring(self) -> KeyRing:
        old = self.token_encryption_old_keys
        return build_key_ring(
            self.token_encryption_key.get_secret_value(),
            old.get_secret_value() if old is not None else None,
        )


def normalize_origin(raw: str) -> str:
    """小写、去掉末尾的 /、去掉默认端口；不是纯 origin 就报错。"""
    parts = urlsplit(raw.strip())
    scheme, hostname = parts.scheme.lower(), parts.hostname
    if scheme not in _DEFAULT_PORTS or not hostname:
        raise ValueError("SITE_ORIGIN must look like https://example.com")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        raise ValueError("SITE_ORIGIN must not have a path, query or credentials")
    port = parts.port
    suffix = f":{port}" if port is not None and port != _DEFAULT_PORTS[scheme] else ""
    return f"{scheme}://{hostname.lower()}{suffix}"


def is_local_origin(origin: str) -> bool:
    return urlsplit(origin).hostname in _LOCAL_HOSTNAMES


class ApiSettings(BaseSettings):
    """只有 api 校验。"""

    model_config = _ENV_CONFIG

    site_origin: str
    session_cookie_secure: bool = True

    @field_validator("site_origin")
    @classmethod
    def _normalize_site_origin(cls, value: str) -> str:
        return normalize_origin(value)

    @model_validator(mode="after")
    def _insecure_cookie_only_locally(self) -> "ApiSettings":
        if not self.session_cookie_secure and not is_local_origin(self.site_origin):
            raise ValueError("SESSION_COOKIE_SECURE=false is only allowed on localhost")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_crypto_settings() -> CryptoSettings:
    return CryptoSettings()  # pyright: ignore[reportCallIssue]  # 必填项来自环境变量


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()  # pyright: ignore[reportCallIssue]  # 必填项来自环境变量


# 附件存储（spec 0011）。MEDIA_STORAGE 必填：没有默认值，写错也直接报错退出。
MediaRole = Literal["api", "collector"]


class MediaStorageChoice(BaseSettings):
    model_config = _ENV_CONFIG

    media_storage: Literal["local", "r2"]


class LocalMediaSettings(BaseSettings):
    """本地开发：文件放在目录里，api 直接返回内容。"""

    model_config = _ENV_CONFIG

    media_local_dir: Path = Path("./media-dev")
    # 只有 collector 用：下载中的临时文件，启动时清空。
    media_tmp_dir: Path = Path("/tmp/harvester-media")


class _R2Common(BaseSettings):
    model_config = _ENV_CONFIG

    media_r2_account_id: str
    media_r2_bucket: str

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.media_r2_account_id}.r2.cloudflarestorage.com"


class CollectorR2MediaSettings(_R2Common):
    """collector 拿读写 token，拿不到 api 的只读 token。"""

    media_r2_write_access_key_id: SecretStr
    media_r2_write_secret_access_key: SecretStr
    media_tmp_dir: Path = Path("/tmp/harvester-media")


class ApiR2MediaSettings(_R2Common):
    """api 只拿只读 token，没有任何写 token 的字段。"""

    media_r2_read_access_key_id: SecretStr
    media_r2_read_secret_access_key: SecretStr
    # 桶的公开域名，只有导出用它拼文件链接（spec 0014 AC-17）。
    media_public_base_url: str

    @field_validator("media_public_base_url")
    @classmethod
    def _check_public_base_url(cls, value: str) -> str:
        return normalize_public_base_url(value)


def normalize_public_base_url(raw: str) -> str:
    """只接受 `https://<主机名>`：R2 自定义域名不支持路径前缀，带了链接会全部 404。"""
    stripped = raw.strip()
    parts = urlsplit(stripped)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("MEDIA_PUBLIC_BASE_URL must look like https://media.example.com")
    has_extra = "?" in stripped or "#" in stripped or parts.username or parts.password
    if parts.path not in ("", "/") or has_extra:
        raise ValueError("MEDIA_PUBLIC_BASE_URL must not have a path, query, fragment or user")
    return stripped.rstrip("/")


MediaSettings = LocalMediaSettings | CollectorR2MediaSettings | ApiR2MediaSettings


@overload
def load_media_settings(role: Literal["api"]) -> LocalMediaSettings | ApiR2MediaSettings: ...
@overload
def load_media_settings(
    role: Literal["collector"],
) -> LocalMediaSettings | CollectorR2MediaSettings: ...
def load_media_settings(role: MediaRole) -> MediaSettings:
    """先读 MEDIA_STORAGE，再实例化对应的类；缺任何一项必填配置都会抛校验错误。"""
    choice = MediaStorageChoice()  # pyright: ignore[reportCallIssue]  # 必填项来自环境变量
    if choice.media_storage == "local":
        return LocalMediaSettings()
    if role == "collector":
        return CollectorR2MediaSettings()  # pyright: ignore[reportCallIssue]
    return ApiR2MediaSettings()  # pyright: ignore[reportCallIssue]
