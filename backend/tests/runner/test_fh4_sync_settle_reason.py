"""fh4 T2 (E2a): the synchronous Task path's ``subagent_done`` says why the
child settled, like the background path's."""

import pytest

from app.services.ai.runner.subagent_task_service import SubAgentTaskService
from tests.test_subagent_task_service import _EventRecorder, _wire_sync_spawn

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def caller_ctx():
    from uuid import uuid4

    return {
        "caller_agent_id": uuid4(),
        "caller_user_id": uuid4(),
        "parent_run_id": uuid4(),
        "agent_depth": 0,
    }


async def test_sync_success_settles_as_producer(monkeypatch, caller_ctx):
    _wire_sync_spawn(monkeypatch)
    rec = _EventRecorder()
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)

    await service.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert rec.events[-1][1]["settle_reason"] == "producer"


async def test_sync_crash_settles_as_producer(monkeypatch, caller_ctx):
    wired = _wire_sync_spawn(monkeypatch)
    wired.run_turn.side_effect = RuntimeError("provider exploded")
    rec = _EventRecorder()
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)

    await service.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert rec.events[-1][1]["settle_reason"] == "producer"


async def test_sync_cancelled_child_settles_as_kill(monkeypatch, caller_ctx):
    wired = _wire_sync_spawn(monkeypatch)
    wired.run_turn.return_value = {"content": "", "stop_reason": "cancelled"}
    rec = _EventRecorder()
    service = SubAgentTaskService(**caller_ctx, parent_recorder=rec)

    out = await service.spawn({"subagent_type": "librarian", "prompt": "dig"})

    assert out["status"] == "cancelled"
    assert rec.events[-1][1]["settle_reason"] == "kill"
