"""``GET /api/v1/search`` 的参数契约（3c §2.3）。

**拒绝体是生产的 ``ErrorResponse`` 外壳。** 这里装了
``register_exception_handlers``，所以一次 400 到达客户端时长这样::

    {"success": false, "error": …, "code": "http_400",
     "request_id": …, "details": {"code": "query_too_short", …}}

裸 ``FastAPI()`` 会给回 ``{"detail": …}``，那个形状从不出货——照它写断言就是
2026-09-09 那次「单测全绿、真栈每个拒绝都塌成 http_400」的复刻（CLAUDE.md）。
前端读的是 ``details.code``，所以断言也读它。
"""

import importlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import register_exception_handlers

pytestmark = pytest.mark.unit

# ``app/api/__init__.py`` 把 ``search_router`` 这个名字重新绑到了 APIRouter
# 对象上，所以 ``from app.api import search_router`` 拿到的是路由不是模块。
# 要 monkeypatch 模块全局，只能按完整路径 import（同 test_outputs_router）。
#
# ⚠️ 这个 router **不是**本 Task 新建的：``/search`` 前缀早就归它，上面挂着
# 资源库检索的五条子路径。本 Task 加的是裸路径 ``GET ""``，所以 fixture 里
# 整个 router 都会被挂上——下面的断言只碰 ``/api/v1/search`` 这一条。
mod = importlib.import_module("app.api.search_router")


@pytest.fixture
def client(monkeypatch):
    from types import SimpleNamespace

    from app.core.deps import get_auth
    from app.schemas.unified_search import (
        SearchGroups,
        SearchTotals,
        UnifiedSearchResponse,
    )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(mod.router, prefix="/api/v1")

    async def _grant():
        return SimpleNamespace(user_id="11111111-1111-1111-1111-111111111111")

    app.dependency_overrides[get_auth] = _grant
    calls: list = []

    async def _search(**kw):
        calls.append(kw)
        return UnifiedSearchResponse(
            groups=SearchGroups(), totals=SearchTotals(), took_ms=1
        )

    monkeypatch.setattr(mod, "unified_search", _search)
    return app, calls


async def _get(app, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/api/v1/search", params=params)


async def test_a_short_query_is_a_typed_400_after_stripping(client):
    res = await _get(client[0], q="  r  ")
    assert res.status_code == 400
    # detail 必须是 dict——生产的 ErrorResponse 外壳只让 dict 活到 details；
    # 字符串会塌成 http_400 而前端读的是 details.code（CLAUDE.md 2026-09-09）。
    assert res.json()["details"]["code"] == "query_too_short"


async def test_kinds_default_to_all_three(client):
    app, calls = client
    await _get(app, q="rain")
    assert calls[-1]["kinds"] == {"issue", "run", "output"}


async def test_an_unknown_kind_is_dropped_but_all_unknown_is_a_typed_400(client):
    app, calls = client
    res = await _get(app, q="rain", kinds="issue,bogus")
    assert res.status_code == 200 and calls[-1]["kinds"] == {"issue"}
    body = (await _get(app, q="rain", kinds="bogus")).json()
    assert body["details"]["code"] == "unknown_kinds"


async def test_an_empty_kinds_param_means_all_not_none(client):
    """``?kinds=`` 是「没挑」，不是「一个都不认得」。

    拒绝它会让一个空 filter chip 变成 400——而 ``none of [] is a searchable
    kind`` 这句话对读日志的人也毫无意义。
    """
    app, calls = client
    res = await _get(app, q="rain", kinds="")
    assert res.status_code == 200 and calls[-1]["kinds"] == {"issue", "run", "output"}


async def test_the_stripped_term_is_what_reaches_the_service(client):
    """服务层拿到的是 strip 过的词。带空白的模式会让 ``%  rain  %`` 匹配不到
    任何东西，而长度校验又已经按 strip 后判过——两处口径必须是同一个词。"""
    app, calls = client
    await _get(app, q="  rain  ")
    assert calls[-1]["q"] == "rain"


async def test_the_scope_filters_ride_through_untouched(client):
    app, calls = client
    await _get(app, q="rain", team_id=7, project_id=3, issue_id=96, limit_per_group=5)
    call = calls[-1]
    assert (call["team_id"], call["project_id"], call["issue_id"]) == (7, 3, 96)
    assert call["limit_per_group"] == 5


async def test_an_out_of_range_page_size_is_refused_by_the_signature(client):
    # 上限在签名里（``le=50``）。没有它，一次 limit=100000 会让 SQL 层被要求
    # 取三十万行——分组裁剪在 Python 里做，代价全落在数据库上。
    assert (await _get(client[0], q="rain", limit_per_group=999)).status_code == 422
