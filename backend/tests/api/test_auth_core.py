"""纯函数和配置校验：AC-5、AC-7、AC-9、AC-16、AC-17。"""

from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from pydantic import SecretStr, ValidationError

from harvester.api import auth_core as core
from harvester.config import ApiSettings, CryptoSettings, is_local_origin, normalize_origin
from harvester.crypto import (
    DecryptError,
    build_key_ring,
    decrypt,
    encrypt,
    generate_key,
    parse_key,
)

NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("failures", "seconds"),
    [(0, 0), (4, 0), (5, 60), (6, 120), (7, 240), (10, 1920), (11, 3600), (12, 3600), (100, 3600)],
)
def test_backoff_seconds(failures: int, seconds: int) -> None:  # covers: AC-9
    assert core.backoff_seconds(failures) == seconds


def test_remaining_wait_rounds_up_and_floors_at_zero() -> None:  # covers: AC-9
    last = NOW - timedelta(seconds=59, milliseconds=500)
    assert core.remaining_wait_seconds(5, last, NOW) == 1
    assert core.remaining_wait_seconds(5, NOW - timedelta(seconds=61), NOW) == 0
    assert core.remaining_wait_seconds(4, NOW, NOW) == 0


def test_session_validity_boundaries() -> None:  # covers: AC-7
    assert core.session_is_valid(NOW - timedelta(days=29), NOW - timedelta(days=6), NOW)
    assert not core.session_is_valid(NOW - timedelta(days=29), NOW - timedelta(days=7), NOW)
    assert not core.session_is_valid(NOW - timedelta(days=30), NOW, NOW)


def test_verify_totp_step() -> None:  # covers: AC-5
    secret = pyotp.random_base32()
    current = core.totp_step(NOW)
    totp = pyotp.TOTP(secret)
    assert core.verify_totp_step(secret, totp.generate_otp(current), NOW, None) == current
    assert core.verify_totp_step(secret, totp.generate_otp(current - 1), NOW, None) == current - 1
    assert core.verify_totp_step(secret, totp.generate_otp(current + 1), NOW, None) == current + 1
    assert core.verify_totp_step(secret, totp.generate_otp(current + 2), NOW, None) is None
    assert core.verify_totp_step(secret, totp.generate_otp(current), NOW, current) is None
    assert core.verify_totp_step(secret, "١٢٣٤٥٦", NOW, None) is None


def test_request_context_keeps_inet_safe() -> None:  # covers: AC-11
    assert core.request_context("not-an-ip", None).ip == core.UNKNOWN_IP
    assert core.request_context("2001:db8::1", "ua").ip == "2001:db8::1"
    assert core.request_context(None, "x" * 600).user_agent == "x" * 512


def test_encrypt_round_trip_and_purpose_binding() -> None:  # covers: AC-16
    keys = build_key_ring(generate_key(), None)
    blob = encrypt(keys, "totp", b"secret")
    assert blob[0] == 1
    assert decrypt(keys, "totp", blob) == b"secret"
    assert isinstance(decrypt(keys, "discord_token", blob), DecryptError)
    assert isinstance(decrypt(keys, "totp", b"\x01short"), DecryptError)


def test_old_key_decrypts_after_rotation() -> None:  # covers: AC-16
    old, new = generate_key(1), generate_key(2)
    blob = encrypt(build_key_ring(old, None), "totp", b"secret")
    assert isinstance(decrypt(build_key_ring(new, None), "totp", blob), DecryptError)
    assert decrypt(build_key_ring(new, old), "totp", blob) == b"secret"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "abc",
        "0:AAAA",
        "256:AAAA",
        "1:not base64!",
        "1:" + "QUJD",  # 长度不对
    ],
)
def test_bad_key_format_is_rejected(raw: str) -> None:  # covers: AC-16
    with pytest.raises(ValueError, match="key"):
        parse_key(raw)


def test_missing_key_fails_config(monkeypatch: pytest.MonkeyPatch) -> None:  # covers: AC-16
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(ValidationError):
        CryptoSettings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_bad_key_error_does_not_print_the_key() -> None:  # covers: AC-16, AC-17
    with pytest.raises(ValidationError) as caught:
        CryptoSettings(_env_file=None, token_encryption_key=SecretStr("1:c2VjcmV0"))  # pyright: ignore[reportCallIssue]
    assert "c2VjcmV0" not in str(caught.value)


def test_duplicate_versions_fail_config() -> None:  # covers: AC-16
    key = generate_key(3)
    with pytest.raises(ValidationError):
        CryptoSettings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            token_encryption_key=SecretStr(key),
            token_encryption_old_keys=SecretStr(generate_key(3)),
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Dash.Example.com/", "https://dash.example.com"),
        ("https://dash.example.com:443", "https://dash.example.com"),
        ("http://localhost:80", "http://localhost"),
        ("http://localhost:8080", "http://localhost:8080"),
    ],
)
def test_normalize_origin(raw: str, expected: str) -> None:  # covers: AC-10
    assert normalize_origin(raw) == expected


@pytest.mark.parametrize("raw", ["dash.example.com", "ftp://x.com", "https://x.com/path"])
def test_normalize_origin_rejects(raw: str) -> None:  # covers: AC-10
    with pytest.raises(ValueError, match="SITE_ORIGIN"):
        normalize_origin(raw)


@pytest.mark.parametrize(
    "origin", ["http://localhost.evil.com", "https://dash.example.com", "http://127.0.0.1.nip.io"]
)
def test_insecure_cookie_refused_off_localhost(origin: str) -> None:  # covers: AC-17
    assert not is_local_origin(origin)
    with pytest.raises(ValidationError):
        ApiSettings(_env_file=None, site_origin=origin, session_cookie_secure=False)  # pyright: ignore[reportCallIssue]


def test_insecure_cookie_allowed_on_localhost() -> None:  # covers: AC-17
    settings = ApiSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        site_origin="http://127.0.0.1:8080",
        session_cookie_secure=False,
    )
    assert settings.site_origin == "http://127.0.0.1:8080"
