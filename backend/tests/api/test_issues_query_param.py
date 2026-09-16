"""``GET /issues?q=``（3c §2.3）。在此之前 Issues 页的搜索是对**最新 200 行**的
内存子串匹配（侦察 A3）：第 201 件议题在 UI 上不可检索，而这一点不报错，只会
安静地搜不到。

这里钉的是 router → repository 的透传本身：``q`` 原样落到
``list_for_user(q=)``（Task 14 已实现的那一个），没搜时是 ``None`` 而不是空串
——空串会在 repo 侧走进一个 ``%%`` 谓词、让 mig 166 的三个 trgm GIN 失效。
"""

import importlib
import sys

import pytest

pytestmark = pytest.mark.unit


def _mod():
    """importlib + sys.modules, not ``from app.api import issues_router``: the
    package re-exports the APIRouter under that very name, so the plain import
    binds the router OBJECT (同 tests/services/issues/test_dispatch_window_guard.py)."""
    importlib.import_module("app.api.issues_router")
    return sys.modules["app.api.issues_router"]


class _Auth:
    user_id = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def seen(monkeypatch):
    mod = _mod()

    captured: dict = {}

    async def _list(**kw):
        captured.update(kw)
        return ([], 0)

    monkeypatch.setattr(mod.issue_repository, "list_for_user", _list)
    return captured


async def _call(**over):
    mod = _mod()

    kw = {
        "auth": _Auth(),
        "status_filter": None,
        "project_id": None,
        "team_id": None,
        "include_hidden": False,
        "limit": 50,
        "offset": 0,
        "q": None,
    }
    kw.update(over)
    return await mod.list_issues(**kw)


async def test_the_router_hands_q_to_the_repository(seen):
    res = await _call(q="rain")
    assert seen["q"] == "rain"
    # total 是服务端的匹配总数——UI 显示「N of M issues」靠它，而不是靠页面
    # 长度（那个只会说 50）。
    assert res.total == 0


async def test_no_q_is_none_not_an_empty_string(seen):
    await _call()
    assert seen["q"] is None
