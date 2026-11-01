"""Entry point: python -m harvester.collector"""

import asyncio
import sys

from harvester.collector.main import run
from harvester.config import get_crypto_settings, get_settings
from harvester.logging import configure_logging


def main() -> None:
    configure_logging(get_settings().log_level)
    get_crypto_settings()  # 缺密钥或格式不对时直接报错退出。
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
