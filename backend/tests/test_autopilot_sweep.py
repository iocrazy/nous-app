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


async def test_sweep_skips_gracefully_when_db_not_configured(monkeypatch):
    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: False)

    result = await autopilot_sweep.sweep_autopilot_projects_step()

    assert result == {"projects": 0}


async def test_sweep_reenqueues_tick_for_every_eligible_project(monkeypatch):
    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    async def _fake_fetch_all(sql, params=None):
        assert "auto_start" in sql
        return [{"id": 100}, {"id": 200}]

    monkeypatch.setattr(db_engine, "fetch_all", _fake_fetch_all)

    enqueued: List[str] = []

    async def _spy_enqueue(project_id):
        enqueued.append(project_id)

    monkeypatch.setattr("app.workflows.autopilot.enqueue_autopilot_tick", _spy_enqueue)

    result = await autopilot_sweep.sweep_autopilot_projects_step()

    assert result == {"projects": 2}
    assert sorted(enqueued) == ["100", "200"]


async def test_sweep_one_project_enqueue_failure_does_not_stop_the_rest(
    monkeypatch,
):
    from app.db import engine as db_engine

    monkeypatch.setattr(db_engine, "is_configured", lambda: True)

    async def _fake_fetch_all(sql, params=None):
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(db_engine, "fetch_all", _fake_fetch_all)

    enqueued: List[str] = []

    async def _flaky_enqueue(project_id):
        if project_id == "1":
            raise RuntimeError("boom")
        enqueued.append(project_id)

    monkeypatch.setattr(
        "app.workflows.autopilot.enqueue_autopilot_tick", _flaky_enqueue
    )

    result = await autopilot_sweep.sweep_autopilot_projects_step()

    assert result == {"projects": 2}  # counted before any per-project failure
    assert enqueued == ["2"]
