"""The sweeper tick expires orphaned inbox items (spec §1-③) through the repo."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_expire_step_marks_items_older_than_a_day(monkeypatch):
    import app.repositories.agent_run_inbox_repository as m
    from app.workflows import agent_runs_sweeper as sw

    repo = SimpleNamespace(expire_stale=AsyncMock(return_value=3))
    monkeypatch.setattr(m, "get_agent_run_inbox_repository", lambda: repo)
    assert await sw.expire_orphan_inbox_step() == 3
    older_than = repo.expire_stale.await_args.kwargs["older_than"]
    age = dt.datetime.now(dt.timezone.utc) - older_than
    assert dt.timedelta(hours=23, minutes=59) < age < dt.timedelta(hours=24, minutes=1)


def test_tick_calls_the_expiry_step():
    import inspect

    from app.workflows import agent_runs_sweeper as sw

    assert "expire_orphan_inbox_step()" in inspect.getsource(
        sw.agent_runs_sweeper_workflow
    )
