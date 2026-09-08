"""Spec-1b: reply-turn steps + workflow lock logic."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.db import session as db_session


def _compile(stmt: Any) -> tuple[str, dict[str, Any]]:
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class _FakeResult:
    def __init__(self, rowcount: int = 0) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount: int = 0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rowcount = rowcount

    async def execute(self, stmt: Any) -> _FakeResult:
        self.calls.append(_compile(stmt))
        return _FakeResult(self._rowcount)


class _ScopeCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc: Any) -> bool:
        return False


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
    assert out == {
        "awaiting_input": False,
        "question": None,
        "options": None,
        "content": "hello",
        "outcome": None,
        "reason": None,
        "run_id": None,
    }
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

    assert out == {
        "content": "ok",
        "outcome": None,
        "reason": None,
        "run_id": "r1",
        "awaiting_input": False,
        "question": None,
        "options": None,
    }
    kwargs = fake_chat.run_session_turn.call_args.kwargs
    assert kwargs["content"] == "please continue"
    assert kwargs["trigger"] == "issue_reply"


@pytest.mark.asyncio
async def test_acquire_turn_lock_true_when_free(monkeypatch):
    from app.workflows import issue_lifecycle as m

    # execution_locked_at is service_role-only (issues_update_allowlist, mig 170)
    session = _FakeSession(rowcount=1)  # acquired
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

    got = await m.acquire_turn_lock.__wrapped__(99)
    assert got is True
    sql = "\n".join(sql for sql, _ in session.calls)
    assert "SET LOCAL ROLE service_role" in sql
    assert "execution_locked_at=now()" in sql
    assert "dbos_workflow_id" not in sql  # decision #2: do not touch wf id


@pytest.mark.asyncio
async def test_acquire_turn_lock_false_when_held(monkeypatch):
    from app.workflows import issue_lifecycle as m

    session = _FakeSession(rowcount=0)  # already locked
    monkeypatch.setattr(db_session, "write_scope", lambda: _ScopeCM(session))

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
