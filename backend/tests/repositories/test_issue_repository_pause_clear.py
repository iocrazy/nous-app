"""Phase 2a Task 5: landing on a terminal status ends a target-level pause
(spec §2 "cancel while paused clears paused_at") — otherwise the rollup, where
``paused_at`` wins, keeps showing a cancelled issue as paused."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories import issue_repository as m

pytestmark = pytest.mark.unit
PAUSED = "2026-09-08T00:00:00+00:00"


@pytest.fixture
def repo(monkeypatch):
    r = m.IssueRepository()
    r.update = AsyncMock(side_effect=lambda iid, patch: {"id": iid, **patch})
    for hook in (
        "_fire_subissue_barrier",
        "_fire_pipeline_relay",
        "_fire_stage_node_sync",
    ):
        monkeypatch.setattr(m, hook, AsyncMock())
    return r


@pytest.mark.parametrize("terminal", ["cancelled", "done", "closed"])
async def test_terminal_transition_clears_paused_at(repo, terminal):
    repo.get_by_id = AsyncMock(
        return_value={"id": 7, "status": "in_progress", "paused_at": PAUSED}
    )
    await repo.transition_status(7, terminal)
    patch = repo.update.await_args.args[1]
    assert patch["status"] == terminal and patch["paused_at"] is None


async def test_non_terminal_transition_keeps_the_pause(repo):
    repo.get_by_id = AsyncMock(
        return_value={"id": 7, "status": "backlog", "paused_at": PAUSED}
    )
    await repo.transition_status(7, "in_progress")
    assert "paused_at" not in repo.update.await_args.args[1]


async def test_unpaused_issue_gets_no_paused_at_key(repo):
    repo.get_by_id = AsyncMock(
        return_value={"id": 7, "status": "in_progress", "paused_at": None}
    )
    await repo.transition_status(7, "done")
    assert "paused_at" not in repo.update.await_args.args[1]
