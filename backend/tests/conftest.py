"""Integration tests run against a real PostgreSQL (the compose one by default).

Each run creates a dedicated `harvester_test` database, migrates it to head and drops
it again at the end, so the tests never touch the development data. Set `TEST_DATABASE_URL` to use
another server or database. After each test every table is truncated.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from harvester.config import Settings, get_settings
from harvester.db import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _test_url() -> URL:
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return make_url(override)
    return make_url(Settings().sqlalchemy_url).set(database="harvester_test")


TEST_URL = _test_url()

# Alembic's env.py and every harvester module read the URL from settings.
os.environ["DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)
get_settings.cache_clear()


def asyncpg_dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def _admin(url: URL, *statements: str) -> None:
    admin = await asyncpg.connect(asyncpg_dsn(url.set(database="postgres")))
    try:
        for statement in statements:
            await admin.execute(statement)
    finally:
        await admin.close()


def recreate_database(url: URL) -> None:
    drop = f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'
    asyncio.run(_admin(url, drop, f'CREATE DATABASE "{url.database}"'))


def drop_database(url: URL) -> None:
    asyncio.run(_admin(url, f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))


def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[URL]:
    recreate_database(TEST_URL)
    command.upgrade(alembic_config(), "head")
    yield TEST_URL
    drop_database(TEST_URL)


@pytest.fixture
async def engine(migrated_db: URL) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    yield engine
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        # 迁移插入的单行默认设置，清表后要放回去（spec 0011、0012）。
        await conn.execute(text("INSERT INTO media_settings (id) VALUES (1)"))
        await conn.execute(text("INSERT INTO history_settings (id) VALUES (1)"))
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture(autouse=True)
def media_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """附件存储的配置没有默认值（spec 0011 AC-20）：测试统一用本地目录。"""
    monkeypatch.setenv("MEDIA_STORAGE", "local")
    monkeypatch.setenv("MEDIA_LOCAL_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MEDIA_TMP_DIR", str(tmp_path / "media-tmp"))
