"""Phase 2a Task 5: a hook STOP reason (paused / cancelled / awaiting_input)
reaches ``run_session_turn``'s result verbatim on BOTH paths — the buffered
``run_turn`` dict and the stream's terminal chunk (``usage.stop_reason``).
The issue workflow routes on it before any FinishIssue outcome."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.services.ai.chat import ai_library_chat_service as svc_mod
from tests.test_parity_gap_coverage import _chat_env, _FakeStore, _session_row

pytestmark = pytest.mark.unit


async def test_buffered_turn_returns_the_stop_reason():
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    with _chat_env(
        run_turn_result={
            "content": "",
            "stop_reason": "paused",
            "cancelled": False,
            "tool_calls": [],
        },
        agent_id=agent_id,
    ):
        out = await svc_mod.AILibraryChatService(store=store).run_session_turn(
            uuid4(), user_id=user_id, content="go", trigger="issue_dispatch"
        )
    assert out["stop_reason"] == "paused"
    assert out["cancelled"] is False and out["awaiting_input"] is False


async def test_normal_end_has_no_stop_reason():
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    with _chat_env(
        run_turn_result={"content": "done", "tool_calls": []}, agent_id=agent_id
    ):
        out = await svc_mod.AILibraryChatService(store=store).run_session_turn(
            uuid4(), user_id=user_id, content="go", trigger="issue_dispatch"
        )
    assert out["stop_reason"] is None


async def test_stream_terminal_chunk_stop_reason_is_returned():
    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    with _chat_env(run_turn_result={"content": ""}, agent_id=agent_id):
        runner = svc_mod.build_agent_runner_stack.return_value.runner

        async def stream_turn(*a, **k):
            yield StreamChunk(delta_text="half ")
            yield StreamChunk(
                delta_text="",
                finish_reason="stop",
                usage={"stop_reason": "paused"},
                tool_call_trace=[],
            )

        runner.stream_turn = stream_turn
        out = await svc_mod.AILibraryChatService(store=store).run_session_turn(
            uuid4(),
            user_id=user_id,
            content="go",
            trigger="issue_dispatch",
            chunk_callback=AsyncMock(),
        )
    assert out["stop_reason"] == "paused"
    assert out["awaiting_input"] is False and out["cancelled"] is False
    assert out["assistant_message"]["content"] == "half "


async def test_stream_terminal_chunk_hook_decision_becomes_awaiting_approval():
    """The terminal chunk's ``usage.hook_decision`` (both stream routes file
    it) must rebuild ``awaiting_approval`` / ``approval_reason`` on the
    result — that is what persists the approval row and the message
    metadata; before this the chunk_callback route never wrote either."""
    from unittest.mock import MagicMock

    user_id, agent_id = uuid4(), uuid4()
    store = _FakeStore(_session_row(user_id, agent_id))
    repo = MagicMock()
    repo.create = AsyncMock(return_value={"id": "ap-1"})
    with _chat_env(
        run_turn_result={"content": ""}, agent_id=agent_id, approval_repo=repo
    ):
        runner = svc_mod.build_agent_runner_stack.return_value.runner

        async def stream_turn(*a, **k):
            yield StreamChunk(
                delta_text="\n\n[awaiting approval: publish live]",
                finish_reason="stop",
                usage={
                    "hook_decision": "await_approval",
                    "approval_reason": "publish live",
                },
                tool_call_trace=[],
            )

        runner.stream_turn = stream_turn
        out = await svc_mod.AILibraryChatService(store=store).run_session_turn(
            uuid4(),
            user_id=user_id,
            content="go",
            trigger="issue_dispatch",
            chunk_callback=AsyncMock(),
        )
    # The row is persisted and the message carries the card's seat.
    repo.create.assert_awaited_once()
    assert repo.create.await_args.kwargs["reason"] == "publish live"
    assert (
        out["assistant_message"]["metadata_json"]["awaiting_approval"]["reason"]
        == "publish live"
    )
    assert out["stop_reason"] is None
