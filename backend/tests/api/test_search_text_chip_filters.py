"""``POST /api/v1/search/text`` 必须把筛选 chip 送到 SQL 去。

这条路以前是断的：列表按 chip 过滤（``rpc_downloads_library_search``），搜索走
另一个 RPC 且**一个 chip 都不传**。用户勾了「AI · 已转录」再搜一个词，回来的是
没转录的条目，而 chip 还画在工具栏上显示为激活。

所以这里断言的是**参数真的到了**，不是「调用成功了」——原来的失败模式正是
「调用非常成功，只是少带了用户的意图」。

拒绝体用生产的 ``ErrorResponse`` 外壳（装 ``register_exception_handlers``），
断言读 ``details``/``code`` 而不是裸 ``detail``（CLAUDE.md 2026-09-09）。
"""

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import register_exception_handlers
from app.services.library.search_service import SearchFiltersUnavailable

pytestmark = pytest.mark.unit

# ``app/api/__init__.py`` 把 ``search_router`` 重新绑成了 APIRouter 对象，
# 要 monkeypatch 模块全局只能按完整路径 import（同 test_search_router）。
mod = importlib.import_module("app.api.search_router")


@pytest.fixture
def client(monkeypatch):
    from app.core.deps import get_auth

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(mod.router, prefix="/api/v1")

    async def _grant():
        return SimpleNamespace(user_id="11111111-1111-1111-1111-111111111111")

    app.dependency_overrides[get_auth] = _grant

    calls: list = []

    class _Svc:
        async def search_user_media_text(self, **kw):
            calls.append(kw)
            return []

    monkeypatch.setattr(mod, "SearchService", _Svc)
    return app, calls


async def _post(app, body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post("/api/v1/search/text", json=body)


async def test_the_chips_reach_the_service(client):
    """报障的那一条：勾了「已转录」，搜索必须带着它下去。"""
    app, calls = client
    r = await _post(
        app,
        {
            "query": "抖音",
            "fields": ["title"],
            "filters": {"ai_transcribed": True, "platforms": ["douyin"]},
        },
    )
    assert r.status_code == 200
    assert len(calls) == 1
    f = calls[0]["filters"]
    assert f is not None
    assert f.ai_transcribed is True
    assert f.platforms == ["douyin"]


async def test_no_filters_is_not_an_empty_filter_set(client):
    """没有 chip 的调用方不该被当成「筛掉一切」。

    ``None`` 和「全 None 的 filters 对象」在 SQL 里同义（都走 IS NULL 分支），
    但这里要钉住的是路由不会自己造一个出来。
    """
    app, calls = client
    r = await _post(app, {"query": "abc", "fields": ["title"]})
    assert r.status_code == 200
    assert calls[0]["filters"] is None


async def test_every_chip_survives_the_wire(client):
    """逐字段过一遍。少送一个字段不会报错，只会安静地不生效——所以逐个断言。"""
    app, calls = client
    sent = {
        "tag_ids": ["7", "9"],
        "min_rating": 3,
        "ai_transcribed": True,
        "ai_summarized": True,
        "ai_analyzed": True,
        "ai_has_prompt": True,
        "created_after": "2026-01-01",
        "created_before": "2026-12-31",
        "duration_min": 10,
        "duration_max": 600,
        "aspect_ratios": ["9:16"],
        "platforms": ["douyin", "bilibili"],
        "media_types": ["video"],
        "has_comments": True,
        "min_likes": 100,
        "min_comments": 5,
        "min_favorites": 7,
        "min_shares": 2,
        "social_combine": "or",
    }
    r = await _post(app, {"query": "abc", "fields": ["title"], "filters": sent})
    assert r.status_code == 200
    got = calls[0]["filters"]
    for key, value in sent.items():
        assert getattr(got, key) == value, key


async def test_an_unknown_social_combine_is_refused(client):
    """``social_combine`` 直接进 SQL 的 CASE，取值必须闭合。"""
    app, _ = client
    r = await _post(
        app,
        {"query": "abc", "fields": ["title"], "filters": {"social_combine": "xor"}},
    )
    assert r.status_code == 422


async def test_a_database_without_migration_475_is_a_503_not_silent_results(client):
    """部署窗口期：宁可短暂不可用，也不能回一页无视筛选的结果。

    静默返回未筛选的结果正是这个改动要消掉的故障；把它当降级方案，等于把一次
    短暂的故障变成一个长期看不见的缺陷。
    """
    app, _ = client

    class _Broken:
        async def search_user_media_text(self, **kw):
            raise SearchFiltersUnavailable("migration 475 not applied")

    mod.SearchService = _Broken
    try:
        r = await _post(
            app,
            {
                "query": "abc",
                "fields": ["title"],
                "filters": {"ai_transcribed": True},
            },
        )
    finally:
        pass
    assert r.status_code == 503
    assert r.json()["code"] == "http_503"
