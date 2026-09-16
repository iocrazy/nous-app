"""``GET /ai-library/runs/costs``（3c §4.2 的数据源）。可见性 = 自己的 run，或一个自己
看得见的议题上的 run。**看不见的 id 是键省略，不是 404**——一次批量里混进一个别人的
run，不该把另外 49 个也打掉。"""

import importlib

import pytest
from fastapi import HTTPException

# app.api.__init__ rebinds the module name to the router object, so reach the
# module (and its patchable factory names) via importlib — same as the sibling
# run-router tests.
R = importlib.import_module("app.api.ai_library_router")

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"
SOMEONE = "22222222-2222-2222-2222-222222222222"
ROWS = [
    {
        "id": 1,
        "user_id": ME,
        "issue_id": None,
        "cost_cents": 12.5,
        "model": "doubao",
        "status": "completed",
        "prompt_tokens": 900,
        "completion_tokens": 120,
    },
    {
        "id": 2,
        "user_id": SOMEONE,
        "issue_id": 77,
        "cost_cents": 3.0,
        "model": "qwen",
        "status": "failed",
        "prompt_tokens": 10,
        "completion_tokens": 0,
    },
    {
        "id": 3,
        "user_id": SOMEONE,
        "issue_id": 99,
        "cost_cents": 1.0,
        "model": "qwen",
        "status": "completed",
        "prompt_tokens": 5,
        "completion_tokens": 5,
    },
]


class _Auth:
    user_id = ME


def _stub(monkeypatch, visible):
    class _Repo:
        async def cost_rows_for_ids(self, ids):
            return [r for r in ROWS if r["id"] in ids]

    class _Points:
        async def charged_points_for_references(self, *, reference_type, reference_ids):
            return {"1": 13.0}

    async def _visible(issue_ids, auth):
        return visible

    monkeypatch.setattr(R, "get_agent_runs_repository", lambda: _Repo())
    monkeypatch.setattr(R, "get_points_repository", lambda: _Points())
    monkeypatch.setattr(R, "visible_issue_ids", _visible)


async def test_owner_and_visible_issue_rows_come_back(monkeypatch):
    _stub(monkeypatch, {"77"})
    out = await R.get_run_costs(_Auth(), ids="1,2,3")
    assert sorted(out["items"]) == ["1", "2"]
    assert out["items"]["1"] == {
        "cost_cents": 12.5,
        "charged_points": 13.0,
        "model": "doubao",
        "status": "completed",
        "prompt_tokens": 900,
        "completion_tokens": 120,
    }
    # 没扣过就是 None，不是 0——BYOK / 急停关闭都长这样。
    assert out["items"]["2"]["charged_points"] is None


async def test_invisible_and_garbage_ids_are_dropped_not_fatal(monkeypatch):
    _stub(monkeypatch, set())
    assert list((await R.get_run_costs(_Auth(), ids="1,3"))["items"]) == ["1"]
    assert list((await R.get_run_costs(_Auth(), ids="1,,abc, 3 "))["items"]) == ["1"]


async def test_more_than_fifty_ids_is_a_typed_400():
    with pytest.raises(HTTPException) as e:
        await R.get_run_costs(_Auth(), ids=",".join(str(i) for i in range(51)))
    assert e.value.status_code == 400 and e.value.detail["code"] == "too_many_ids"


async def test_the_literal_path_actually_reaches_this_endpoint(monkeypatch):
    """``/runs/costs`` must be registered before ``/runs/{run_id}``.

    Every other test in this file calls the function directly, so none of them
    can see the one failure that matters in production: if the parameterised
    route wins, ``costs`` is read as a run id and the caller gets 404 "run not
    found" from a completely different handler. This asserts through the real
    ASGI router, which is the only place that ordering exists.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_auth

    _stub(monkeypatch, {"77"})
    app = FastAPI()
    app.include_router(R.router, prefix="/api/v1")

    async def _fake_auth():
        return _Auth()

    app.dependency_overrides[get_auth] = _fake_auth
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/ai-library/runs/costs?ids=1,2,3")
    assert resp.status_code == 200
    assert sorted(resp.json()["items"]) == ["1", "2"]
