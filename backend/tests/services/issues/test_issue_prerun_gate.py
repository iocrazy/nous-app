"""Hotfix-2 PR-3: an issue run re-reads ``issues.status`` AFTER the per-user
turn slot is acquired and BEFORE anything is persisted or recorded.

Production R4 (2026-09-23): the workflow's loop-top PREEMPT check ran at
15:07:26, the cancel landed at 15:07:28, and the turn sat ~54 s in
``agent_concurrency.user_slot`` (six probes, two slots) before it created
its agent_runs row and spent a point plus a workforce subtree on a cancelled
issue. The gate must sit after that wait, so these tests drive the REAL
``run_session_turn`` with the slot held by someone else.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.ai.chat import agent_concurrency
from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

pytestmark = pytest.mark.unit


@pytest.fixture
def one_slot():
    prev = agent_concurrency.current_limit()
    agent_concurrency.reset_for_tests()
    agent_concurrency.set_agent_concurrency(1)
    yield
    agent_concurrency.set_agent_concurrency(prev)
    agent_concurrency.reset_for_tests()


def _patch_executor(monkeypatch, status_box: dict):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-1")
    )
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "_resolve_stage_brief", AsyncMock(return_value=None))
    reads: list[str | None] = []

    async def _read(issue_id: int, **_kw):
        reads.append(status_box["status"])
        return status_box["status"]

    monkeypatch.setattr(m, "read_issue_status", _read)
    inner = AsyncMock(
        return_value={"assistant_message": {"content": "ran"}, "run_id": "r1"}
    )
    monkeypatch.setattr(AILibraryChatService, "_run_session_turn_inner", inner)
    return m, inner, reads


@pytest.mark.parametrize("status", ["cancelled", "done", "closed"])
async def test_cancel_during_the_slot_wait_starts_no_turn(
    monkeypatch, one_slot, status
):
    box = {"status": "in_progress"}
    m, inner, reads = _patch_executor(monkeypatch, box)

    release = asyncio.Event()

    async def _other_turn():
        async with agent_concurrency.user_slot("u"):
            await release.wait()

    holder = asyncio.create_task(_other_turn())
    await asyncio.sleep(0)  # the other turn now holds the only slot

    run = asyncio.create_task(
        m.run_issue_agent(
            issue={"id": 42, "title": "t", "description": "d"},
            agent_id="a",
            user_id="u",
        )
    )
    await asyncio.sleep(0.01)
    assert not run.done(), "the issue turn must be queued behind the slot"
    box["status"] = status  # the cancel lands while the turn is queued
    release.set()
    out = await asyncio.wait_for(run, 2)
    await holder

    inner.assert_not_awaited()
    assert reads == [status], "one read, after the wait — not before it"
    assert out["stop_reason"] == "cancelled"
    assert out["outcome"] is None
    assert out["content"] == ""
    assert out["preempted_status"] == status
    assert out["reason"] == f"issue {status} before the turn started"
    assert out.get("run_id") is None
    # The UI's spinner still gets its ``done`` frame (run_id null).
    assert m.publish_status.await_args_list[-1].args == (42, "done")
    assert m.publish_status.await_args_list[-1].kwargs == {"run_id": None}


async def test_live_issue_after_the_wait_runs_the_turn(monkeypatch, one_slot):
    box = {"status": "in_progress"}
    m, inner, reads = _patch_executor(monkeypatch, box)
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
    out = await m.run_issue_agent(
        issue={"id": 42, "title": "t", "description": "d"}, agent_id="a", user_id="u"
    )
    inner.assert_awaited_once()
    assert reads == ["in_progress"]
    assert out["content"] == "ran"
    assert out.get("preempted_status") is None


async def test_unreadable_status_does_not_block_the_turn(monkeypatch, one_slot):
    """Best-effort: a DB hiccup reads as "not preempted" — the step hook and
    the workflow's final re-read remain behind it."""
    box = {"status": None}
    m, inner, _ = _patch_executor(monkeypatch, box)
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
    out = await m.run_issue_agent(
        issue={"id": 42, "title": "t", "description": "d"}, agent_id="a", user_id="u"
    )
    inner.assert_awaited_once()
    assert out["content"] == "ran"


async def test_run_session_turn_without_a_gate_is_unchanged(monkeypatch, one_slot):
    inner = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(AILibraryChatService, "_run_session_turn_inner", inner)
    out = await AILibraryChatService(store=object()).run_session_turn(
        "s", user_id="u", content="hi"
    )
    assert out == {"ok": True}
    inner.assert_awaited_once()
