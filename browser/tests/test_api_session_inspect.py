"""`/session/inspect` 的入口契约。

不启动浏览器。守的是三条在浏览器存在**之前**就该发生的事：认证、平台覆盖、
以及 URL 白名单 —— 最后一条是这个端点的全部安全性所在，所以它不只断言
"返回 400"，还断言**根本没有去访问**（``run_inspect`` 被换成一枚地雷）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.schemas import SessionStatus
from tests.conftest import TEST_TOKEN

pytestmark = pytest.mark.unit

LIVE_STATE = {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}
CREATOR_URL = "https://creator.douyin.com/creator-micro/content/upload"


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def landmine(monkeypatch):
    """Explodes if anything downstream of the gates ever runs."""
    calls = []

    async def _boom(spec, request):  # pragma: no cover - the point is not to run
        calls.append(request.url)
        raise AssertionError(f"a browser run was started for {request.url}")

    monkeypatch.setattr(main, "run_inspect", _boom)
    return calls


def _body(**overrides):
    payload = {
        "platform": "douyin",
        "storage_state": LIVE_STATE,
        "url": CREATOR_URL,
    }
    payload.update(overrides)
    return payload


def _post(client, **overrides):
    return client.post(
        "/session/inspect",
        json=_body(**overrides),
        headers={"X-Internal-Token": TEST_TOKEN},
    )


def test_requires_the_internal_token(client):
    resp = client.post("/session/inspect", json=_body())
    assert resp.status_code in (401, 403)


def test_unknown_platform_is_refused_before_anything_opens(client, landmine):
    resp = _post(client, platform="bilibili")
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["reason"] == "not_supported"
    assert body["detail"]["supported"] == ["douyin", "xiaohongshu"]
    assert landmine == []


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://creator.douyin.com/",
        "https://creator.douyin.com@example.com/",
        "https://www.douyin.com/",
        "file:///etc/passwd",
    ],
)
def test_a_url_outside_the_allow_list_is_refused_without_a_fetch(
    client, landmine, url
):
    """The endpoint carries a live account's cookies. An arbitrary URL here is
    a credentialed SSRF, so the refusal must happen before a browser exists —
    which is what the landmine proves."""
    resp = _post(client, url=url)
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["status"] == SessionStatus.FAILED.value
    assert body["detail"]["reason"] == "url_not_allowed"
    assert body["detail"]["allowed_hosts"] == ["creator.douyin.com"]
    assert landmine == []


def test_empty_storage_state_fails_fast_without_a_browser(client):
    """A real conclusion reachable with no browser at all: an empty state
    cannot possibly be a live session."""
    resp = _post(client, storage_state={"cookies": [], "origins": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == SessionStatus.SESSION_INVALID.value
    assert body["detail"]["stage"] == "fail_fast"


def test_probe_counts_over_the_cap_are_a_422(client, landmine):
    resp = _post(client, text_probes=["x"] * 41)
    assert resp.status_code == 422
    assert landmine == []


def test_the_url_is_required(client):
    resp = client.post(
        "/session/inspect",
        json={"platform": "douyin", "storage_state": LIVE_STATE},
        headers={"X-Internal-Token": TEST_TOKEN},
    )
    assert resp.status_code == 422


def test_the_service_exposes_no_other_new_route(client):
    """Recon adds exactly one route. A second one would be a second surface
    holding decrypted sessions."""
    paths = sorted(
        route.path for route in main.app.routes if "inspect" in getattr(route, "path", "")
    )
    assert paths == ["/session/inspect"]
