"""Defect B on the cancel edge: cancelling an issue also disarms the wake-ups
the agent armed on it (reason ``issue_terminal``), as a third best-effort step
of ``stop_live_work_for_cancel``. A failure in any step never skips the next."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories import user_schedules_repository as repo
from app.services.issues import cancel_live_work as clw

pytestmark = pytest.mark.unit

ROW = {"id": 7, "status": "cancelled", "ai_session_id": None}


@pytest.fixture
def quiet_steps(monkeypatch):
    runs = AsyncMock()
    runs.running_root_run_id = AsyncMock(return_value=None)
    monkeypatch.setattr(clw, "_runs_repo", lambda: runs)
    return runs


@pytest.mark.asyncio
async def test_cancel_disarms_agent_wakeups(monkeypatch, quiet_steps):
    disarm = AsyncMock(return_value=2)
    monkeypatch.setattr(repo, "disarm_agent_wakeups", disarm)
    await clw.stop_live_work_for_cancel(7, issue=ROW)
    disarm.assert_awaited_once_with(7, reason="issue_terminal")


@pytest.mark.asyncio
async def test_disarm_still_runs_when_an_earlier_step_fails(monkeypatch, quiet_steps):
    quiet_steps.running_root_run_id = AsyncMock(side_effect=RuntimeError("boom"))
    disarm = AsyncMock(return_value=0)
    monkeypatch.setattr(repo, "disarm_agent_wakeups", disarm)
    await clw.stop_live_work_for_cancel(7, issue=ROW)
    disarm.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_disarm_is_logged_not_raised(monkeypatch, quiet_steps):
    monkeypatch.setattr(
        repo, "disarm_agent_wakeups", AsyncMock(side_effect=RuntimeError("db down"))
    )
    errors: list[str] = []
    monkeypatch.setattr(clw.logger, "error", lambda msg, *a, **k: errors.append(msg))
    await clw.stop_live_work_for_cancel(7, issue=ROW)
    assert any("disarm agent wake-ups" in e for e in errors)
