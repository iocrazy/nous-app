"""``GET /distribution/music/search`` —— 配乐选择器的数据源。

重心与 ``test_distribution_topic_suggest`` 同族：**空列表只允许有一个含义**。
这条链上尤其如此 —— 实测拿一个乱码关键词去搜仍回 8 条（模糊召回），所以一个空
面板几乎只可能是我们自己错了，用它表达失败等于把故障说成结论。

另外钉一件话题那条链没有的事：**这一次是以谁的身份在看曲库**。搜索本身与账号
无关，但换搜索凭证那一步用的是某个已绑账号的会话，而收藏那类 tab 是按账号隔离
的（实测三个账号分别 0 / 18 / 9 条）。响应里不带身份，面板就只能猜 —— 那是
"系统知道、用户看不到"，本仓库明令禁止的那一族。

mock 用的是上游**真实响应形状**（2026-08-15 实测）：``id`` 是 JSON number 且
超过 2^53、``id_str`` 是字符串、``has_more`` 是 1/0 整数、``duration`` 是秒。
不按前端书写习惯"美化"（CLAUDE.md「边界 mock 必须用真实 JSON 形状」）。
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.distribution_router as dr
from app.services.distribution import music_catalog as mc

_PATH = "/api/v1/distribution/music/search"

_AUTH_BODY = {"status_code": 0, "signature": "s" * 121}
_SEARCH_BODY = {
    "status_code": 0,
    "cursor": 20,
    "has_more": 1,
    "music": [
        {
            "id": 6953836671917951012,
            "id_str": "6953836671917951012",
            "title": "起风了",
            "author": "叶龙发",
            "duration": 49,
            "user_count": 30025,
            "cover_medium": {"url_list": ["https://p3.example.invalid/m.jpeg"]},
            "play_url": {"url_list": ["https://sf.example.invalid/a.mp3"]},
        }
    ],
}


def _make_app(monkeypatch) -> FastAPI:
    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = lambda: None
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": "u-1"}

    async def fake_authorize_account(account_id, user):
        return {
            "id": str(account_id),
            "platform": "douyin",
            "auth_type": "session",
            "scope_type": "user",
            "scope_id": "u-1",
            "username": "HEYGO",
            "avatar_url": "https://p3.example.invalid/avatar.jpeg",
        }

    async def fake_cookie(account_id):
        return "sessionid=REDACTED"

    monkeypatch.setattr(dr, "_authorize_account", fake_authorize_account)
    monkeypatch.setattr(mc, "_account_cookie_header", fake_cookie)
    return app


@pytest.fixture(autouse=True)
def _clean_cache():
    """进程内缓存跨测试泄漏 = 后一条悄悄命中前一条的结果，等于没测。"""
    mc.clear_catalog_cache()
    yield
    mc.clear_catalog_cache()


@pytest.fixture
def client(monkeypatch) -> TestClient:
    return TestClient(_make_app(monkeypatch))


@respx.mock
def test_a_search_returns_pickable_cards(client):
    """封面/歌名/作者/时长/使用量 —— 这五样就是用户挑一张卡片的依据。"""
    respx.get(mc.DOUYIN_AUTH_URL).mock(
        return_value=httpx.Response(200, json=_AUTH_BODY)
    )
    route = respx.get(mc.DOUYIN_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_SEARCH_BODY)
    )

    body = client.get(_PATH, params={"account_id": 10, "keyword": "起风了"}).json()

    assert route.called
    track = body["tracks"][0]
    assert track["music_id"] == "6953836671917951012"
    assert (track["title"], track["author"]) == ("起风了", "叶龙发")
    assert (track["duration"], track["user_count"]) == (49, 30025)
    assert body["has_more"] is True and body["cursor"] == 20


@respx.mock
def test_the_response_says_whose_library_this_is(client):
    """**守卫**。收藏这类 tab 按账号隔离，而浏览身份取的是第一个目标账号 ——
    用户换个目标账号，列表会静默变化。把 ``browsing_as`` 删掉这条就红。"""
    respx.get(mc.DOUYIN_AUTH_URL).mock(
        return_value=httpx.Response(200, json=_AUTH_BODY)
    )
    respx.get(mc.DOUYIN_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_SEARCH_BODY)
    )

    body = client.get(_PATH, params={"account_id": 10, "keyword": "起风了"}).json()

    assert body["browsing_as"]["account_id"] == "10"
    assert body["browsing_as"]["username"] == "HEYGO"


@respx.mock
def test_a_two_hundred_that_says_not_logged_in_is_not_an_empty_result(client):
    """**守卫**。creator 站对未登录回 ``status_code:8`` 而 HTTP **是 200**。
    把它当成功，用户会看到一个空面板并读作"平台没有这首歌"。"""
    respx.get(mc.DOUYIN_AUTH_URL).mock(
        return_value=httpx.Response(
            200, json={"status_code": 8, "status_msg": "未登录"}
        )
    )
    resp = client.get(_PATH, params={"account_id": 10, "keyword": "起风了"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["reason"] == mc.REASON_UPSTREAM_SHAPE


@respx.mock
def test_a_search_upstream_error_is_typed_not_empty(client):
    respx.get(mc.DOUYIN_AUTH_URL).mock(
        return_value=httpx.Response(200, json=_AUTH_BODY)
    )
    respx.get(mc.DOUYIN_SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))
    resp = client.get(_PATH, params={"account_id": 10, "keyword": "起风了"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["reason"] == mc.REASON_UPSTREAM_STATUS


@respx.mock
def test_network_failure_is_typed(client):
    respx.get(mc.DOUYIN_AUTH_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    resp = client.get(_PATH, params={"account_id": 10, "keyword": "起风了"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["reason"] == mc.REASON_UPSTREAM_UNREACHABLE


@respx.mock
def test_a_session_that_cannot_be_used_is_a_different_answer_from_a_dead_upstream(
    monkeypatch,
):
    """账号要重新扫码（409）与上游挂了（502）指向两个完全不同的下一步。合并
    它们，用户会去等一个永远不会自己好的东西。"""
    app = _make_app(monkeypatch)

    async def broken_cookie(account_id):
        raise mc.MusicCatalogError(
            mc.REASON_SESSION_UNUSABLE, "reconnect the account", http_status=409
        )

    monkeypatch.setattr(mc, "_account_cookie_header", broken_cookie)
    resp = TestClient(app).get(_PATH, params={"account_id": 10, "keyword": "起风了"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == mc.REASON_SESSION_UNUSABLE


@respx.mock
def test_an_unsupported_platform_never_asks_douyin(client):
    route = respx.get(mc.DOUYIN_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_SEARCH_BODY)
    )
    resp = client.get(
        _PATH, params={"account_id": 10, "keyword": "起风了", "platform": "xiaohongshu"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == mc.REASON_PLATFORM_UNSUPPORTED
    assert not route.called


@respx.mock
def test_an_empty_upstream_list_is_a_two_hundred(client):
    """空列表**只**在上游真的回了空的时候出现 —— 它是成功路径。"""
    respx.get(mc.DOUYIN_AUTH_URL).mock(
        return_value=httpx.Response(200, json=_AUTH_BODY)
    )
    respx.get(mc.DOUYIN_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"status_code": 0, "music": [], "cursor": 0, "has_more": 0}
        )
    )
    resp = client.get(_PATH, params={"account_id": 10, "keyword": "zzzqqqxxx"})
    assert resp.status_code == 200
    assert resp.json()["tracks"] == []


@respx.mock
def test_a_repeated_search_does_not_ask_the_platform_again(client):
    respx.get(mc.DOUYIN_AUTH_URL).mock(
        return_value=httpx.Response(200, json=_AUTH_BODY)
    )
    route = respx.get(mc.DOUYIN_SEARCH_URL).mock(
        return_value=httpx.Response(200, json=_SEARCH_BODY)
    )

    first = client.get(_PATH, params={"account_id": 10, "keyword": "起风了"}).json()
    second = client.get(_PATH, params={"account_id": 10, "keyword": " 起风了 "}).json()

    assert route.call_count == 1
    assert (first["cached"], second["cached"]) == (False, True)
    # 缓存命中时身份仍然要跟着走 —— 否则第二个用户会看到第一个用户的账号名。
    assert second["browsing_as"]["account_id"] == "10"
