"""Dedicated asyncpg connections that must never come from the SQLAlchemy pool.

LISTEN subscriptions and session level advisory locks are bound to one physical
connection; a pooled connection can be recycled and silently drop either.
"""

import asyncpg

from harvester.config import get_settings

# 新消息入库时通知，载荷是 {"channel_id": "1", "message_id": "2"}，ID 都是字符串（spec 0008）。
NEW_MESSAGE_CHANNEL = "new_message"

# 消息被编辑、embed 刷新、删除时通知，载荷是 {"channel_id": "1", "message_ids": ["2"]}。
MESSAGE_CHANGED_CHANNEL = "message_changed"

# 账号的配置或状态变化时通知，载荷是 {"account_id": <id>}（spec 0005）。
ACCOUNT_CHANGED_CHANNEL = "discord_account_changed"

# 频道的采集开关变了，collector 据此对齐覆盖段；载荷是 {}（spec 0006）。
CHANNEL_COLLECT_CHANGED_CHANNEL = "channel_collect_changed"

# 频道、服务器或开关的数据变了，api 据此通知页面重新取数；载荷是 {}（spec 0006）。
CHANNELS_CHANGED_CHANNEL = "channels_changed"

# 全局一把锁，和账号无关；做多账号时再按账号区分（spec 0005）。
COLLECTOR_LOCK_KEY = 0x6861_7276  # "harv"


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(get_settings().asyncpg_dsn)
