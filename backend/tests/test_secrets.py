"""AC-9: tokens never reach logs or API responses."""

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from harvester.api.main import app
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
    names = property_names(app.openapi())

    assert not {n for n in names if "token" in n.lower()}
