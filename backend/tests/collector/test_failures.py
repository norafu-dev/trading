"""失败分类表的每一行和退避序列（spec 0005）。"""

import pytest

from harvester.collector.failures import (
    ConnectionFailure,
    FailureVerdict,
    classify_failure,
    next_delay,
)

STOP_INVALID = "token_invalid"
STOP_VERIFY = "verification_required"
RETRY = "reconnecting"


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (ConnectionFailure("captcha", http_status=400), STOP_VERIFY, "captcha_required"),
        (ConnectionFailure("login_failure", http_status=401), STOP_INVALID, "http_401"),
        (ConnectionFailure("http", http_status=401), STOP_INVALID, "http_401"),
        (ConnectionFailure("gateway_closed", close_code=4004), STOP_INVALID, "gateway_close_4004"),
        (ConnectionFailure("undecryptable"), STOP_INVALID, "token_undecryptable"),
        (ConnectionFailure("required_action"), STOP_VERIFY, "required_action"),
        (ConnectionFailure("http", http_status=403, during_login=True), STOP_VERIFY, "http_403"),
        (ConnectionFailure("http", http_status=429), RETRY, "http_429"),
        (ConnectionFailure("rate_limited", retry_after=90.0), RETRY, "http_429"),
        (ConnectionFailure("gateway_reconnect"), RETRY, "gateway_reconnect"),
        (ConnectionFailure("http", http_status=502), RETRY, "http_5xx"),
        (ConnectionFailure("network"), RETRY, "network_error"),
        (ConnectionFailure("gateway_closed", close_code=4008), RETRY, "gateway_close_4008"),
        (ConnectionFailure("gateway_closed", close_code=4014), RETRY, "gateway_close_4014"),
    ],
)
def test_table(failure: ConnectionFailure, status: str, code: str) -> None:  # covers: AC-8, AC-9
    assert classify_failure(failure) == FailureVerdict(status, code)  # pyright: ignore[reportArgumentType]


def test_captcha_wins_over_http_status() -> None:
    # CaptchaRequired 是 HTTPException 的子类，即使状态码是 401 也要先按验证码处理。
    verdict = classify_failure(ConnectionFailure("captcha", http_status=401))
    assert verdict == FailureVerdict(STOP_VERIFY, "captcha_required")


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionFailure("http", http_status=400),
        ConnectionFailure("http", http_status=403),
    ],
)
def test_unknown_rows_mean_crash(failure: ConnectionFailure) -> None:
    assert classify_failure(failure) is None


def test_stops_login_flag() -> None:
    assert FailureVerdict(STOP_INVALID, "x").stops_login
    assert FailureVerdict(STOP_VERIFY, "x").stops_login
    assert not FailureVerdict(RETRY, "x").stops_login


def test_backoff_doubles_and_caps() -> None:  # covers: AC-5
    top = [next_delay(n, lambda _low, high: high) for n in range(1, 9)]
    assert top == [5, 10, 20, 40, 80, 160, 300, 300]


def test_backoff_jitter_stays_in_range() -> None:  # covers: AC-5
    assert next_delay(1, lambda low, _high: low) == pytest.approx(4.0)
    assert next_delay(20, lambda low, _high: low) == pytest.approx(240.0)
