"""Entry point: python -m harvester.api"""

import uvicorn

from harvester.config import get_api_settings, get_crypto_settings, get_settings
from harvester.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, "api", settings.log_dir)
    # 缺配置或格式不对时，在起 uvicorn 之前就报错退出。
    get_api_settings()
    get_crypto_settings()
    # Exactly one worker: each worker would LISTEN and fan out on its own.
    uvicorn.run(
        "harvester.api.main:create_app_from_env",
        factory=True,
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
        workers=1,
        log_config=None,
    )


if __name__ == "__main__":
    main()
