from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from harvester.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().sqlalchemy_url,
        pool_pre_ping=True,
        # 语句参数里可能有哈希和密文，不让它们跟着异常进日志。
        hide_parameters=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return get_sessionmaker()


SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


async def get_session(factory: SessionFactoryDep) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
