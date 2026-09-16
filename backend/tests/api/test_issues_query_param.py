"""``GET /issues?q=``（3c §2.3）。在此之前 Issues 页的搜索是对**最新 200 行**的
内存子串匹配（侦察 A3）：第 201 件议题在 UI 上不可检索，而这一点不报错，只会
安静地搜不到。

这里钉的是 router → repository 的透传本身：``q`` 原样落到
``list_for_user(q=)``（Task 14 已实现的那一个），没搜时是 ``None`` 而不是空串
——空串会在 repo 侧走进一个 ``%%`` 谓词、让 mig 166 的三个 trgm GIN 失效。

**全部经 TestClient 走真实路由层**。直接 ``await list_issues()`` 并省略 ``q``
拿到的是 FastAPI 的 ``Query`` 对象而不是 ``None``——那样的「没搜时是 None」只是
在断言我自己刚写下的默认值，而真正决定这件事的是 FastAPI 的参数解析。同理，
``max_length=200`` 只在解析层生效，绕过它就测不到 422。
"""

import importlib
import sys
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

_USER_ID = "11111111-1111-1111-1111-111111111111"


def _mod():
    """importlib + sys.modules, not ``from app.api import issues_router``: the
    package re-exports the APIRouter under that very name, so the plain import
    binds the router OBJECT (同 tests/services/issues/test_dispatch_window_guard.py)."""
    importlib.import_module("app.api.issues_router")
    return sys.modules["app.api.issues_router"]


@pytest.fixture
def seen(monkeypatch) -> dict:
    """Every kwarg the router hands the repository, captured."""
    mod = _mod()
    captured: dict = {}

    async def _list(**kw):
        captured.clear()
        captured.update(kw)
        return ([], 0)

    monkeypatch.setattr(mod.issue_repository, "list_for_user", _list)
    return captured


@pytest.fixture
def client(seen) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(_mod().router, prefix="/api/v1")

    from app.core.deps import get_auth

    class _FakeAuth:
        user_id = _USER_ID
        email = "user@example.com"

    async def _grant():
        return _FakeAuth()

    app.dependency_overrides[get_auth] = _grant
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_router_hands_q_to_the_repository(client: TestClient, seen: dict) -> None:
    res = client.get("/api/v1/issues/", params={"q": "rain"})
    assert res.status_code == 200
    assert seen["q"] == "rain"
    # total 是服务端的匹配总数——UI 显示「N of M issues」靠它，而不是靠页面
    # 长度（那个只会说 50）。
    assert res.json()["total"] == 0


def test_no_q_is_none_not_an_empty_string(client: TestClient, seen: dict) -> None:
    res = client.get("/api/v1/issues/")
    assert res.status_code == 200
    assert seen["q"] is None


def test_a_q_over_200_chars_is_rejected_not_passed_down(
    client: TestClient, seen: dict
) -> None:
    """``max_length=200``。超长必须在解析层挡住：放行的话这串会进 trgm 查询，
    而前端把 422 的整个 ErrorResponse 直接渲成列表错误。"""
    res = client.get("/api/v1/issues/", params={"q": "x" * 201})
    assert res.status_code == 422
    assert seen == {}, "被拒绝的请求不该抵达 repository"


def test_exactly_200_chars_is_still_accepted(client: TestClient, seen: dict) -> None:
    """边界是包含的——否则上面那条测的就是 199 和 201 之间的某个未知位置。"""
    res = client.get("/api/v1/issues/", params={"q": "x" * 200})
    assert res.status_code == 200
    assert seen["q"] == "x" * 200
