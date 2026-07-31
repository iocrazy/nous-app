"""autopilot_sweep — the 5-minute backstop sweep (task O2, review adjacent
minor fix: the eligible-project SQL's boolean check).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.workflows import autopilot_sweep

pytestmark = pytest.mark.asyncio


def test_eligible_projects_sql_uses_string_equality_not_boolean_cast():
    """Review adjacent-minor fix: a ``::boolean`` cast RAISES on any row
    whose ``events->>'auto_start'`` isn't a Postgres-recognized boolean
    literal, and that raise aborts the ENTIRE query — one dirty row would
    take down the global sweep for every OTHER project too. Plain string
    equality never raises; Pydantic's ``WorkflowNodeEvents.auto_start: bool``
    always serializes as the JSON literal ``true``/``false`` so no real match
    is lost."""
    sql = autopilot_sweep._ELIGIBLE_PROJECTS_SQL
    assert "::boolean" not in sql
    assert "events->>'auto_start' = 'true'" in sql


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
    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    async def _fake_fetch_all(sql, params=None):
        assert "auto_start" in sql
        return [{"id": 100}, {"id": 200}]

    monkeypatch.setattr(db_engine, "fetch_all", _fake_fetch_all)

    assert await autopilot_sweep.list_eligible_autopilot_projects_step() == [
        "100",
        "200",
    ]


async def test_query_step_returns_empty_when_scan_fails(monkeypatch):
    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    async def _boom(sql, params=None):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(db_engine, "fetch_all", _boom)

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
