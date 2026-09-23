"""Hotfix-2 PR-3 follow-up: the reply turn (``run_issue_reply_step``, driven by
``respond_to_issue_reply`` and the needs_input wait loop) re-reads
``issues.status`` after the per-user slot wait too. A cancel that lands while
the reply is queued starts no turn and writes no status."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.ai.chat import agent_concurrency
from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

pytestmark = pytest.mark.unit
USER = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def one_slot():
    prev = agent_concurrency.current_limit()
    agent_concurrency.reset_for_tests()
    agent_concurrency.set_agent_concurrency(1)
    yield
    agent_concurrency.set_agent_concurrency(prev)
    agent_concurrency.reset_for_tests()


@pytest.mark.parametrize("status", ["cancelled", "done", "closed"])
async def test_reply_cancelled_during_the_slot_wait_starts_no_turn(
    monkeypatch, one_slot, status
):
    from app.services.issues import issue_agent_executor as ex
    from app.workflows import issue_lifecycle as il

    box = {"status": "in_progress"}
    reads: list = []

    async def _read(issue_id: int, **_kw):
        reads.append(box["status"])
        return box["status"]

    monkeypatch.setattr(ex, "read_issue_status", _read)
    inner = AsyncMock(return_value={"assistant_message": {"content": "ran"}})
    monkeypatch.setattr(AILibraryChatService, "_run_session_turn_inner", inner)
    monkeypatch.setattr(il, "publish_chunk", AsyncMock())
    monkeypatch.setattr(il, "publish_message", AsyncMock())

    release = asyncio.Event()

    async def _other_turn():
        async with agent_concurrency.user_slot(USER):
            await release.wait()

    holder = asyncio.create_task(_other_turn())
    await asyncio.sleep(0)
    run = asyncio.create_task(
        il.run_issue_reply_step(
            issue_id=42, session_id="55", user_id=USER, reply_text="ping"
        )
    )
    await asyncio.sleep(0.01)
    assert not run.done(), "the reply turn must be queued behind the slot"
    box["status"] = status
    release.set()
    out = await asyncio.wait_for(run, 2)
    await holder

    inner.assert_not_awaited()
    il.publish_message.assert_not_awaited()
    assert reads == [status]
    assert out["stop_reason"] == "cancelled"
    assert out["preempted_status"] == status
    assert out["run_id"] is None
    assert out["outcome"] is None
    assert out["pending_dispatches"] == []


def _prerun(status: str = "cancelled") -> dict:
    return {
        "content": "",
        "stop_reason": "cancelled",
        "outcome": None,
        "reason": f"issue {status} before the turn started",
        "preempted_status": status,
        "run_id": None,
        "pending_dispatches": [],
    }


@pytest.mark.parametrize("resuming", [True, False])
async def test_reply_turn_that_never_started_is_preempted_not_routed(resuming):
    from app.workflows import issue_lifecycle as il

    set_status = AsyncMock(return_value=True)
    issue = (
        {
            "id": 1,
            "status": "needs_followup",
            "execution_state": '{"agent_outcome": "needs_input"}',
        }
        if resuming
        else {"id": 1, "status": "in_progress"}
    )
    release = AsyncMock()
    out = await il._run_reply_turns(
        1,
        USER,
        "ping",
        session_id="55",
        acquire=AsyncMock(return_value=True),
        run_turn=AsyncMock(return_value=_prerun("cancelled")),
        release=release,
        sleep=AsyncMock(),
        load_issue=AsyncMock(return_value=issue),
        set_status=set_status,
    )
    assert out == {"issue_id": 1, "preempted": True, "preempted_status": "cancelled"}
    written = [c.args[1] for c in set_status.await_args_list]
    # Only the resume write (made before the cancel) — no routing afterwards.
    assert written == (["in_progress"] if resuming else [])
    release.assert_awaited_once_with(1)
