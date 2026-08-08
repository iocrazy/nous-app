"""autopilot_sweep — the 5-minute backstop sweep (task O2, review adjacent
minor fix: the eligible-project SQL's boolean check).

Phase B5 Task 2: the eligible-project scan moved from a raw
``_ELIGIBLE_PROJECTS_SQL`` string to ``_eligible_projects_stmt()``, an ORM
select executed inside ``read_scope()``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List

import pytest
from sqlalchemy.dialects import postgresql

from app.workflows import autopilot_sweep

pytestmark = pytest.mark.asyncio


def test_eligible_projects_stmt_uses_string_equality_not_boolean_cast():
    """Review adjacent-minor fix: a ``::boolean`` cast RAISES on any row
    whose ``events->>'auto_start'`` isn't a Postgres-recognized boolean
    literal, and that raise aborts the ENTIRE query — one dirty row would
    take down the global sweep for every OTHER project too. Plain string
    equality never raises; Pydantic's ``WorkflowNodeEvents.auto_start: bool``
    always serializes as the JSON literal ``true``/``false`` so no real match
    is lost."""
    sql = str(
        autopilot_sweep._eligible_projects_stmt(200).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "CAST" not in sql.upper() or "::boolean" not in sql
    assert "events ->> 'auto_start') = 'true'" in sql


def test_eligible_projects_stmt_is_column_level_not_entity_level():
    """``select(Projects.id)`` — a single explicit column — not
    ``select(Projects)``, which would map each row to an entity-keyed
    ``{"Projects": <instance>}`` instead of ``{"id": ...}`` (see
    tests/test_orm_b5_task2_row_shape_e2e.py for the real-engine proof)."""
    stmt = autopilot_sweep._eligible_projects_stmt(200)
    assert list(stmt.selected_columns.keys()) == ["id"]


def test_eligible_projects_stmt_covers_cascade_pending_episodes():
    """B4 点火实测(2026-08-08):surface 自动完成把节点推到 done 后,若当下的
    tick enqueue 丢失(DBOS step 内触发、临时进程、worker 重启),旧判定只认
    auto_start 节点——纯 cascade 停摆的项目永远不会被 sweep 补,「5 分钟兜底」
    对它们不成立。新增 OR 支:某集游标(episodes.current_node_id)仍指着一个
    已 done 的节点 = cascade 欠账,一并入选。"""
    sql = str(
        autopilot_sweep._eligible_projects_stmt(200).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "episodes" in sql
    assert "current_node_id" in sql
    assert "'done'" in sql
    assert " OR " in sql


def test_query_step_never_enqueues_from_inside_a_dbos_step():
    """Regression (live E2E probe, 2026-07-31): the step used to call
    ``enqueue_autopilot_tick`` itself, and ``DBOS.start_workflow`` from inside
    a ``@DBOS.step`` raises a bare AssertionError. Every per-project enqueue
    was wrapped in its own try/except, so the sweep swallowed that assertion
    and reported success while never enqueueing a single tick — for any
    project, ever. The step is query-only now; the enqueue loop lives in the
    workflow-context impl."""
    import inspect

    src = inspect.getsource(autopilot_sweep.list_eligible_autopilot_projects_step)
    assert "enqueue_autopilot_tick" not in src
    assert "enqueue_autopilot_tick" in inspect.getsource(
        autopilot_sweep._autopilot_sweep_impl
    )


async def test_query_step_skips_gracefully_when_db_not_configured(monkeypatch):
    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: False)

    assert await autopilot_sweep.list_eligible_autopilot_projects_step() == []


async def test_query_step_returns_eligible_project_ids(monkeypatch):
    """Phase B5 Task 2: the scan moved from ``db_engine.fetch_all`` (raw SQL)
    to an ORM select executed inside ``read_scope()`` — patch the session
    scope, not the retired db_engine call."""
    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    class _FakeResult:
        def mappings(self):
            return self

        def all(self):
            return [{"id": 100}, {"id": 200}]

    class _FakeSession:
        async def execute(self, stmt):
            sql = str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            assert "auto_start" in sql
            return _FakeResult()

    @asynccontextmanager
    async def fake_read_scope():
        yield _FakeSession()

    monkeypatch.setattr("app.db.session.read_scope", fake_read_scope)

    assert await autopilot_sweep.list_eligible_autopilot_projects_step() == [
        "100",
        "200",
    ]


async def test_query_step_returns_empty_when_scan_fails(monkeypatch):
    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    class _FakeSession:
        async def execute(self, stmt):
            raise RuntimeError("scan blew up")

    @asynccontextmanager
    async def fake_read_scope():
        yield _FakeSession()

    monkeypatch.setattr("app.db.session.read_scope", fake_read_scope)

    assert await autopilot_sweep.list_eligible_autopilot_projects_step() == []


async def test_sweep_reenqueues_tick_for_every_eligible_project(monkeypatch):
    async def _fake_step():
        return ["100", "200"]

    monkeypatch.setattr(
        autopilot_sweep, "list_eligible_autopilot_projects_step", _fake_step
    )

    enqueued: List[str] = []

    async def _spy_enqueue(project_id):
        enqueued.append(project_id)

    monkeypatch.setattr("app.workflows.autopilot.enqueue_autopilot_tick", _spy_enqueue)

    result = await autopilot_sweep._autopilot_sweep_impl()

    assert result == {"projects": 2}
    assert sorted(enqueued) == ["100", "200"]


async def test_sweep_one_project_enqueue_failure_does_not_stop_the_rest(
    monkeypatch,
):
    async def _fake_step():
        return ["1", "2"]

    monkeypatch.setattr(
        autopilot_sweep, "list_eligible_autopilot_projects_step", _fake_step
    )

    enqueued: List[str] = []

    async def _flaky_enqueue(project_id):
        if project_id == "1":
            raise RuntimeError("boom")
        enqueued.append(project_id)

    monkeypatch.setattr(
        "app.workflows.autopilot.enqueue_autopilot_tick", _flaky_enqueue
    )

    result = await autopilot_sweep._autopilot_sweep_impl()

    assert result == {"projects": 2}  # counted before any per-project failure
    assert enqueued == ["2"]
