import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.distribution_router as dr


def _make_app(monkeypatch, *, module_on=True) -> TestClient:
    async def fake_require():
        if not module_on:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not found")

    app = FastAPI()
    app.include_router(dr.router, prefix="/api/v1")
    app.dependency_overrides[dr.require_distribution] = fake_require
    app.dependency_overrides[dr.get_current_user] = lambda: {"id": "u-1"}
    return app


def test_create_task_flag_off_404(monkeypatch):
    app = _make_app(monkeypatch, module_on=False)
    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={"title": "x", "resource_ids": ["1"], "account_ids": ["10"]},
    )
    assert resp.status_code == 404


def test_create_task_dispatches_and_returns_accounts(monkeypatch):
    app = _make_app(monkeypatch, module_on=True)
    dispatched = {}

    # authorize every supplied account to the caller
    async def fake_authorize_account(account_id, user):
        return {"id": str(account_id), "scope_type": "user", "scope_id": "u-1"}

    async def fake_create_task(**f):
        return {
            "id": "700",
            "title": f["title"],
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-07-08T00:00:00Z",
        }

    created_accounts = []

    async def fake_create_task_account(**f):
        row = {
            "id": str(len(created_accounts) + 1),
            "account_id": f["account_id"],
            "username": "HEYGO",
            "avatar_url": None,
            "channel": f.get("channel", "h5"),
            "status": "pending",
        }
        created_accounts.append(row)
        return row

    async def fake_get_task(task_id):
        return {
            "id": "700",
            "title": "x",
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-07-08T00:00:00Z",
            "dbos_workflow_id": "wf-1",
        }

    async def fake_get_task_accounts(task_id):
        return created_accounts

    async def fake_set_wf(task_id, wf_id):
        dispatched["set_wf"] = wf_id

    class _Mgr:
        async def create(self, **kw):
            dispatched["tt_create"] = kw.get("dbos_workflow_id")
            return kw.get("dbos_workflow_id")

    async def fake_start_wf(*a, **kw):
        dispatched["start_wf"] = kw.get("workflow_id")
        return {"mode": "dbos"}

    monkeypatch.setattr(dr, "_authorize_account", fake_authorize_account)
    monkeypatch.setattr(dr.publish_repo, "create_task", fake_create_task)
    monkeypatch.setattr(
        dr.publish_repo, "create_task_account", fake_create_task_account
    )
    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)

    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Launch",
            "resource_ids": ["30"],
            "account_ids": ["10", "11"],
            "channel": "h5",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "700"
    assert len(body["accounts"]) == 2
    # task_tracking id and the dispatched workflow_id are the SAME wf_id (路线 C)
    assert dispatched["tt_create"] == dispatched["start_wf"] == dispatched["set_wf"]


def _wire_repo_spies(monkeypatch, spy):
    """把 create_task 会走到的每一处写入都换成探针。

    校验前移（图集设计 §2 D3）的全部意义就是"被拒的批次一行都不建"，
    所以这些探针里**任何一个**被碰到，前移就没做到 —— 断言的是缺席，
    不是返回码。
    """

    async def fake_create_task(**f):
        spy.setdefault("create_task", []).append(f)
        return {
            "id": "700",
            "title": f["title"],
            "content_type": f.get("content_type", "video"),
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-08-11T00:00:00Z",
        }

    async def fake_create_task_account(**f):
        row = {
            "id": str(len(spy.get("create_task_account", [])) + 1),
            "account_id": f["account_id"],
            "username": "HEYGO",
            "channel": f.get("channel", "h5"),
            "status": "pending",
        }
        spy.setdefault("create_task_account", []).append(row)
        return row

    async def fake_get_task(task_id):
        return {
            "id": "700",
            "title": "x",
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-08-11T00:00:00Z",
        }

    async def fake_get_task_accounts(task_id):
        return spy.get("create_task_account", [])

    async def fake_set_wf(task_id, wf_id):
        spy.setdefault("set_wf", []).append(wf_id)

    class _Mgr:
        async def create(self, **kw):
            spy.setdefault("tt_create", []).append(kw.get("dbos_workflow_id"))
            return kw.get("dbos_workflow_id")

    async def fake_start_wf(*a, **kw):
        spy.setdefault("start_wf", []).append(kw.get("workflow_id"))
        return {"mode": "dbos"}

    monkeypatch.setattr(dr.publish_repo, "create_task", fake_create_task)
    monkeypatch.setattr(
        dr.publish_repo, "create_task_account", fake_create_task_account
    )
    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)


def _authorize_returning(accounts):
    async def fake_authorize_account(account_id, user):
        return accounts[str(account_id)]

    return fake_authorize_account


def test_image_post_mixing_session_and_oauth_accounts_creates_no_row(monkeypatch):
    """D3 的核心动机：``decide_channel`` 会把 OAuth 账号降级到 h5，一个图集
    批次于是一半走浏览器一半走 H5 交接。前移之后，混选在提交时就被拒 ——
    **没有任务行、没有 task_tracking 行、没有 workflow 派发**。"""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    monkeypatch.setattr(
        dr,
        "_authorize_account",
        _authorize_returning(
            {
                "10": {"id": "10", "platform": "douyin", "auth_type": "session"},
                "11": {"id": "11", "platform": "douyin", "auth_type": "oauth"},
            }
        ),
    )
    # 生产此刻仍声明 content_types={"video"}（T7 才翻转），所以这里把画像翻过来，
    # 让拒绝理由落在"混选"这一条上而不是 unsupported_content_type。
    from dataclasses import replace

    from app.services.distribution.session_adapter import SESSION_PLATFORM_PROFILES

    monkeypatch.setitem(
        SESSION_PLATFORM_PROFILES,
        "douyin",
        replace(
            SESSION_PLATFORM_PROFILES["douyin"],
            content_types=frozenset({"video", "images"}),
        ),
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Summer Trip",
            "content_type": "images",
            "resource_ids": ["1", "2", "3"],
            "account_ids": ["10", "11"],
            "channel": "session",
        },
    )

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["reason"] == "publish_intent_rejected"
    assert [p["reason"] for p in detail["problems"]] == ["account_not_session_bound"]
    assert detail["problems"][0]["account_id"] == "11"
    # 一行都没建，一次都没派发。
    assert spy == {}


def test_an_accepted_batch_still_creates_its_rows(monkeypatch):
    """对照组：同一条路径在放行时**确实**会建行 —— 否则上面那个"什么都没发生"
    的断言可能只是因为整个链路被打断了。"""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    monkeypatch.setattr(
        dr,
        "_authorize_account",
        _authorize_returning(
            {"10": {"id": "10", "platform": "douyin", "auth_type": "session"}}
        ),
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Summer Trip",
            "content_type": "video",
            "resource_ids": ["30"],
            "account_ids": ["10"],
            "channel": "session",
        },
    )

    assert resp.status_code == 200, resp.text
    assert len(spy["create_task"]) == 1
    assert len(spy["create_task_account"]) == 1
    assert spy["start_wf"] == spy["tt_create"] == spy["set_wf"]


def test_a_platform_without_a_publisher_creates_no_row(monkeypatch):
    """小红书能绑账号但发不了。走到 workflow 也必然是一行 failed —— 提交时
    就说清楚，别在记录页上留一条注定失败的批次。"""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}
    _wire_repo_spies(monkeypatch, spy)
    monkeypatch.setattr(
        dr,
        "_authorize_account",
        _authorize_returning(
            {"12": {"id": "12", "platform": "xiaohongshu", "auth_type": "session"}}
        ),
    )

    client = TestClient(app)
    resp = client.post(
        "/api/v1/distribution/tasks",
        json={
            "title": "Summer Trip",
            "resource_ids": ["30"],
            "account_ids": ["12"],
            "channel": "session",
        },
    )

    assert resp.status_code == 422, resp.text
    assert [p["reason"] for p in resp.json()["detail"]["problems"]] == [
        "publishing_not_implemented"
    ]
    assert spy == {}


def _fake_task_with_wf():
    async def fake_get_task(task_id):
        return {
            "id": "700",
            "user_id": "u-1",
            "title": "x",
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-07-08T00:00:00Z",
            "dbos_workflow_id": "wf-old",
        }

    return fake_get_task


def test_retry_non_terminal_task_409(monkeypatch):
    """retry_task() returning None (task not failed/cancelled/lost) must NOT
    re-dispatch a second, untracked DBOS workflow — it must 409 instead."""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}

    async def fake_get_task_accounts(task_id):
        return []

    async def fake_reset_failed_accounts(task_id):
        spy["reset_failed_accounts"] = task_id

    async def fake_set_wf(task_id, wf_id):
        spy["set_wf"] = wf_id

    class _Mgr:
        async def retry_task(self, old_wf, user_id, *, new_workflow_id=None):
            spy["retry_task_called_with"] = (old_wf, user_id, new_workflow_id)
            return None  # simulate: task not in a terminal state

    async def fake_start_wf(*a, **kw):
        spy["start_wf"] = kw.get("workflow_id")
        return {"mode": "dbos"}

    monkeypatch.setattr(dr.publish_repo, "get_task", _fake_task_with_wf())
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(
        dr.publish_repo, "reset_failed_accounts", fake_reset_failed_accounts
    )
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)

    client = TestClient(app)
    resp = client.post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 409, resp.text
    assert "retry_task_called_with" in spy  # manager was consulted
    assert "reset_failed_accounts" not in spy
    assert "set_wf" not in spy
    assert "start_wf" not in spy


def test_retry_terminal_task_redispatches(monkeypatch):
    """retry_task() returning a truthy result (task WAS failed/cancelled/lost)
    must re-dispatch under the SAME new wf_id used to re-key task_tracking."""
    app = _make_app(monkeypatch, module_on=True)
    spy = {}

    async def fake_get_task_accounts(task_id):
        return []

    async def fake_reset_failed_accounts(task_id):
        spy["reset_failed_accounts"] = task_id

    async def fake_set_wf(task_id, wf_id):
        spy["set_wf"] = wf_id

    class _Mgr:
        async def retry_task(self, old_wf, user_id, *, new_workflow_id=None):
            spy["retry_task_called_with"] = (old_wf, user_id, new_workflow_id)
            return {"dbos_workflow_id": new_workflow_id}

    async def fake_start_wf(*a, **kw):
        spy["start_wf"] = kw.get("workflow_id")
        return {"mode": "dbos"}

    monkeypatch.setattr(dr.publish_repo, "get_task", _fake_task_with_wf())
    monkeypatch.setattr(dr.publish_repo, "get_task_accounts", fake_get_task_accounts)
    monkeypatch.setattr(
        dr.publish_repo, "reset_failed_accounts", fake_reset_failed_accounts
    )
    monkeypatch.setattr(dr.publish_repo, "set_task_workflow_id", fake_set_wf)
    monkeypatch.setattr(dr, "get_task_manager", lambda: _Mgr())
    monkeypatch.setattr(dr, "start_workflow_routed", fake_start_wf)

    client = TestClient(app)
    resp = client.post("/api/v1/distribution/tasks/700/retry")

    assert resp.status_code == 200, resp.text
    assert spy["reset_failed_accounts"] == 700
    assert spy["set_wf"] == spy["start_wf"]
    # same new wf_id flowed through retry_task -> set_task_workflow_id -> dispatch
    assert spy["retry_task_called_with"][2] == spy["set_wf"]


def test_authorize_task_cross_user_404(monkeypatch):
    """IDOR guard: a task owned by another user must 404, not 403 — existence
    of the task must not be leaked to a non-owner."""
    app = _make_app(monkeypatch, module_on=True)

    async def fake_get_task(task_id):
        return {
            "id": "700",
            "user_id": "someone-else",
            "title": "x",
            "content_type": "video",
            "topics": [],
            "visibility": "public",
            "distribution_mode": "broadcast",
            "created_at": "2026-07-08T00:00:00Z",
            "dbos_workflow_id": "wf-old",
        }

    monkeypatch.setattr(dr.publish_repo, "get_task", fake_get_task)

    client = TestClient(app)
    resp = client.get("/api/v1/distribution/tasks/700")

    assert resp.status_code == 404, resp.text
