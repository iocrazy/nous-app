import pytest

from app.validation import (
    ProbeKind,
    classify_playwright_error,
    storage_state_is_empty,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "message",
    [
        "Error: net::ERR_PROXY_CONNECTION_FAILED at https://creator.douyin.com/",
        "net::ERR_TUNNEL_CONNECTION_FAILED",
        "net::ERR_SOCKS_CONNECTION_FAILED",
        "net::ERR_UNEXPECTED_PROXY_AUTH",
        "net::ERR_NO_SUPPORTED_PROXIES",
    ],
)
def test_chromium_proxy_errors_are_classified_as_proxy_failed(message):
    """Reporting these as session_invalid would send users to re-scan a QR code
    to fix what is actually a proxy outage."""
    assert classify_playwright_error(message) is ProbeKind.PROXY_FAILED


def test_navigation_timeout_is_a_timeout_not_a_proxy_verdict():
    """`proxy_failed` means "stop retrying, go fix the proxy". Inferring it from
    a plain timeout - which a merely slow platform also produces - would make
    the status untrustworthy. The `proxy_configured` flag rides in `detail`
    instead."""
    assert classify_playwright_error("TimeoutError: Timeout 90000ms exceeded.") is ProbeKind.TIMEOUT


def test_unknown_errors_fall_back_to_generic_error():
    assert classify_playwright_error("TargetClosedError: page crashed") is ProbeKind.ERROR


@pytest.mark.parametrize(
    "state",
    [{}, {"cookies": [], "origins": []}, {"cookies": []}, {"origins": []}],
)
def test_empty_storage_state_is_detected(state):
    assert storage_state_is_empty(state) is True


@pytest.mark.parametrize(
    "state",
    [
        {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []},
        {"cookies": [], "origins": [{"origin": "https://creator.douyin.com"}]},
    ],
)
def test_populated_storage_state_is_not_empty(state):
    assert storage_state_is_empty(state) is False
