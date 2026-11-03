from functools import lru_cache
from pathlib import Path
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
