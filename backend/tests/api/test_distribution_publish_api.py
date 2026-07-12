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
