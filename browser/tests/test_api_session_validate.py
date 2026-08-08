import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.schemas import EnvironmentConfig, SessionResult, SessionStatus
from tests.conftest import TEST_TOKEN

pytestmark = pytest.mark.unit

LIVE_STATE = {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _body(**overrides):
    payload = {"platform": "douyin", "storage_state": LIVE_STATE}
    payload.update(overrides)
    return payload


def test_missing_token_is_rejected(client):
    resp = client.post("/session/validate", json=_body())
    assert resp.status_code == 401


def test_wrong_token_is_rejected(client):
    resp = client.post(
        "/session/validate", json=_body(), headers={"X-Internal-Token": "nope"}
    )
    assert resp.status_code == 401


def test_unconfigured_token_fails_closed(client, monkeypatch):
    """An unset BROWSER_INTERNAL_TOKEN must not mean 'auth disabled' - this
    service holds decrypted session material."""
    monkeypatch.setenv("BROWSER_INTERNAL_TOKEN", "")
    get_settings.cache_clear()
    resp = client.post(
        "/session/validate", json=_body(), headers={"X-Internal-Token": ""}
    )
    assert resp.status_code == 503


def test_schema_violations_are_rejected_before_any_browser_work(client, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("validator must not run on a malformed request")

    monkeypatch.setattr(main, "get_validator", _boom)
    resp = client.post(
        "/session/validate",
        json={"platform": "douyin"},  # storage_state missing
        headers={"X-Internal-Token": TEST_TOKEN},
    )
    assert resp.status_code == 422


def test_unsupported_platform_returns_the_result_shape_not_session_invalid(client):
    resp = client.post(
        "/session/validate",
        json=_body(platform="kuaishou"),
        headers={"X-Internal-Token": TEST_TOKEN},
    )
    assert resp.status_code == 400
    body = resp.json()
    # Same shape as every other response so callers parse one thing, but not a
    # verdict about the account's session.
    assert body["status"] == SessionStatus.FAILED.value
    assert body["success"] is False
    assert "douyin" in body["detail"]["supported"]


def test_successful_validation_passes_through(client, monkeypatch):
    captured = {}

    async def fake_validator(storage_state, environment):
        captured["storage_state"] = storage_state
        captured["environment"] = environment
        return SessionResult(
            success=True,
            status=SessionStatus.SESSION_VALID,
            message="upload page reached with no login prompt",
            detail={"attempts": 1},
        )

    monkeypatch.setattr(main, "get_validator", lambda _p: fake_validator)
    resp = client.post(
        "/session/validate",
        json=_body(
            environment={
                "proxy_url": None,
                "user_agent": None,
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "geo_lat": None,
                "geo_lng": None,
            }
        ),
        headers={"X-Internal-Token": TEST_TOKEN},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["status"] == "session_valid"
    assert captured["storage_state"] == LIVE_STATE
    assert isinstance(captured["environment"], EnvironmentConfig)


def test_environment_is_optional(client, monkeypatch):
    async def fake_validator(storage_state, environment):
        assert environment is None
        return SessionResult(
            success=True, status=SessionStatus.SESSION_VALID, message="ok"
        )

    monkeypatch.setattr(main, "get_validator", lambda _p: fake_validator)
    resp = client.post(
        "/session/validate", json=_body(), headers={"X-Internal-Token": TEST_TOKEN}
    )
    assert resp.status_code == 200


def test_validator_exception_becomes_a_typed_failure_not_a_500(client, monkeypatch):
    """Business status travels in the body, never as an exception (spec 7.8)."""

    async def exploding_validator(storage_state, environment):
        raise RuntimeError("chromium vanished")

    monkeypatch.setattr(main, "get_validator", lambda _p: exploding_validator)
    resp = client.post(
        "/session/validate", json=_body(), headers={"X-Internal-Token": TEST_TOKEN}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["success"] is False
    assert "chromium vanished" in body["message"]


def test_proxy_credentials_never_appear_in_a_response(client, monkeypatch):
    async def exploding_validator(storage_state, environment):
        raise RuntimeError(
            "net::ERR_PROXY_CONNECTION_FAILED via http://alice:s3cr3t@proxy.example.com:8080"
        )

    monkeypatch.setattr(main, "get_validator", lambda _p: exploding_validator)
    resp = client.post(
        "/session/validate",
        json=_body(environment={"proxy_url": "http://alice:s3cr3t@proxy.example.com:8080"}),
        headers={"X-Internal-Token": TEST_TOKEN},
    )
    raw = resp.text
    assert "s3cr3t" not in raw
    assert "alice" not in raw


def test_returned_statuses_stay_inside_the_shared_enum(client, monkeypatch):
    """The enum is the whole spec 7.8 table - not "whatever this stage emits".

    Both services carry their own copy (no shared Python package across a
    container boundary), so the table is the contract and each side must hold
    all of it. A member missing on one side means the other's answer arrives
    unrecognised and gets flattened to `failed`, throwing away exactly the part
    that told the user what to do. That is why `published` is here in S2 even
    though nothing in this service can produce it until S3.

    Platform-specific values would break every caller's branching and are
    forbidden outright (design doc 6.1a).
    """
    from app.platforms import supported_platforms

    # 同注册表用例:断言"包含",否则每接一个平台都会红一次。
    assert "douyin" in supported_platforms()
    allowed = {s.value for s in SessionStatus}
    assert allowed == {
        "session_valid",
        "session_invalid",
        "proxy_failed",
        "timeout",
        "failed",
        "waiting_scan",
        "scanned",
        "qrcode_expired",
        "sms_required",
        "success",
        "published",
    }
