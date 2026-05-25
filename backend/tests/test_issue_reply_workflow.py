"""Spec-1b: reply-turn steps + workflow lock logic."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_run_issue_reply_step_passes_chunk_callback_and_publishes(monkeypatch):
    from app.workflows import issue_lifecycle as m

    captured = {}

    async def fake_run_session_turn(
        session_id, *, user_id, content, trigger, chunk_callback=None, **kw
    ):
        captured["has_cb"] = chunk_callback is not None
        if chunk_callback:
            await chunk_callback("hel")
            await chunk_callback("lo")
        return {
            "assistant_message": {
                "id": "m1",
                "role": "assistant",
                "content": "hello",
                "agent_id": "a1",
                "metadata_json": {},
                "created_at": "2026-05-25T00:00:00+00:00",
            }
        }

    fake_chat = type(
        "C", (), {"run_session_turn": staticmethod(fake_run_session_turn)}
    )()
    monkeypatch.setattr(m, "AILibraryChatService", lambda: fake_chat)

    chunks, messages = [], []
    monkeypatch.setattr(
        m, "publish_chunk", AsyncMock(side_effect=lambda iid, d: chunks.append(d))
    )
    monkeypatch.setattr(
        m,
        "publish_message",
        AsyncMock(side_effect=lambda iid, row, **k: messages.append(row)),
    )

    out = await m.run_issue_reply_step.__wrapped__(
        issue_id=7,
        session_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        reply_text="hi",
    )
    assert out == "hello"
    assert captured["has_cb"] is True
    assert chunks == ["hel", "lo"]
    assert messages and messages[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_run_issue_reply_step_calls_run_session_turn(monkeypatch):
    from app.workflows import issue_lifecycle as m

    fake_chat = AsyncMock()
    fake_chat.run_session_turn = AsyncMock(
        return_value={
            "assistant_message": {
                "id": "m1",
                "role": "assistant",
                "content": "ok",
                "agent_id": None,
                "metadata_json": {},
                "created_at": "2026-05-25T00:00:00+00:00",
            },
            "run_id": "r1",
        }
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: fake_chat)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())

    out = await m.run_issue_reply_step.__wrapped__(
        issue_id=1,
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


@pytest.mark.asyncio
async def test_run_reply_turns_acquires_runs_releases():
    from app.workflows import issue_lifecycle as m

    calls = {"run": 0, "release": 0}
    acquire = AsyncMock(return_value=True)

    async def run_turn(**kw):
        calls["run"] += 1
        return "done"

    async def release(_id):
        calls["release"] += 1

    async def sleep(_s):
        raise AssertionError("should not sleep when lock is free")

    out = await m._run_reply_turns(
        7,
        "u",
        "hi",
        session_id="s",
        acquire=acquire,
        run_turn=run_turn,
        release=release,
        sleep=sleep,
    )

    assert out["executed"] is True
    assert calls == {"run": 1, "release": 1}
    acquire.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_run_reply_turns_waits_then_defers():
    from app.workflows import issue_lifecycle as m

    acquire = AsyncMock(return_value=False)  # never free
    slept = {"n": 0}

    async def run_turn(**kw):
        raise AssertionError("must not run when lock never acquired")

    async def release(_id):
        raise AssertionError("must not release a lock we never held")

    async def sleep(_s):
        slept["n"] += 1

    out = await m._run_reply_turns(
        7,
        "u",
        "hi",
        session_id="s",
        acquire=acquire,
        run_turn=run_turn,
        release=release,
        sleep=sleep,
        max_attempts=3,
        wait_seconds=1,
    )

    assert out["deferred"] is True
    assert slept["n"] == 3  # waited between each failed attempt
    assert acquire.await_count == 3


@pytest.mark.asyncio
async def test_run_reply_turns_releases_even_on_error():
    from app.workflows import issue_lifecycle as m

    acquire = AsyncMock(return_value=True)
    released = {"n": 0}

    async def run_turn(**kw):
        raise RuntimeError("llm blew up")

    async def release(_id):
        released["n"] += 1

    async def sleep(_s):
        pass

    with pytest.raises(RuntimeError, match="llm blew up"):
        await m._run_reply_turns(
            7,
            "u",
            "hi",
            session_id="s",
            acquire=acquire,
            run_turn=run_turn,
            release=release,
            sleep=sleep,
        )
    assert released["n"] == 1  # finally released
