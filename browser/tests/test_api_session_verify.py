"""`/session/verify-publish` 的入口契约。

不启动浏览器。守的是入口形状：认证、平台覆盖、以及**空 storage_state 走
fail-fast** 这条 —— 后者尤其重要，因为它是唯一一条不开浏览器就能返回的
真实结论路径。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.schemas import SessionStatus
from tests.conftest import TEST_TOKEN

pytestmark = pytest.mark.unit

LIVE_STATE = {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _body(**overrides):
    payload = {
        "platform": "douyin",
        "storage_state": LIVE_STATE,
        "probe": {"title": "Autumn Harvest Field Notes"},
    }
    payload.update(overrides)
    return payload


def _post(client, **overrides):
    return client.post(
        "/session/verify-publish",
        json=_body(**overrides),
        headers={"X-Internal-Token": TEST_TOKEN},
    )


def test_requires_the_internal_token(client):
    resp = client.post("/session/verify-publish", json=_body())
    assert resp.status_code in (401, 403)


def test_unknown_platform_is_not_supported_not_a_400(client):
    """A platform with no read-back is a statement about OUR coverage, not a
    malformed request. 400 would tell the caller to fix its payload; instead it
    must learn that this post can never be confirmed, so it stops waiting."""
    resp = _post(client, platform="bilibili")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == SessionStatus.NOT_PUBLISHED.value
    assert body["detail"]["reason"] == "not_supported"
    assert body["success"] is False


def test_empty_storage_state_fails_fast_as_a_session_verdict(client):
    """An empty state tells us about the ACCOUNT, never about the post — so it
    must not come back as `not_published`, which would block the work item."""
    resp = _post(client, storage_state={"cookies": [], "origins": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == SessionStatus.SESSION_INVALID.value
    assert body["status"] != SessionStatus.NOT_PUBLISHED.value
    assert body["detail"]["stage"] == "fail_fast"


def test_probe_title_is_required(client):
    resp = client.post(
        "/session/verify-publish",
        json={"platform": "douyin", "storage_state": LIVE_STATE, "probe": {}},
        headers={"X-Internal-Token": TEST_TOKEN},
    )
    assert resp.status_code == 422


def test_douyin_has_a_readback_registered():
    """P0-4's lesson in one line: a registry entry is what makes the feature
    reachable. Tests over an unregistered module prove nothing."""
    from app.platforms import verify_platforms

    assert "douyin" in verify_platforms()
