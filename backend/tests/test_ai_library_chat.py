"""Unit tests for AILibraryChatService.

Covers session CRUD authorization (404 on foreign session) and the chat
orchestration flow (user-message-first persistence, runner wiring,
assistant message + counters). Runner + PromptComposer + RunRecorder
are patched — we're testing the service's own coordination logic, not
the third-party-ish wiring those classes own.

Store seam (Conversations Phase 3, Task 6): ``AILibraryChatService`` now
defaults to ``ConversationsAiStore`` (the sole surviving ``MessageStore``
implementation — the legacy ai_sessions/ai_messages-backed store and its
router were retired). These tests inject a hand-rolled ``_FakeStore``
directly via the constructor's ``store=`` param instead of mocking a
Supabase table client, mirroring the pattern
``tests/test_task6_run_recorder_store_dispatch.py`` established.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException


class _FakeStore:
    """Minimal MessageStore stub — session lookup + message append/bump,
    with lightweight call tracking for assertions."""

    def __init__(self, session_row: Optional[Dict[str, Any]]) -> None:
        self._session_row = session_row
        self.appended: List[Dict[str, Any]] = []
        self.bumps: List[Dict[str, Any]] = []

    async def get_session(self, *, session_id: Any) -> Optional[Dict[str, Any]]:
        return dict(self._session_row) if self._session_row is not None else None

    async def get_messages(
        self, *, session_id: Any, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return []

    async def append_user_message(
        self,
        *,
        session_id: Any,
        user_id: str,
        content: str,
        attachments: Any = None,
    ) -> Dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "session_id": session_id,
            "role": "user",
            "content": content,
            "attachments": attachments,
        }
        self.appended.append(row)
        return row

    async def latest_assistant_open_question(self, *, session_id: Any = None):
        return None  # phase 2a: no open typed question in this fake

    async def mark_question_answered(
        self, *, message_id: Any = None, value: Any = None, superseded: bool = False
    ) -> None:
        return None

    async def append_assistant_message(
        self,
        *,
        session_id: Any,
        agent_id: Optional[str],
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        metadata: dict,
    ) -> Dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "session_id": session_id,
            "role": "assistant",
            "content": content,
            "agent_id": agent_id,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "metadata_json": metadata,
        }
        self.appended.append(row)
        return row

    async def bump_counters(
        self, *, session_id: Any, add_tokens: int, add_messages: int
    ) -> None:
        self.bumps.append({"total_tokens": add_tokens, "message_count": add_messages})


class _RunRecorderCM:
    """Async context manager standing in for RunRecorder(...)."""

    def __init__(self, recorder: MagicMock) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> MagicMock:
        return self._recorder

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


@pytest.mark.asyncio
async def test_get_session_returns_owned_row() -> None:
    """Owner fetches session → row returned unchanged."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    session_id = uuid4()
    row = {"id": str(session_id), "user_id": str(user_id), "agent_slug": "foo"}

    svc = AILibraryChatService(store=_FakeStore(row))
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

    svc = AILibraryChatService(store=_FakeStore(row))
    with pytest.raises(HTTPException) as exc:
        await svc.get_session(session_id, user_id=user_id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_session_404_when_missing() -> None:
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    svc = AILibraryChatService(store=_FakeStore(None))
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
    store = _FakeStore(session_row)

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
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=lambda **kw: _RunRecorderCM(recorder),
        ),
    ):
        svc = AILibraryChatService(store=store)
        out = await svc.chat(session_id, user_id=user_id, content="Hello")

    # User message was inserted first (before the runner call) — find it.
    user_inserts = [m for m in store.appended if m.get("role") == "user"]
    asst_inserts = [m for m in store.appended if m.get("role") == "assistant"]
    assert len(user_inserts) == 1
    assert user_inserts[0]["content"] == "Hello"
    assert len(asst_inserts) == 1
    assert asst_inserts[0]["content"] == "Hi user"
    assert asst_inserts[0]["prompt_tokens"] == 42
    assert asst_inserts[0]["completion_tokens"] == 7

    # Session counters bumped: prior total (100) + 42+7 = 149; prior count (2) + 2 = 4.
    assert len(store.bumps) == 1
    assert store.bumps[0]["total_tokens"] == 149
    assert store.bumps[0]["message_count"] == 4

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
    store = _FakeStore(session_row)

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
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=lambda **kw: _RunRecorderCM(recorder),
        ),
    ):
        svc = AILibraryChatService(store=store)
        out = await svc.chat(session_id, user_id=user_id, content="route it")

    asst_inserts = [m for m in store.appended if m.get("role") == "assistant"]
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
    store = _FakeStore(session_row)

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
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=lambda **kw: _RunRecorderCM(recorder),
        ),
    ):
        svc = AILibraryChatService(store=store)
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
    asst_inserts = [m for m in store.appended if m.get("role") == "assistant"]
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
    store = _FakeStore(session_row)

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
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=lambda **kw: _RunRecorderCM(recorder),
        ),
    ):
        svc = AILibraryChatService(store=store)
        # Must NOT raise — callback failures are swallowed
        out = await svc.chat(
            session_id,
            user_id=user_id,
            content="hi",
            chunk_callback=_broken_callback,
        )

    # Turn completed cleanly; full content persisted
    asst_inserts = [m for m in store.appended if m.get("role") == "assistant"]
    assert asst_inserts[0]["content"] == "part1part2"
    assert out["assistant_message"]["content"] == "part1part2"


@pytest.mark.asyncio
async def test_chat_stream_user_cancel_degrades_gracefully() -> None:
    """RunAborted mid-stream (user cancel) must NOT propagate as an
    unhandled exception (pre-fix behavior: 500). It mirrors run_turn's
    buffered-cancel contract: partial content + any tool trace gathered
    so far are kept, result carries cancelled=True. (#984 review
    follow-up — the streaming path had no RunAborted handler at all.)"""
    from app.agent_framework.abort_controller import RunAborted
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
    store = _FakeStore(session_row)

    composed = MagicMock()
    composed.agent_id = agent_id
    composed.agent_slug = "script_ai"
    composed.model = "qwen-max"

    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    async def _fake_stream(*_a, **_kw):
        yield StreamChunk(delta_text="partial ")
        yield StreamChunk(delta_text="answer")
        raise RunAborted("user cancel mid-stream")

    runner = MagicMock()
    runner.stream_turn = MagicMock(side_effect=_fake_stream)
    runner.run_turn = AsyncMock(return_value={"content": "unused"})

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 5
    recorder.completion_tokens = 3
    recorder.set_summaries = MagicMock()
    recorder.record_usage = MagicMock()

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

    async def _capture(_text):
        pass

    with (
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
            "app.services.ai.providers.ai_provider_helpers.resolve_db_adapter",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.SkillToolService",
            return_value=MagicMock(),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=lambda **kw: _RunRecorderCM(recorder),
        ),
    ):
        svc = AILibraryChatService(store=store)
        out = await svc.chat(
            session_id,
            user_id=user_id,
            content="hi",
            chunk_callback=_capture,
        )

    assert out["cancelled"] is True
    # Partial content persisted as the assistant turn (not lost)
    assert out["assistant_message"] is not None


@pytest.mark.asyncio
async def test_chat_stream_error_event_carries_provider_code() -> None:
    """2026-08-11 终审 Critical 1: chat_stream's own except-block used to
    hand-roll ``{"error": f"{type(exc).__name__}: {exc}"}`` with no ``code``
    key at all — the router's ``_stream_error_payload`` typed mapping never
    ran on this path because the chat task's exception is caught here first.
    Now both delegate to ``provider_errors.stream_error_data``, so a
    429-caused ``AllModelsFailed`` from the underlying turn must surface as
    a typed ``provider_rate_limit`` error event, not a raw exception string.
    """
    import httpx

    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.ai.llm.llm_fallback_chain import AllModelsFailed

    user_id = uuid4()
    session_id = uuid4()

    request = httpx.Request("POST", "https://ark.example/api")
    inner = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request)
    )
    exc = AllModelsFailed("primary + 0 fallback(s) exhausted")
    exc.__cause__ = inner

    svc = AILibraryChatService(store=_FakeStore(None))
    svc.chat = AsyncMock(side_effect=exc)  # chat_stream drives self.chat()

    events = [
        evt async for evt in svc.chat_stream(session_id, user_id=user_id, content="hi")
    ]

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["data"]["code"] == "provider_rate_limit"
    assert "rate-limiting" in error_events[0]["data"]["error"]


@pytest.mark.asyncio
async def test_chat_stream_error_event_internal_error_for_plain_exception() -> None:
    """Non-provider exceptions must keep the old shape (type name + str)
    with code=internal_error — same contract as the router-level helper."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    session_id = uuid4()

    svc = AILibraryChatService(store=_FakeStore(None))
    svc.chat = AsyncMock(side_effect=RuntimeError("boom"))

    events = [
        evt async for evt in svc.chat_stream(session_id, user_id=user_id, content="hi")
    ]

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["data"]["code"] == "internal_error"
    assert "RuntimeError" in error_events[0]["data"]["error"]


# ─── Session listing: cross-agent + server-side search (P1) ─────────────────


@pytest.mark.asyncio
async def test_list_sessions_passes_search_through_to_store() -> None:
    """Service forwards agent_slug=None (cross-agent) + search to the store."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    captured: Dict[str, Any] = {}

    class _ListingStore(_FakeStore):
        async def list_sessions(self, **kwargs: Any) -> List[Dict[str, Any]]:
            captured.update(kwargs)
            return []

    svc = AILibraryChatService(store=_ListingStore(None))
    await svc.list_sessions(user_id=uuid4(), search="outline", limit=30)

    assert captured["agent_slug"] is None  # cross-agent: no filter
    assert captured["search"] == "outline"
    assert captured["limit"] == 30


@pytest.mark.asyncio
async def test_store_search_builds_escaped_ilike() -> None:
    """ConversationsAiStore escapes LIKE metacharacters and adds the ILIKE
    clause only when a search term is given.

    ORM (Phase B4): list_sessions reads through ``app.db.session.read_scope()``
    now, not ``db_engine.fetch_all`` — the harness patches read_scope and
    inspects the compiled statement/binds instead of a raw SQL string.
    """
    from contextlib import asynccontextmanager

    from sqlalchemy.dialects import postgresql

    import app.db.session as db_session
    from app.services.ai.chat.conversations_ai_store import ConversationsAiStore

    store = ConversationsAiStore()
    seen: Dict[str, Any] = {}

    class _FakeResult:
        def mappings(self) -> "_FakeResult":
            return self

        def all(self) -> List[Dict[str, Any]]:
            return []

    class _FakeSession:
        async def execute(self, stmt: Any) -> _FakeResult:
            compiled = stmt.compile(dialect=postgresql.dialect())
            seen["sql"] = str(compiled)
            seen["params"] = dict(compiled.params)
            return _FakeResult()

    @asynccontextmanager
    async def fake_read_scope():
        yield _FakeSession()

    with patch.object(db_session, "read_scope", fake_read_scope):
        await store.list_sessions(
            user_id=str(uuid4()),
            agent_slug=None,
            project_id=None,
            limit=50,
            search="50%_done",
        )

    assert "public.conversations.title ILIKE" in seen["sql"]
    # % and _ must arrive escaped so user input matches literally.
    assert seen["params"]["title_1"] == "%50\\%\\_done%"

    # No search → no ILIKE clause at all.
    with patch.object(db_session, "read_scope", fake_read_scope):
        await store.list_sessions(
            user_id=str(uuid4()), agent_slug=None, project_id=None, limit=50
        )
    assert "ILIKE" not in seen["sql"]


@pytest.mark.asyncio
async def test_list_all_chat_sessions_endpoint_validates_and_delegates() -> None:
    """GET /sessions rejects bad limits and forwards search to the service."""
    from app.api.ai_library_router import list_all_chat_sessions

    auth = MagicMock()
    auth.user_id = str(uuid4())

    svc = MagicMock()
    svc.list_sessions = AsyncMock(return_value=[])

    with patch("app.api.ai_library_router.AILibraryChatService", return_value=svc):
        with pytest.raises(HTTPException) as exc:
            await list_all_chat_sessions(auth, limit=0)
        assert exc.value.status_code == 400

        out = await list_all_chat_sessions(auth, search="plan", limit=20)

    assert out == []
    call = svc.list_sessions.await_args
    assert call.kwargs["search"] == "plan"
    assert call.kwargs["limit"] == 20
    assert "agent_slug" not in call.kwargs  # cross-agent list
