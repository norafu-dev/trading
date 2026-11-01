"""生成一次性设置码：python -m harvester.api.setup_code

设置码只打印到运行命令的终端，库里只存哈希，不写日志。
"""

import asyncio
import sys
from datetime import UTC, datetime

from harvester.api.auth_store import issue_setup_code
from harvester.db import get_engine, get_sessionmaker


async def run() -> int:
    async with get_sessionmaker()() as session, session.begin():
        code = await issue_setup_code(session, datetime.now(UTC))
    await get_engine().dispose()
    if code is None:
        print(
            "已经设置过了。要重新设置，请先运行 python -m harvester.api.auth_reset",
            file=sys.stderr,
        )
        return 1
    print(f"设置码（1 小时内有效）：{code}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
