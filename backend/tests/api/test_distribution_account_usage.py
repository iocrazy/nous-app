"""``GET /accounts/{id}/usage`` —— 解绑确认框的数字从哪来（P0-2 / mig 416）。

「移除」按钮以前直接进库：没有确认框，后端硬 DELETE，而
``publish_task_accounts.account_id`` 是 ``ON DELETE CASCADE``。一次点击可以
静默抹掉一个账号的全部发布记录。2026-08-09 实测生产库，屏幕上两张卡片分别背着
10 条和 0 条记录，用户点掉的恰好是 0 条那张 —— 运气，不是设计。

现在有两半：这个端点提供**实测**数字，前端拿它填确认框。所以这里钉的是

* 数字来自 ``publish_task_accounts`` 的真实 COUNT，不是估算、不写死；
* **0 要如实返回 0**，不能被当成「没有影响」而省掉；
* 归属校验走和其它每账号端点同一个 ``_authorize_account`` 接缝 —— 否则这个
  端点会变成「查任意账号发了多少条」的旁路；
* ``DELETE`` 调的是 ``soft_delete``，不是 ``delete``。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.distribution_router as dr

_USER = "11111111-1111-1111-1111-111111111111"
_ACCOUNT_ID = 335617669826935


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = lambda: None
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": _USER}
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app())


@pytest.fixture
def repos(monkeypatch):
    accounts = MagicMock()
    accounts.soft_delete = AsyncMock()
    publish = MagicMock()
    publish.count_account_publish_records = AsyncMock(return_value=10)
    monkeypatch.setattr(dr, "accounts_repo", accounts)
    monkeypatch.setattr(dr, "publish_repo", publish)

    async def _noop_authorize(account_id, user):
        return None

    monkeypatch.setattr(dr, "_authorize_account", _noop_authorize)
    return accounts, publish


def test_usage_reports_the_measured_publish_record_count(client, repos):
    _, publish = repos
    resp = client.get(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/usage")

    assert resp.status_code == 200
    assert resp.json() == {"publish_records": 10}
    publish.count_account_publish_records.assert_awaited_once_with(_ACCOUNT_ID)


def test_usage_reports_zero_as_zero(client, repos):
    """0 是答案，不是「没有答案」。

    确认框要说的是「它的 0 条发布记录会保留」，而不是在这一档悄悄不显示 ——
    用户点掉的正是 0 条那张卡，而他当时无从知道另一张背着 10 条。
    """
    _, publish = repos
    publish.count_account_publish_records = AsyncMock(return_value=0)

    resp = client.get(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/usage")
    assert resp.status_code == 200
    assert resp.json() == {"publish_records": 0}


def test_usage_is_behind_the_same_ownership_guard(client, repos, monkeypatch):
    """不然它就是一个「随便查别人发了多少条」的旁路。"""
    _, publish = repos

    async def _deny(account_id, user):
        raise HTTPException(status_code=404, detail="Account not found")

    monkeypatch.setattr(dr, "_authorize_account", _deny)

    resp = client.get(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}/usage")
    assert resp.status_code == 404
    publish.count_account_publish_records.assert_not_awaited()


def test_delete_unbinds_instead_of_hard_deleting(client, repos):
    """路由/动词/204 都没变，变的是它不再级联炸掉发布记录。"""
    accounts, _ = repos
    resp = client.delete(f"/api/v1/distribution/accounts/{_ACCOUNT_ID}")

    assert resp.status_code == 204
    accounts.soft_delete.assert_awaited_once_with(_ACCOUNT_ID)
    # MagicMock 会凭空长出任何属性，所以「没调 delete」必须显式断言 ——
    # 否则把 soft_delete 改回 delete 时这个测试仍然是绿的。
    assert not accounts.delete.called
