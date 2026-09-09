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
    # This turn produces content with no FinishIssue declaration, which would
    # otherwise trigger the forced-declaration fallback — irrelevant to what
    # this test drives (chunk_callback plumbing + publish), so stub it out.
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )

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
    # "essay" has no FinishIssue declaration — stub the forced-declaration
    # fallback so this test stays focused on the trigger-mapping contract.
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
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


async def test_run_issue_agent_auto_true_maps_to_issue_dispatch_auto_trigger(
    monkeypatch,
):
    """M4 Autopilot quota hard line (task O2 review, round 2 carry-forward):
    the ONLY thing distinguishing an autopilot-driven dispatch from a manual
    one in ``agent_runs`` — and therefore the only thing
    ``count_auto_dispatches_today``'s ``WHERE trigger='issue_dispatch_auto'``
    can filter on — is this exact ``auto: bool`` -> ``trigger`` string
    mapping inside ``run_issue_agent`` itself. Every OTHER test that checks
    ``trigger == 'issue_dispatch_auto'`` (test_issue_agent_executor_p2.py's
    C1 regression test) passes that trigger value in DIRECTLY to
    ``run_session_turn``, bypassing this mapping entirely — so the mapping's
    True branch was previously unproven. Drives the real
    ``run_issue_agent(..., auto=True)`` call (same lightweight
    run_session_turn-mocked harness as the sibling test above — the
    "least faking" seam this module's existing tests already established)."""
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-2")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "auto essay"}, "run_id": "r"}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )

    out = await m.run_issue_agent(
        issue={"id": 410, "title": "autopilot task", "description": "x"},
        agent_id="a",
        user_id="u",
        auto=True,
    )

    chat_svc.run_session_turn.assert_awaited_once()
    aa = chat_svc.run_session_turn.await_args
    assert aa.kwargs["trigger"] == "issue_dispatch_auto"
    assert out["content"] == "auto essay"


async def test_run_issue_agent_auto_false_maps_to_manual_issue_dispatch_trigger(
    monkeypatch,
):
    """Sibling of the test above — explicit ``auto=False`` (not just the
    default) must still yield the MANUAL trigger value, never
    ``issue_dispatch_auto``. Together the two pin both halves of the
    mapping so a manual "Run now" can never accidentally count against the
    autopilot daily quota, and an autopilot dispatch can never accidentally
    be miscounted as manual."""
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-3")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={"assistant_message": {"content": "manual essay"}, "run_id": "r"}
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )

    out = await m.run_issue_agent(
        issue={"id": 411, "title": "manual task", "description": "x"},
        agent_id="a",
        user_id="u",
        auto=False,
    )

    chat_svc.run_session_turn.assert_awaited_once()
    aa = chat_svc.run_session_turn.await_args
    assert aa.kwargs["trigger"] == "issue_dispatch"
    assert out["content"] == "manual essay"


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
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
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
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
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


async def test_run_issue_agent_passes_stop_reason_through(monkeypatch):
    """Phase 2a Task 5: a hook stop (pause) reaches the workflow as
    ``stop_reason`` — with empty content the forced-declare fallback stays off."""
    from app.services.issues import issue_agent_executor as m

    monkeypatch.setattr(
        m, "get_or_create_issue_session", AsyncMock(return_value="sess-1")
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={
            "assistant_message": {"content": ""},
            "run_id": "31",
            "stop_reason": "paused",
        }
    )
    monkeypatch.setattr(m, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(m, "publish_chunk", AsyncMock())
    monkeypatch.setattr(m, "publish_message", AsyncMock())
    monkeypatch.setattr(m, "publish_status", AsyncMock())
    forced = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(m, "attempt_forced_finish_declaration", forced)
    out = await m.run_issue_agent(
        issue={"id": 409, "title": "t", "description": "d"}, agent_id="a", user_id="u"
    )
    assert out["stop_reason"] == "paused" and out["outcome"] is None
    assert out["run_id"] == "31"
    forced.assert_not_awaited()


async def test_run_issue_agent_passes_fork_of_from_execution_state(monkeypatch):
    """Phase 2b-1: execution_state.forked_from → run_session_turn(fork_of=…)."""
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
    monkeypatch.setattr(
        m, "attempt_forced_finish_declaration", AsyncMock(return_value=(None, None))
    )
    clear = AsyncMock()
    monkeypatch.setattr(m, "merge_execution_state", clear)
    await m.run_issue_agent(
        issue={
            "id": 409,
            "title": "Fork probe",
            "description": "x",
            "execution_state": {
                "forked_from": {
                    "run_id": 42,
                    "at_seq": 7,
                    "steer": True,
                    "steer_text": "make act 2 darker",
                }
            },
        },
        agent_id="a",
        user_id="u",
    )
    kw = chat_svc.run_session_turn.await_args.kwargs
    assert kw["fork_of"] == (42, 7) and kw["fork_steer"] is True
    # The seeded history already holds the task text: the steer IS the
    # forked turn's user message, never the full task again.
    assert kw["content"] == "make act 2 darker"
    # The stamp is consumed BEFORE the turn: a later resume / retry / reply
    # on this issue must not be recorded as a fork.
    clear.assert_awaited_once_with(409, {"forked_from": None})
    assert clear.await_args_list[0] and chat_svc.run_session_turn.await_count == 1

    chat_svc.run_session_turn.reset_mock()
    clear.reset_mock()
    await m.run_issue_agent(
        issue={"id": 410, "title": "Plain", "description": "x"},
        agent_id="a",
        user_id="u",
    )
    kw = chat_svc.run_session_turn.await_args.kwargs
    assert kw["fork_of"] is None and kw["fork_steer"] is False
    clear.assert_not_awaited()

    # load_issue may hand execution_state back as a raw JSON string
    chat_svc.run_session_turn.reset_mock()
    await m.run_issue_agent(
        issue={
            "id": 412,
            "title": "String state",
            "description": "x",
            "execution_state": '{"forked_from": {"run_id": 5, "at_seq": 2}}',
        },
        agent_id="a",
        user_id="u",
    )
    kw = chat_svc.run_session_turn.await_args.kwargs
    assert kw["fork_of"] == (5, 2) and kw["fork_steer"] is False
    # No steer → the continuation nudge (the history already has the task)
    assert kw["content"] == m.CONTINUATION_NUDGE
    clear.assert_awaited_once_with(412, {"forked_from": None})
    clear.reset_mock()

    # Malformed stamp → plain run, logged, never an exception
    chat_svc.run_session_turn.reset_mock()
    await m.run_issue_agent(
        issue={
            "id": 411,
            "title": "Bad stamp",
            "description": "x",
            "execution_state": {"forked_from": {"run_id": "abc"}},
        },
        agent_id="a",
        user_id="u",
    )
    kw = chat_svc.run_session_turn.await_args.kwargs
    assert kw["fork_of"] is None and kw["fork_steer"] is False
    clear.assert_not_awaited()
