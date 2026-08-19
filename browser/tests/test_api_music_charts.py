"""`/session/music/charts` 的入口契约 —— 与 `/session/probe` 同款闸门。

守的是"在浏览器存在**之前**就该发生"的三件事：认证、平台覆盖、URL 白名单。
最后一条不只断言返回 400，还断言**根本没去访问**（`run_harvest` 换成一枚
地雷）—— 这个端点带着某个真实账号的 cookie，白名单失守就是一次带凭证的
SSRF，"返回了 400"和"没有发出请求"不是同一件事。

比 `/session/probe` 多守一条：**这个端点有副作用**。要够到「选择音乐」面板
必须先上传，所以每次运行会在账号上留一条草稿。因此"闸门拦住了"与"没有真的
跑"之间的差别，在这里比在一个只读端点上更贵。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.schemas import SessionStatus
from tests.conftest import TEST_TOKEN

pytestmark = pytest.mark.unit

LIVE_STATE = {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}
EDITOR_URL = "https://creator.douyin.com/creator-micro/content/upload?default-tab=3"
SEED = {
    "kind": "image",
    "url": "https://nous-backend:8080/signed/seed.jpg",
    "filename": "seed.jpg",
}


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

    monkeypatch.setattr(main, "run_harvest", _boom)
    return calls


def _body(**overrides):
    payload = {
        "platform": "douyin",
        "storage_state": LIVE_STATE,
        "url": EDITOR_URL,
        "seed_file": SEED,
    }
    payload.update(overrides)
    return payload


def _post(client, **overrides):
    return client.post(
        "/session/music/charts",
        json=_body(**overrides),
        headers={"X-Internal-Token": TEST_TOKEN},
    )


def test_the_endpoint_requires_the_internal_token(client, landmine):
    response = client.post("/session/music/charts", json=_body())
    assert response.status_code == 401
    assert landmine == []


def test_an_unknown_platform_is_refused_before_a_browser_exists(client, landmine):
    response = _post(client, platform="nosuchplatform")
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["reason"] == "not_supported"
    assert landmine == []


def test_a_url_outside_the_platforms_hosts_is_refused_and_never_fetched(
    client, landmine
):
    """Both halves matter: the 400 AND the absence of a request.

    This body carries a real account's cookies. A host allow-list that returns
    400 *after* opening the page has already performed the SSRF it exists to
    prevent.
    """
    response = _post(client, url="https://evil.example/creator-micro/content/upload")
    assert response.status_code == 400
    assert response.json()["detail"]["reason"] == "url_not_allowed"
    assert landmine == []


def test_a_url_that_only_looks_like_the_creator_host_is_refused(client, landmine):
    response = _post(client, url="https://creator.douyin.com@evil.example/x")
    assert response.status_code == 400
    assert landmine == []


def test_a_request_without_a_seed_file_is_a_422_not_an_empty_harvest(client, landmine):
    """The panel does not exist before an upload [实测 2026-08-19].

    Taking the seed as optional would turn "you forgot it" into "the platform
    has no charts" — a caller would cache an empty catalogue and never learn
    why.
    """
    payload = _body()
    payload.pop("seed_file")
    response = client.post(
        "/session/music/charts",
        json=payload,
        headers={"X-Internal-Token": TEST_TOKEN},
    )
    assert response.status_code == 422
    assert landmine == []


def test_the_gates_let_a_well_formed_request_through(client, monkeypatch):
    """The positive control. Without it every test above would still pass on an
    endpoint that refused everything."""
    from app.schemas import MusicChartPayload, MusicHarvestResponse, MusicSongPayload

    seen = {}

    async def _ok(spec, request):
        seen["url"] = request.url
        seen["platform"] = spec.platform
        return MusicHarvestResponse(
            success=True,
            status=SessionStatus.SESSION_VALID,
            message="read 1 of 1 charts",
            charts=[
                MusicChartPayload(
                    category_id="1",
                    category_name="推荐",
                    category_kind="recommend",
                    ok=True,
                    songs=[
                        MusicSongPayload(
                            music_id="7655518923810474790",
                            music_name="自带流量的音乐",
                            user_count=0,
                        )
                    ],
                )
            ],
        )

    monkeypatch.setattr(main, "run_harvest", _ok)
    response = _post(client)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert seen == {"url": EDITOR_URL, "platform": "douyin"}
    song = body["charts"][0]["songs"][0]
    # A string on the wire, all the way out. 19 digits do not survive a JSON
    # number, and the frontend keys its selection on this.
    assert song["music_id"] == "7655518923810474790"
    # `0` survives as `0`, not as `null`: a track really can be used by nobody.
    assert song["user_count"] == 0
