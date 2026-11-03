"""Write backend/openapi.json: python -m harvester.api.export_openapi

The web app generates its API types from this committed file (pnpm gen:api).
"""

import base64
import json
from pathlib import Path

from pydantic import SecretStr

from harvester.api.main import create_app
from harvester.config import ApiSettings, CryptoSettings

OUTPUT = Path(__file__).resolve().parents[3] / "openapi.json"

# 固定的本地配置，只用来生成接口描述，不需要真实的 .env。密钥不会被用到。
_PLACEHOLDER_KEY = f"1:{base64.b64encode(bytes(32)).decode()}"


def main() -> None:
    app = create_app(
        ApiSettings(site_origin="http://localhost:8080", session_cookie_secure=False),
        CryptoSettings(token_encryption_key=SecretStr(_PLACEHOLDER_KEY)),
    )
    OUTPUT.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
