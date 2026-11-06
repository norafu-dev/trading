import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from harvester.config import get_settings
from harvester.db import Base

config = context.config

if config.config_file_name is not None:
    # 不关掉已有的 logger：测试里迁移和 collector 在同一进程，discord.http 被关掉会让 429 计数失效。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Every model must be imported via harvester.db so autogenerate sees it.
target_metadata = Base.metadata

# The URL comes from settings (env / .env), never from alembic.ini.
DATABASE_URL = get_settings().sqlalchemy_url


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
