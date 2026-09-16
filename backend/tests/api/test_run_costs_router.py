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
TREE = importlib.import_module("app.services.billing.run_tree_points")

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


#: run 1 委派出去一条子 run（11）。扣费逐 run 发生，气泡要显示的是整棵树的合计
#: ——3c 终审 I2。这里让**真的** ``charged_points_for_run_trees`` 跑起来，只桩它
#: 底下那两个仓库，所以钉的是整条链而不是一次转发。
TREES = {"1": ["1", "11"], "2": ["2"], "3": ["3"]}
CHARGED = {"1": 13.0, "11": 4.0}


def _stub(monkeypatch, visible):
    class _Repo:
        async def cost_rows_for_ids(self, ids):
            return [r for r in ROWS if r["id"] in ids]

        async def run_ids_in_trees(self, root_ids):
            return {k: v for k, v in TREES.items() if int(k) in root_ids}

    class _Points:
        async def charged_points_for_references(self, *, reference_type, reference_ids):
            return {k: v for k, v in CHARGED.items() if k in reference_ids}

    async def _visible(issue_ids, auth):
        return visible

    monkeypatch.setattr(R, "get_agent_runs_repository", lambda: _Repo())
    monkeypatch.setattr(TREE, "get_agent_runs_repository", lambda: _Repo())
    monkeypatch.setattr(TREE, "get_points_repository", lambda: _Points())
    monkeypatch.setattr(R, "visible_issue_ids", _visible)


async def test_owner_and_visible_issue_rows_come_back(monkeypatch):
    _stub(monkeypatch, {"77"})
    out = await R.get_run_costs(_Auth(), ids="1,2,3")
    assert sorted(out["items"]) == ["1", "2"]
    assert out["items"]["1"] == {
        "cost_cents": 12.5,
        # 13.0（root 自己）+ 4.0（委派出去那条子 run）—— 气泡回答的是「这次回合
        # 扣了我多少」，而扣费是逐 run 发生的（3c 终审 I2）。
        "charged_points": 17.0,
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


async def test_a_billing_read_failure_is_a_typed_503_not_a_free_run(monkeypatch):
    """积分读挂了就整条 503。降级成「没扣过」会把一批真花了钱的 run 显示成免费——
    与「空结果不是否定结论」同族。"""
    _stub(monkeypatch, {"77"})

    class _Broken:
        async def charged_points_for_references(self, *, reference_type, reference_ids):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(TREE, "get_points_repository", lambda: _Broken())
    with pytest.raises(HTTPException) as e:
        await R.get_run_costs(_Auth(), ids="1")
    assert e.value.status_code == 503
    assert e.value.detail["code"] == "run_costs_unavailable"


async def test_the_tree_read_failing_is_the_same_typed_503(monkeypatch):
    """树结构读不到时**不能**退回「只算 root」——那是一个静默的低报，正是 I2 本身。
    整条 503，与积分读失败同码。"""
    _stub(monkeypatch, {"77"})

    class _BrokenTree:
        async def run_ids_in_trees(self, root_ids):
            raise RuntimeError("db down")

    monkeypatch.setattr(TREE, "get_agent_runs_repository", lambda: _BrokenTree())
    with pytest.raises(HTTPException) as e:
        await R.get_run_costs(_Auth(), ids="1")
    assert e.value.status_code == 503
    assert e.value.detail["code"] == "run_costs_unavailable"


async def test_duplicate_ids_collapse_before_the_fifty_cap(monkeypatch):
    """The cap is on distinct runs, not on commas. A client repainting one
    screen may well send the same run twice; refusing that batch outright
    would be a 400 the user cannot act on, and the work is identical either
    way because the query is an ``IN`` over the deduplicated set."""
    _stub(monkeypatch, {"77"})
    out = await R.get_run_costs(_Auth(), ids="1,1,1,2,2")
    assert sorted(out["items"]) == ["1", "2"]

    # 60 commas, 2 distinct runs — allowed.
    many = ",".join(["1", "2"] * 30)
    assert sorted((await R.get_run_costs(_Auth(), ids=many))["items"]) == ["1", "2"]


async def test_fifty_one_distinct_ids_is_still_a_typed_400():
    with pytest.raises(HTTPException) as e:
        await R.get_run_costs(_Auth(), ids=",".join(str(i) for i in range(1, 53)))
    assert e.value.status_code == 400 and e.value.detail["code"] == "too_many_ids"


async def test_the_endpoint_declares_a_real_response_model():
    """Every other endpoint in this router declares one, and it is what puts the
    batch's shape into the OpenAPI schema the frontend generates types from.

    ``is not None`` would not do: FastAPI infers a response model from the
    return annotation, so a bare ``Dict[str, Any]`` already satisfies that and
    documents nothing. The assertion is that it is a declared schema.
    """
    from pydantic import BaseModel

    route = next(
        r for r in R.router.routes if getattr(r, "path", "") == "/ai-library/runs/costs"
    )
    model = route.response_model
    assert isinstance(model, type) and issubclass(model, BaseModel)
    assert "items" in model.model_fields


async def test_a_run_read_failure_is_a_typed_503_too(monkeypatch):
    """The sibling of the billing-arm test: the runs read has its own arm, and
    an empty batch would read as "these runs cost nothing"."""
    _stub(monkeypatch, {"77"})

    class _Boom:
        async def cost_rows_for_ids(self, ids):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(R, "get_agent_runs_repository", lambda: _Boom())
    with pytest.raises(HTTPException) as e:
        await R.get_run_costs(_Auth(), ids="1,2")
    assert e.value.status_code == 503
    assert e.value.detail["code"] == "run_costs_unavailable"


async def test_a_visibility_read_failure_is_a_typed_503_not_an_empty_batch(monkeypatch):
    """The third arm. If the issue-visibility read fails, every row whose claim
    to visibility runs through an issue silently drops out — the caller gets a
    short batch that looks like "those runs are not yours", which is a claim
    about permissions rather than an outage."""
    _stub(monkeypatch, {"77"})

    async def _boom(issue_ids, auth):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(R, "visible_issue_ids", _boom)
    with pytest.raises(HTTPException) as e:
        await R.get_run_costs(_Auth(), ids="1,2")
    assert e.value.status_code == 503
    assert e.value.detail["code"] == "run_costs_unavailable"
