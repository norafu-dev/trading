"""Dedicated asyncpg connections that must never come from the SQLAlchemy pool.

LISTEN subscriptions and session level advisory locks are bound to one physical
connection; a pooled connection can be recycled and silently drop either.
"""

import asyncpg

from harvester.config import get_settings

# Channel the collector notifies on after inserting a message (payload: ids only).
NEW_MESSAGE_CHANNEL = "new_message"

# 账号的配置或状态变化时通知，载荷是 {"account_id": <id>}（spec 0005）。
ACCOUNT_CHANGED_CHANNEL = "discord_account_changed"

# 全局一把锁，和账号无关；做多账号时再按账号区分（spec 0005）。
COLLECTOR_LOCK_KEY = 0x6861_7276  # "harv"


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(get_settings().asyncpg_dsn)
