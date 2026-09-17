"""``GET /ai-library/usage/efficiency``（3c §3.3）。跨团队汇总在 SQL 层没有兜底
（agent_runs 不在 SCOPE_ENFORCE 清单里），边界完全由端点自己负责——所以「非成员拿
404」是本文件的主角，不是附赠。"""

import importlib

import pytest
from fastapi import HTTPException

# app.api.__init__ rebinds the module name to the router object, so reach the
# module (and its patchable factory names) via importlib — same as the sibling
# run-router tests.
R = importlib.import_module("app.api.ai_library_router")

pytestmark = pytest.mark.unit
GROUPS = [
    {
        "key": "doubao",
        "run_count": 10,
        "failed_runs": 1,
        "total_ms": 410000,
        "timed_runs": 10,
        "tool_calls": 40,
        "tool_errors": 4,
        "deliverables": 8,
        "cost_cents": 32.0,
    }
]
REASONS = {"completed": 8, "awaiting_input": 1, "error": 1}


class _Auth:
    user_id = "11111111-1111-1111-1111-111111111111"


def _stub(monkeypatch, rows=GROUPS, captured=None):
    class _Repo:
        async def efficiency_groups(self, **kw):
            if captured is not None:
                captured.update(kw)
            return rows, REASONS

    monkeypatch.setattr(R, "get_agent_runs_repository", lambda: _Repo())


async def test_user_scope_is_hard_locked_and_the_row_carries_rate_and_ratio(
    monkeypatch,
):
    captured: dict = {}
    _stub(monkeypatch, captured=captured)
    out = await R.get_usage_efficiency(_Auth(), scope="user", group_by="model")
    assert str(captured["user_id"]) == _Auth.user_id
    assert captured["team_id"] is None and captured["project_id"] is None
    g = out.groups[0]
    assert (g.run_count, g.failed_runs, g.avg_run_ms) == (10, 1, 41000)
    assert (g.tool_error_rate, g.cost_per_deliverable_cents) == (0.1, 4.0)
    assert out.turn_end_reasons == REASONS


async def test_zero_denominators_read_differently(monkeypatch):
    """0 次调用 → 错误率 0（确定没错过）；0 件产出 → 单价 null（不知道，不是免费）。"""
    _stub(
        monkeypatch,
        rows=[{**GROUPS[0], "tool_calls": 0, "tool_errors": 0, "deliverables": 0}],
    )
    g = (await R.get_usage_efficiency(_Auth(), scope="user")).groups[0]
    assert g.tool_error_rate == 0.0 and g.cost_per_deliverable_cents is None


async def test_a_group_with_output_but_no_cost_reads_as_unknown_not_free(monkeypatch):
    """3c 终审 I5：``cost_cents`` 带 ``FILTER (parent_run_id IS NULL)``，产出五列不带。

    一次委派里父用 A 模型、子用 B 模型时，整棵树的钱进 A 组、子的产出进 B 组 ——
    于是 B 组拿到 ``cost_cents=0`` 且 ``deliverables>0``，算出来是 ``¢0.00 /
    output``，界面在说「这些产出是免费的」。与「0 件产出 → null」同一条纪律：
    算不出单价就说不知道，别编一个 0 出来。
    """
    _stub(monkeypatch, rows=[{**GROUPS[0], "cost_cents": 0.0, "deliverables": 5}])
    g = (await R.get_usage_efficiency(_Auth(), scope="user")).groups[0]
    assert g.cost_per_deliverable_cents is None
    # 产出本身照报 —— 不知道的是单价，不是有没有干活。
    assert g.deliverables == 5 and g.cost_cents == 0.0


async def test_team_scope_404s_for_a_non_member(monkeypatch):
    class _Teams:
        async def get_team_by_id(self, team_id, user_id):
            return None

    monkeypatch.setattr(R, "get_team_repository", lambda: _Teams())
    with pytest.raises(HTTPException) as e:
        await R.get_usage_efficiency(_Auth(), scope="team", id=42)
    assert e.value.status_code == 404


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"scope": "team"}, "scope_requires_id"),
        ({"scope": "user", "group_by": "module"}, "invalid_group_by"),
        ({"scope": "nope"}, "invalid_scope"),
    ],
)
async def test_bad_input_is_a_typed_400(kwargs, code):
    with pytest.raises(HTTPException) as e:
        await R.get_usage_efficiency(_Auth(), **kwargs)
    assert e.value.status_code == 400 and e.value.detail["code"] == code


async def test_the_wire_body_carries_the_from_alias(monkeypatch):
    """Through the real ASGI stack: the route resolves, and ``from_`` leaves as
    ``from``. Direct calls return the model, so they never see the alias — and a
    client reading ``body["from"]`` would break silently if it regressed."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_auth

    _stub(monkeypatch)
    app = FastAPI()
    app.include_router(R.router, prefix="/api/v1")

    async def _fake_auth():
        return _Auth()

    app.dependency_overrides[get_auth] = _fake_auth
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/v1/ai-library/usage/efficiency?scope=user")
    assert resp.status_code == 200
    body = resp.json()
    assert "from" in body and "to" in body and "from_" not in body
    assert body["groups"][0]["cost_per_deliverable_cents"] == 4.0
    assert body["turn_end_reasons"] == REASONS


async def test_an_over_long_window_is_refused_with_the_shared_cap():
    """One cap, shared with /usage/summary. An unbounded window on agent_runs
    is a full-table scan on the busiest table in the schema — and the column it
    ranges over is the only thing keeping the query off every row ever written.
    """
    with pytest.raises(HTTPException) as e:
        await R.get_usage_efficiency(
            _Auth(), scope="user", frm="2020-01-01", to="2026-01-01"
        )
    assert e.value.status_code == 400 and e.value.detail["code"] == "range_too_long"


async def test_the_cap_is_the_same_number_both_endpoints_use():
    """Not 'both happen to be 366' — literally the same constant. Two copies
    drift, and the one that drifts upward is the one nobody notices."""
    from app.utils.time_window import MAX_RANGE_DAYS

    ur = importlib.import_module("app.api.usage_router")
    assert ur._MAX_RANGE_DAYS is MAX_RANGE_DAYS


def _teams(member: bool):
    class _Teams:
        calls: list = []

        async def get_team_by_id(self, team_id, user_id):
            _Teams.calls.append((team_id, user_id))
            return {"id": team_id} if member else None

    _Teams.calls = []
    return _Teams


async def test_team_scope_passes_the_team_down_and_does_not_lock_the_user(monkeypatch):
    """The mirror image of the user-scope test, and the one that matters more.

    A scope argument that fails to reach the repository is not a slow query on
    this chain — ``agent_runs`` is not in the SCOPE_ENFORCE list, so the SQL
    layer adds no predicate of its own, and a dropped ``team_id`` means every
    team's runs come back. Asserting the response would not catch it; only the
    kwargs will.
    """
    captured: dict = {}
    _stub(monkeypatch, captured=captured)
    teams = _teams(member=True)
    monkeypatch.setattr(R, "get_team_repository", lambda: teams())

    out = await R.get_usage_efficiency(_Auth(), scope="team", id=42)
    assert captured["team_id"] == 42
    assert captured["user_id"] is None and captured["project_id"] is None
    assert out.scope == "team"
    # The gate was asked about THIS caller, not merely about the team.
    assert teams.calls == [("42", _Auth.user_id)]


def _projects(row):
    class _Projects:
        asked: list = []

        async def get_project_by_id(self, project_id):
            _Projects.asked.append(project_id)
            return row

    _Projects.asked = []
    return _Projects


async def test_project_scope_resolves_its_team_then_passes_the_project_down(
    monkeypatch,
):
    captured: dict = {}
    _stub(monkeypatch, captured=captured)
    projects = _projects({"id": 9, "team_id": 42})
    teams = _teams(member=True)
    monkeypatch.setattr(R, "get_projects_repository", lambda: projects())
    monkeypatch.setattr(R, "get_team_repository", lambda: teams())

    out = await R.get_usage_efficiency(_Auth(), scope="project", id=9)
    assert projects.asked == ["9"]
    # Gated on the project's OWNING team, not on the project id.
    assert teams.calls == [("42", _Auth.user_id)]
    assert captured["project_id"] == 9
    assert captured["team_id"] is None and captured["user_id"] is None
    assert out.scope == "project"


async def test_project_scope_404s_for_a_non_member_of_its_team(monkeypatch):
    projects = _projects({"id": 9, "team_id": 42})
    teams = _teams(member=False)
    monkeypatch.setattr(R, "get_projects_repository", lambda: projects())
    monkeypatch.setattr(R, "get_team_repository", lambda: teams())
    with pytest.raises(HTTPException) as e:
        await R.get_usage_efficiency(_Auth(), scope="project", id=9)
    assert e.value.status_code == 404 and e.value.detail["code"] == "not_found"


async def test_a_project_that_does_not_resolve_is_404_not_an_unscoped_read(monkeypatch):
    """A missing project must not fall through with ``team_id`` unset — that
    would be an aggregate with no tenant predicate at all."""
    monkeypatch.setattr(R, "get_projects_repository", lambda: _projects(None)())
    monkeypatch.setattr(R, "get_team_repository", lambda: _teams(member=True)())
    with pytest.raises(HTTPException) as e:
        await R.get_usage_efficiency(_Auth(), scope="project", id=9)
    assert e.value.status_code == 404


async def test_a_repository_failure_is_a_typed_503_not_an_empty_table(monkeypatch):
    """An empty efficiency table reads as "nothing ran and nothing was spent".
    The read failing must not be able to say that."""

    class _Boom:
        async def efficiency_groups(self, **kw):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(R, "get_agent_runs_repository", lambda: _Boom())
    with pytest.raises(HTTPException) as e:
        await R.get_usage_efficiency(_Auth(), scope="user")
    assert e.value.status_code == 503
    assert e.value.detail["code"] == "efficiency_unavailable"


@pytest.mark.parametrize("grouping", ["model", "agent"])
async def test_the_response_echoes_the_grouping_it_used(monkeypatch, grouping):
    """Same shape as /usage/summary. A chart legend that has to remember what it
    asked for will eventually render one grouping's data under another's title.

    ``agent`` is the case that carries the test: asserting only the default
    grouping would pass even if the endpoint stopped echoing the value
    altogether, because the schema would supply it.
    """
    _stub(monkeypatch)
    out = await R.get_usage_efficiency(_Auth(), scope="user", group_by=grouping)
    assert out.group_by == grouping


async def test_a_reversed_window_is_a_typed_400(monkeypatch):
    """The other half of the shared ``window_error`` contract. ``range_too_long``
    already has a case; without this one, deleting the ``invalid_range`` arm or
    renaming its code goes unnoticed."""

    def _must_not_run():
        raise AssertionError("the window was rejected too late")

    monkeypatch.setattr(R, "get_agent_runs_repository", _must_not_run)
    with pytest.raises(HTTPException) as e:
        await R.get_usage_efficiency(
            _Auth(), scope="user", frm="2026-09-15", to="2026-09-01"
        )
    assert e.value.status_code == 400 and e.value.detail["code"] == "invalid_range"
