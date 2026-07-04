"""run_issue_agent delegates to the full chat runtime via run_session_turn."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_run_issue_agent_passes_chunk_callback_and_publishes(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    captured = {}

    async def fake_run_session_turn(
        session_id, *, user_id, content, trigger, chunk_callback=None, **kw
    ):
        captured["has_cb"] = chunk_callback is not None
        if chunk_callback:
            await chunk_callback("tok")
        return {
            "assistant_message": {
                "id": "m2",
                "role": "assistant",
                "content": "result",
                "agent_id": None,
                "metadata_json": {},
                "created_at": "2026-05-25T00:00:00+00:00",
            }
        }

    fake_chat = type(
        "C", (), {"run_session_turn": staticmethod(fake_run_session_turn)}
    )()
    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-99")
    )
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
    monkeypatch.setattr(m, "publish_status", AsyncMock())

    out = await m.run_issue_agent(
        issue={"id": 42, "title": "do thing", "description": "details"},
        agent_id="a",
        user_id="u",
    )
    assert out["content"] == "result"
    assert out["outcome"] is None  # no FinishIssue declared in this turn
    assert captured["has_cb"] is True
    assert chunks == ["tok"]
    assert messages and messages[0]["content"] == "result"


async def test_run_issue_agent_runs_session_turn(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-1")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "essay"}, "run_id": "r"}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    out = await m.run_issue_agent(
        issue={"id": 409, "title": "写一篇短文", "description": "春"},
        agent_id="a",
        user_id="u",
    )
    chat_svc.run_session_turn.assert_awaited_once()
    aa = chat_svc.run_session_turn.await_args
    assert aa.kwargs["trigger"] == "issue_dispatch"
    assert "写一篇短文" in str(aa)  # issue task is in the content arg
    assert out["content"] == "essay"


async def test_run_issue_agent_extracts_finish_outcome(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-2")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={
            "assistant_message": {"content": "did it"},
            "tool_calls": [
                {
                    "name": "FinishIssue",
                    "args": {"outcome": "completed"},
                    "result": {
                        "acknowledged": True,
                        "outcome": "completed",
                        "reason": "shipped",
                    },
                }
            ],
        }
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    out = await m.run_issue_agent(
        issue={"id": 7, "title": "t"}, agent_id="a", user_id="u"
    )
    assert out["outcome"] == "completed"
    assert out["reason"] == "shipped"


async def test_run_issue_agent_continuation_sends_nudge(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-3")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "more"}}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    await m.run_issue_agent(
        issue={"id": 8, "title": "big task", "description": "lots"},
        agent_id="a",
        user_id="u",
        is_continuation=True,
    )
    sent = chat_svc.run_session_turn.await_args.kwargs["content"]
    assert sent == m.CONTINUATION_NUDGE
    assert "big task" not in sent  # continuation does NOT resend the full task


async def test_run_issue_agent_raises_when_no_session(monkeypatch):
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(m, "get_or_create_issue_session", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError):
        await m.run_issue_agent(
            issue={"id": 1, "title": "t", "description": None},
            agent_id="a",
            user_id="u",
        )


async def test_run_issue_agent_publish_message_survives_realistic_bigint_row(
    monkeypatch,
):
    """Regression (P3 Task 6 review, Critical #2): run_issue_agent's
    publish_message → map_ai_message_to_issue_message → IssueMessage(id=...)
    chain used to crash with ``ValueError: badly formed hexadecimal UUID
    string`` because ConversationsAiStore.append_assistant_message returns a
    native BIGINT snowflake id, and the mapper unconditionally wrapped it in
    UUID(str(...)).

    Unlike this file's other tests, ``publish_message`` is NOT monkeypatched
    here — it's exactly the AsyncMock stubbing that let the crash slip past
    review the first time (see project lesson: mocked tests all missed a
    live-only bug). This test drives the real publish_message → real mapper
    → real IssueMessage validation against a REALISTIC assistant_message
    shape: a genuine BIGINT snowflake id (not a convenience string like
    "a-1"/"m2") and a real ``datetime`` object for created_at (not an ISO
    string) — the actual shape ConversationsAiStore hands back.
    """
    from app.services.issues import issue_agent_executor as m
    from app.services.issues import issue_chat_stream as stream_m

    # A realistic snowflake, not a convenient short string — the whole point
    # of the review's "type-homogeneous mocks masked cross-store bugs" note.
    BIGINT_MESSAGE_ID = 323848780659604
    BIGINT_SESSION_ID = 987654321012345

    async def fake_run_session_turn(
        session_id, *, user_id, content, trigger, chunk_callback=None, **kw
    ):
        return {
            "assistant_message": {
                "id": BIGINT_MESSAGE_ID,
                "session_id": BIGINT_SESSION_ID,
                "role": "assistant",
                "content": "done",
                "agent_id": None,
                "prompt_tokens": 12,
                "completion_tokens": 34,
                "metadata_json": {},
                # Real datetime object, not an ISO string — matching what
                # ConversationsAiStore._to_legacy_message_shape returns.
                "created_at": datetime.now(timezone.utc),
            }
        }

    fake_chat = type(
        "C", (), {"run_session_turn": staticmethod(fake_run_session_turn)}
    )()
    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-real")
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: fake_chat)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    # Deliberately do NOT monkeypatch m.publish_message — exercise the real
    # publish_message -> mapper -> IssueMessage chain.

    published: list[tuple[str, str]] = []

    fake_redis = AsyncMock()
    fake_redis.publish = AsyncMock(
        side_effect=lambda ch, payload: published.append((ch, payload))
    )
    monkeypatch.setattr(stream_m, "_get_redis", AsyncMock(return_value=fake_redis))

    out = await m.run_issue_agent(
        issue={"id": 999, "title": "ship it", "description": "for real"},
        agent_id="a",
        user_id="u",
    )

    assert out["content"] == "done"
    assert out["outcome"] is None

    # The real chain must have reached Redis without raising.
    assert published, "publish_message should have published to Redis"
    channel, payload = published[0]
    assert channel == "issue:999"

    body = json.loads(payload)
    assert body["type"] == "message"
    msg = body["message"]
    # id is a plain str of the bigint — never UUID-parsed.
    assert msg["id"] == str(BIGINT_MESSAGE_ID)
    assert msg["kind"] == "agent_run"
    assert msg["body"] == "done"
