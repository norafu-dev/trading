"""删除凭据、所有会话和设置码：python -m harvester.api.auth_reset"""

import asyncio
import sys
from datetime import UTC, datetime

from harvester.api.auth_store import reset_auth
from harvester.db import get_engine, get_sessionmaker

CONFIRMATION = "RESET"


async def run() -> None:
    async with get_sessionmaker()() as session, session.begin():
        await reset_auth(session, datetime.now(UTC))
    await get_engine().dispose()


def main() -> int:
    print("这会删除登录凭据和所有会话，所有设备都会被登出。")
    if input(f"输入 {CONFIRMATION} 确认：").strip() != CONFIRMATION:
        print("已取消。", file=sys.stderr)
        return 1
    asyncio.run(run())
    print("已重置。运行 python -m harvester.api.setup_code 重新设置。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
