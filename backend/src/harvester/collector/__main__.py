"""Entry point: python -m harvester.collector"""

import asyncio
import sys

from harvester.collector.main import run
from harvester.config import get_crypto_settings, get_settings
from harvester.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, "collector", settings.log_dir)
    get_crypto_settings()  # 缺密钥或格式不对时直接报错退出。
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
