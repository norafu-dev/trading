"""Dedicated asyncpg connections that must never come from the SQLAlchemy pool.

LISTEN subscriptions and session level advisory locks are bound to one physical
connection; a pooled connection can be recycled and silently drop either.
"""

import asyncpg

from harvester.config import get_settings

# Channel the collector notifies on after inserting a message (payload: ids only).
NEW_MESSAGE_CHANNEL = "new_message"

# Single fixed key while there is no account model; feature 6 makes it per account.
COLLECTOR_LOCK_KEY = 0x6861_7276  # "harv"


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(get_settings().asyncpg_dsn)
