"""AC-9: tokens never reach logs or API responses."""

import base64
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from pydantic import SecretStr

from harvester.api.main import create_app
from harvester.config import ApiSettings, CryptoSettings
from harvester.logging import configure_logging


@pytest.fixture
def restore_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers = []


def test_token_fields_are_masked_in_logs(  # covers: AC-9
    capsys: pytest.CaptureFixture[str], restore_logging: None
) -> None:
    # Configured inside the test so the handler writes to this phase's captured stdout.
    configure_logging("INFO")

    structlog.get_logger("test").info(
        "account loaded", token_ciphertext="c2VjcmV0", token="mfa.secret", label="main"
    )

    [event] = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert (event["token_ciphertext"], event["token"]) == ("***", "***")
    assert event["label"] == "main"


def property_names(schema: Any) -> set[str]:
    if isinstance(schema, dict):
        own = set(schema.get("properties", {}))
        return own | {n for v in schema.values() for n in property_names(v)}
    if isinstance(schema, list):
        return {n for v in schema for n in property_names(v)}
    return set()


def test_no_api_schema_exposes_a_token_field() -> None:  # covers: AC-9
    app = create_app(
        ApiSettings(site_origin="http://localhost:8080", session_cookie_secure=False),
        CryptoSettings(token_encryption_key=SecretStr(f"1:{base64.b64encode(bytes(32)).decode()}")),
    )
    schema = app.openapi()
    # 只有这两个请求体可以有 token 字段：token 只写不读（spec 0005）。
    write_only = {"AccountCreate", "TokenUpdate"}
    components = schema["components"]["schemas"]
    assert write_only <= set(components)
    names = property_names(
        {**schema, "components": {k: v for k, v in components.items() if k not in write_only}}
    )

    assert not {n for n in names if "token" in n.lower()}
