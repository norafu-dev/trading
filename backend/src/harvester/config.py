from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root .env (local dev). In containers the variables come from compose instead.
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT_ENV, extra="ignore")

    postgres_user: str = "harvester"
    postgres_password: SecretStr = SecretStr("harvester")
    postgres_db: str = "harvester"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    # Set by compose in containers; locally it is derived from the POSTGRES_* values.
    database_url: SecretStr | None = None

    token_encryption_key: SecretStr | None = None
    log_level: str = "INFO"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
