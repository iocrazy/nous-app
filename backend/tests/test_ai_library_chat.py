"""Unit tests for AILibraryChatService.

Covers session CRUD authorization (404 on foreign session) and the chat
orchestration flow (user-message-first persistence, runner wiring,
assistant message + counters). Runner + PromptComposer + RunRecorder
are patched — we're testing the service's own coordination logic, not
the third-party-ish wiring those classes own.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException


def _fake_maybe_single(row: dict | None):
    """Build a chain-terminator that returns a result-like object with .data."""
    result = MagicMock()
    result.data = row
    exec_mock = AsyncMock(return_value=result)
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.maybe_single.return_value = chain
    chain.single.return_value = chain
    chain.execute = exec_mock
    return chain


@pytest.mark.asyncio
async def test_get_session_returns_owned_row() -> None:
    """Owner fetches session → row returned unchanged."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    session_id = uuid4()
    row = {"id": str(session_id), "user_id": str(user_id), "agent_slug": "foo"}

    client = MagicMock()
    client.table.return_value = _fake_maybe_single(row)

    with patch(
        "app.services.ai.chat.ai_library_chat_service.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        svc = AILibraryChatService()
        got = await svc.get_session(session_id, user_id=user_id)
    assert got["user_id"] == str(user_id)


@pytest.mark.asyncio
async def test_get_session_404_when_not_owner() -> None:
    """Foreign user → 404 (NOT 403, to avoid session-existence leakage)."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    other = uuid4()
    session_id = uuid4()
    row = {"id": str(session_id), "user_id": str(other), "agent_slug": "foo"}

    client = MagicMock()
    client.table.return_value = _fake_maybe_single(row)

    with patch(
        "app.services.ai.chat.ai_library_chat_service.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        svc = AILibraryChatService()
        with pytest.raises(HTTPException) as exc:
            await svc.get_session(session_id, user_id=user_id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_session_404_when_missing() -> None:
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    client = MagicMock()
    client.table.return_value = _fake_maybe_single(None)

    with patch(
        "app.services.ai.chat.ai_library_chat_service.get_async_supabase_admin",
        AsyncMock(return_value=client),
    ):
        svc = AILibraryChatService()
        with pytest.raises(HTTPException) as exc:
            await svc.get_session(uuid4(), user_id=uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_chat_persists_both_messages_and_bumps_counters() -> None:
    """Happy path: chat() inserts user msg, runs agent, inserts assistant
    msg, and updates session total_tokens + message_count."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    session_id = uuid4()
    agent_id = uuid4()
    session_row = {
        "id": str(session_id),
        "user_id": str(user_id),
        "agent_slug": "script_ai",
        "agent_id": str(agent_id),
        "total_tokens": 100,
        "message_count": 2,
        "team_id": None,
        "project_id": None,
    }

    # Track all table ops so we can assert sequence + payloads.
    inserted: list[tuple[str, dict]] = []
    updated: list[tuple[str, dict]] = []

    def _make_table(name: str):
        MagicMock()
        if name == "ai_sessions":
            # .select().eq().maybe_single().execute() → session_row
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.maybe_single.return_value = q
            q.execute = AsyncMock(return_value=MagicMock(data=session_row))
            # .update().eq().execute() — record
            upd_chain = MagicMock()

            def _upd(payload):
                updated.append((name, payload))
                return upd_chain

            q.update = _upd
            upd_chain.eq.return_value = upd_chain
            upd_chain.execute = AsyncMock(return_value=MagicMock(data=[]))
            return q
        if name == "ai_messages":
            q = MagicMock()

            # .select().eq().order().limit().execute() returns [] (no history)
            q.select.return_value = q
            q.eq.return_value = q
            q.order.return_value = q
            q.limit.return_value = q
            q.execute = AsyncMock(return_value=MagicMock(data=[]))

            # .insert(payload).execute() records + returns a fake id
            def _ins(payload):
                inserted.append((name, payload))
                ins_chain = MagicMock()
                ins_chain.execute = AsyncMock(
                    return_value=MagicMock(data=[{**payload, "id": str(uuid4())}])
                )
                return ins_chain

            q.insert = _ins
            return q
        return MagicMock()

    client = MagicMock()
    client.table.side_effect = _make_table

    # Stub ComposedSystemPrompt
    composed = MagicMock()
    composed.agent_id = agent_id
    composed.agent_slug = "script_ai"
    composed.model = "qwen-max"

    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": "Hi user", "raw": {}})

    # Fake RunRecorder context-manager that reports tokens.
    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 42
    recorder.completion_tokens = 7
    recorder.set_summaries = MagicMock()

    class _CM:
        async def __aenter__(self_inner):
            return recorder

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    # M1.5 wiring: chat() now calls AgentRepository.get_by_slug then
    # build_agent_runner_stack. Patch both so the test stays focused on
    # the chat-service coordination logic (M1.5 wiring is unit-tested
    # separately under test_ai_library_chat_wiring.py).
    fake_agent_record = {
        "id": str(agent_id),
        "slug": "script_ai",
        "model": "qwen-max",
        "budget_per_run_cents": None,
        "fallback_models": [],
    }

    fake_stack = MagicMock()
    fake_stack.runner = runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.primary_model = "qwen-max"
    fake_stack.fallback_chain_active = False

    fake_agent_repo_instance = MagicMock()
    fake_agent_repo_instance.get_by_slug = AsyncMock(return_value=fake_agent_record)

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_async_supabase_admin",
            AsyncMock(return_value=client),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
            return_value=fake_agent_repo_instance,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_adapter",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            return_value=_CM(),
        ),
    ):
        svc = AILibraryChatService()
        out = await svc.chat(session_id, user_id=user_id, content="Hello")

    # User message was inserted first (before the runner call) — find it.
    user_inserts = [p for t, p in inserted if p.get("role") == "user"]
    asst_inserts = [p for t, p in inserted if p.get("role") == "assistant"]
    assert len(user_inserts) == 1
    assert user_inserts[0]["content"] == "Hello"
    assert len(asst_inserts) == 1
    assert asst_inserts[0]["content"] == "Hi user"
    assert asst_inserts[0]["prompt_tokens"] == 42
    assert asst_inserts[0]["completion_tokens"] == 7

    # Session counters bumped: prior total (100) + 42+7 = 149; prior count (2) + 2 = 4.
    assert len(updated) == 1
    assert updated[0][1]["total_tokens"] == 149
    assert updated[0][1]["message_count"] == 4

    # Response shape — what the router will return.
    assert out["usage"] == {"prompt_tokens": 42, "completion_tokens": 7}
    assert out["run_id"] == str(recorder.run_id)
    assert out["assistant_message"]["content"] == "Hi user"
    # No tool calls fired this turn → trace is empty + metadata_json
    # carries only run_id (no tool_calls noise).
    assert out["tool_calls"] == []
    assert asst_inserts[0]["metadata_json"] == {"run_id": str(recorder.run_id)}


@pytest.mark.asyncio
async def test_chat_persists_tool_calls_into_metadata_json() -> None:
    """When the runner reports tool dispatches this turn, the assistant
    message row's metadata_json carries them so a fresh session reload
    still renders the sub-task cards (no separate tool_calls table)."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    session_id = uuid4()
    agent_id = uuid4()
    session_row = {
        "id": str(session_id),
        "user_id": str(user_id),
        "agent_slug": "coordinator",
        "agent_id": str(agent_id),
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
    }

    inserted: list[tuple[str, dict]] = []

    def _make_table(name: str):
        MagicMock()
        if name == "ai_sessions":
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.maybe_single.return_value = q
            q.execute = AsyncMock(return_value=MagicMock(data=session_row))
            upd_chain = MagicMock()
            q.update = lambda payload: upd_chain
            upd_chain.eq.return_value = upd_chain
            upd_chain.execute = AsyncMock(return_value=MagicMock(data=[]))
            return q
        if name == "ai_messages":
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.order.return_value = q
            q.limit.return_value = q
            q.execute = AsyncMock(return_value=MagicMock(data=[]))

            def _ins(payload):
                inserted.append((name, payload))
                ins_chain = MagicMock()
                ins_chain.execute = AsyncMock(
                    return_value=MagicMock(data=[{**payload, "id": str(uuid4())}])
                )
                return ins_chain

            q.insert = _ins
            return q
        return MagicMock()

    client = MagicMock()
    client.table.side_effect = _make_table

    composed = MagicMock()
    composed.agent_id = agent_id
    composed.agent_slug = "coordinator"
    composed.model = "qwen-max"
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    trace = [
        {
            "name": "Delegate",
            "iteration": 1,
            "args": {"agent_slug": "summary", "prompt": "do X"},
            "result": {"status": "queued", "inbox_message_id": "abc"},
        }
    ]

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={"content": "Routed it.", "raw": {}, "tool_calls": trace}
    )

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 5
    recorder.completion_tokens = 3
    recorder.set_summaries = MagicMock()

    class _CM:
        async def __aenter__(self_inner):
            return recorder

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    fake_agent_record = {
        "id": str(agent_id),
        "slug": "coordinator",
        "model": "qwen-max",
        "budget_per_run_cents": None,
        "fallback_models": [],
    }
    fake_stack = MagicMock()
    fake_stack.runner = runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.primary_model = "qwen-max"
    fake_stack.fallback_chain_active = False
    fake_agent_repo_instance = MagicMock()
    fake_agent_repo_instance.get_by_slug = AsyncMock(return_value=fake_agent_record)

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_async_supabase_admin",
            AsyncMock(return_value=client),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
            return_value=fake_agent_repo_instance,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_adapter",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            return_value=_CM(),
        ),
    ):
        svc = AILibraryChatService()
        out = await svc.chat(session_id, user_id=user_id, content="route it")

    asst_inserts = [p for t, p in inserted if p.get("role") == "assistant"]
    assert len(asst_inserts) == 1
    meta = asst_inserts[0]["metadata_json"]
    assert meta["run_id"] == str(recorder.run_id)
    assert meta["tool_calls"] == trace
    # Live response also carries the trace so streaming clients don't
    # need to refetch just to render cards.
    assert out["tool_calls"] == trace


@pytest.mark.asyncio
async def test_chat_streams_chunks_via_callback() -> None:
    """P2: when chunk_callback is provided, chat() drives stream_turn
    and forwards each delta_text to the callback. Final assistant
    content equals the concatenation of streamed chunks."""
    from app.services.ai.adapters.base import StreamChunk
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    session_id = uuid4()
    agent_id = uuid4()
    session_row = {
        "id": str(session_id),
        "user_id": str(user_id),
        "agent_slug": "script_ai",
        "agent_id": str(agent_id),
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
    }

    inserted: list[tuple[str, dict]] = []

    def _make_table(name: str):
        MagicMock()
        if name == "ai_sessions":
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.maybe_single.return_value = q
            q.execute = AsyncMock(return_value=MagicMock(data=session_row))
            upd_chain = MagicMock()
            q.update = MagicMock(return_value=upd_chain)
            upd_chain.eq.return_value = upd_chain
            upd_chain.execute = AsyncMock(return_value=MagicMock(data=[]))
            return q
        if name == "ai_messages":
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.order.return_value = q
            q.limit.return_value = q
            q.execute = AsyncMock(return_value=MagicMock(data=[]))

            def _ins(payload):
                inserted.append((name, payload))
                ins_chain = MagicMock()
                ins_chain.execute = AsyncMock(
                    return_value=MagicMock(data=[{**payload, "id": str(uuid4())}])
                )
                return ins_chain

            q.insert = _ins
            return q
        return MagicMock()

    client = MagicMock()
    client.table.side_effect = _make_table

    composed = MagicMock()
    composed.agent_id = agent_id
    composed.agent_slug = "script_ai"
    composed.model = "qwen-max"

    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    # Async generator yielding 3 text deltas + finish chunk
    async def _fake_stream(*_a, **_kw):
        yield StreamChunk(delta_text="Hello ")
        yield StreamChunk(delta_text="streaming ")
        yield StreamChunk(
            delta_text="world",
            finish_reason="stop",
            usage={"prompt_tokens": 5, "completion_tokens": 3},
        )

    runner = MagicMock()
    runner.stream_turn = MagicMock(side_effect=_fake_stream)
    runner.run_turn = AsyncMock(return_value={"content": "should-not-be-used"})

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 5
    recorder.completion_tokens = 3
    recorder.set_summaries = MagicMock()
    recorder.record_usage = MagicMock()

    class _CM:
        async def __aenter__(self_inner):
            return recorder

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    fake_agent_record = {
        "id": str(agent_id),
        "slug": "script_ai",
        "model": "qwen-max",
        "budget_per_run_cents": None,
        "fallback_models": [],
    }
    fake_stack = MagicMock()
    fake_stack.runner = runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.primary_model = "qwen-max"
    fake_stack.fallback_chain_active = False
    fake_agent_repo_instance = MagicMock()
    fake_agent_repo_instance.get_by_slug = AsyncMock(return_value=fake_agent_record)

    chunks_received: list[str] = []

    async def _capture(text):
        chunks_received.append(text)

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_async_supabase_admin",
            AsyncMock(return_value=client),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
            return_value=fake_agent_repo_instance,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_adapter",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            return_value=_CM(),
        ),
    ):
        svc = AILibraryChatService()
        out = await svc.chat(
            session_id,
            user_id=user_id,
            content="hi",
            chunk_callback=_capture,
        )

    # Streaming branch was used (run_turn untouched, stream_turn called)
    runner.stream_turn.assert_called_once()
    runner.run_turn.assert_not_called()

    # All deltas were captured by callback in order
    assert chunks_received == ["Hello ", "streaming ", "world"]

    # Persisted assistant message = concatenated streamed text
    asst_inserts = [p for t, p in inserted if p.get("role") == "assistant"]
    assert len(asst_inserts) == 1
    assert asst_inserts[0]["content"] == "Hello streaming world"

    # Response shape stays the same as buffered chat()
    assert out["assistant_message"]["content"] == "Hello streaming world"
    assert out["run_id"] == str(recorder.run_id)


@pytest.mark.asyncio
async def test_chat_chunk_callback_failure_does_not_abort_turn() -> None:
    """P2: a callback that raises must not crash the stream — the turn
    completes and the persisted message still has the full content."""
    from app.services.ai.adapters.base import StreamChunk
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    session_id = uuid4()
    agent_id = uuid4()
    session_row = {
        "id": str(session_id),
        "user_id": str(user_id),
        "agent_slug": "script_ai",
        "agent_id": str(agent_id),
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
    }

    inserted: list[tuple[str, dict]] = []

    def _make_table(name: str):
        if name == "ai_sessions":
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.maybe_single.return_value = q
            q.execute = AsyncMock(return_value=MagicMock(data=session_row))
            upd_chain = MagicMock()
            q.update = MagicMock(return_value=upd_chain)
            upd_chain.eq.return_value = upd_chain
            upd_chain.execute = AsyncMock(return_value=MagicMock(data=[]))
            return q
        if name == "ai_messages":
            q = MagicMock()
            q.select.return_value = q
            q.eq.return_value = q
            q.order.return_value = q
            q.limit.return_value = q
            q.execute = AsyncMock(return_value=MagicMock(data=[]))

            def _ins(payload):
                inserted.append((name, payload))
                ins_chain = MagicMock()
                ins_chain.execute = AsyncMock(
                    return_value=MagicMock(data=[{**payload, "id": str(uuid4())}])
                )
                return ins_chain

            q.insert = _ins
            return q
        return MagicMock()

    client = MagicMock()
    client.table.side_effect = _make_table

    composed = MagicMock()
    composed.agent_id = agent_id
    composed.agent_slug = "script_ai"
    composed.model = "qwen-max"

    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    async def _fake_stream(*_a, **_kw):
        yield StreamChunk(delta_text="part1")
        yield StreamChunk(
            delta_text="part2",
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )

    runner = MagicMock()
    runner.stream_turn = MagicMock(side_effect=_fake_stream)

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 1
    recorder.completion_tokens = 1
    recorder.set_summaries = MagicMock()
    recorder.record_usage = MagicMock()

    class _CM:
        async def __aenter__(self_inner):
            return recorder

        async def __aexit__(self_inner, *a):
            return False

    fake_stack = MagicMock()
    fake_stack.runner = runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.primary_model = "qwen-max"
    fake_stack.fallback_chain_active = False
    fake_agent_repo_instance = MagicMock()
    fake_agent_repo_instance.get_by_slug = AsyncMock(
        return_value={
            "id": str(agent_id),
            "slug": "script_ai",
            "model": "qwen-max",
            "budget_per_run_cents": None,
            "fallback_models": [],
        }
    )

    async def _broken_callback(_text):
        raise RuntimeError("downstream queue full")

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_async_supabase_admin",
            AsyncMock(return_value=client),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
            return_value=fake_agent_repo_instance,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.AgentRunner",
            return_value=runner,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_adapter",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            return_value=_CM(),
        ),
    ):
        svc = AILibraryChatService()
        # Must NOT raise — callback failures are swallowed
        out = await svc.chat(
            session_id,
            user_id=user_id,
            content="hi",
            chunk_callback=_broken_callback,
        )

    # Turn completed cleanly; full content persisted
    asst_inserts = [p for t, p in inserted if p.get("role") == "assistant"]
    assert asst_inserts[0]["content"] == "part1part2"
    assert out["assistant_message"]["content"] == "part1part2"
