"""Spec-1b: reply-turn steps + workflow lock logic."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_run_issue_reply_step_calls_run_session_turn(monkeypatch):
    from app.workflows import issue_lifecycle as m

    fake_chat = AsyncMock()
    fake_chat.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "ok"}, "run_id": "r1"}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: fake_chat)

    out = await m.run_issue_reply_step.__wrapped__(
        session_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        reply_text="please continue",
    )

    assert out == "ok"
    kwargs = fake_chat.run_session_turn.call_args.kwargs
    assert kwargs["content"] == "please continue"
    assert kwargs["trigger"] == "issue_reply"


@pytest.mark.asyncio
async def test_acquire_turn_lock_true_when_free(monkeypatch):
    from app.workflows import issue_lifecycle as m

    fake_engine = AsyncMock()
    fake_engine.execute = AsyncMock(return_value=1)  # 1 row updated → acquired
    monkeypatch.setattr(m, "_engine", lambda: fake_engine, raising=False)

    got = await m.acquire_turn_lock.__wrapped__(99)
    assert got is True
    sql = fake_engine.execute.call_args.args[0]
    assert "execution_locked_at = now()" in sql
    assert "dbos_workflow_id" not in sql  # decision #2: do not touch wf id


@pytest.mark.asyncio
async def test_acquire_turn_lock_false_when_held(monkeypatch):
    from app.workflows import issue_lifecycle as m

    fake_engine = AsyncMock()
    fake_engine.execute = AsyncMock(return_value=0)  # 0 rows → already locked
    monkeypatch.setattr(m, "_engine", lambda: fake_engine, raising=False)

    assert await m.acquire_turn_lock.__wrapped__(99) is False
