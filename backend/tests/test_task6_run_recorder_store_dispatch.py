"""Task 6 / R1: AILibraryChatService.chat's RunRecorder call must be
store-aware — a conversations-backed session (``store_kind='conversations'``
on the session row) links telemetry via ``agent_runs.conversation_id`` and
passes ``session_id=None``; a legacy session (``store_kind='legacy'`` or the
key simply absent, e.g. an old row) keeps the exact byte-identical
``session_id=...`` behavior it has today.

``agent_runs.session_id`` FKs ``ai_sessions.id`` (mig 231) — binding a
conversations.id there would violate that FK. ``agent_runs.conversation_id``
(mig 331) is the structural link for new-store sessions.

Approach: drive the smallest real turn path (``AILibraryChatService.chat``)
with a hand-rolled fake ``MessageStore`` injected via the constructor
(``AILibraryChatService(store=...)``), so this test exercises the actual
dispatch line in the service rather than re-asserting the store's own
row-shape (already covered by test_conversations_ai_store.py). Runner/
composer/agent-repo wiring is patched the same way test_ai_library_chat.py's
``test_chat_persists_both_messages_and_bumps_counters`` does — this test is
NOT re-testing that coordination logic, only the RunRecorder kwargs it
computes off the session row.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


class _FakeStore:
    """Minimal MessageStore stub — just enough for one chat() turn."""

    def __init__(self, session_row: Dict[str, Any]) -> None:
        self._session_row = session_row

    async def get_session(self, *, session_id: int) -> Optional[Dict[str, Any]]:
        return self._session_row

    async def get_messages(
        self, *, session_id: int, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return []

    async def append_user_message(
        self,
        *,
        session_id: int,
        user_id: str,
        content: str,
        attachments: Any = None,
        metadata: Any = None,
    ) -> Dict[str, Any]:
        return {
            "id": "u-1",
            "session_id": session_id,
            "role": "user",
            "content": content,
        }

    async def latest_assistant_open_question(self, *, session_id: Any = None):
        return None  # phase 2a: no open typed question in this fake

    async def mark_question_answered(
        self, *, message_id: Any = None, value: Any = None, superseded: bool = False
    ) -> None:
        return None

    async def append_assistant_message(
        self,
        *,
        session_id: int,
        agent_id: Optional[str],
        content: str,
        prompt_tokens: int,
        completion_tokens: int,
        metadata: dict,
    ) -> Dict[str, Any]:
        return {
            "id": "a-1",
            "session_id": session_id,
            "role": "assistant",
            "content": content,
            "metadata_json": metadata,
        }

    async def bump_counters(
        self, *, session_id: int, add_tokens: int, add_messages: int
    ) -> None:
        return None


class _RunRecorderCM:
    """Async context manager standing in for RunRecorder(...)."""

    def __init__(self, recorder: MagicMock) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> MagicMock:
        return self._recorder

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


async def _run_chat_turn(session_row: Dict[str, Any]) -> Dict[str, Any]:
    """Drive AILibraryChatService.chat() with a fake store + fully-mocked
    runner stack, returning the exact kwargs RunRecorder(...) was
    constructed with."""
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    agent_id = uuid4()
    # Ownership check in AILibraryChatService.get_session compares
    # str(session["user_id"]) to str(user_id) — stamp the row with the
    # user_id this turn actually authenticates as.
    session_row = {**session_row, "user_id": str(user_id)}
    store = _FakeStore(session_row)

    composed = MagicMock()
    composed.agent_id = agent_id
    composed.agent_slug = "script_ai"
    composed.model = "qwen-max"

    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(return_value={"content": "hi there", "raw": {}})

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 1
    recorder.completion_tokens = 1
    recorder.set_summaries = MagicMock()

    captured_kwargs: Dict[str, Any] = {}

    def _fake_run_recorder(**kwargs: Any) -> _RunRecorderCM:
        captured_kwargs.update(kwargs)
        return _RunRecorderCM(recorder)

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
    fake_stack.agent_memory_facts = []
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
            side_effect=_fake_run_recorder,
        ),
    ):
        svc = AILibraryChatService(store=store)
        await svc.chat(session_row["id"], user_id=user_id, content="Hello")

    return captured_kwargs


@pytest.mark.asyncio
async def test_conversations_session_links_via_conversation_id_not_session_id() -> None:
    """store_kind='conversations' -> RunRecorder gets conversation_id=int(id),
    session_id=None (agent_runs.session_id FKs ai_sessions; would 23503 on a
    conversations id)."""
    session_id = 285274231427099
    session_row = {
        "id": session_id,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "agent_slug": "script_ai",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
        "store_kind": "conversations",
    }

    captured_kwargs = await _run_chat_turn(session_row)

    assert captured_kwargs["conversation_id"] == session_id
    assert isinstance(captured_kwargs["conversation_id"], int)
    assert captured_kwargs["session_id"] is None


@pytest.mark.asyncio
async def test_legacy_session_keeps_session_id_kwarg_unchanged() -> None:
    """store_kind='legacy' (the normal case today) -> RunRecorder still gets
    session_id=<id>, conversation_id=None — byte-identical to pre-Task-6
    behavior."""
    session_id = str(uuid4().int % (2**62))
    session_row = {
        "id": session_id,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "agent_slug": "script_ai",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "total_tokens": 5,
        "message_count": 1,
        "team_id": None,
        "project_id": None,
        "store_kind": "legacy",
    }

    captured_kwargs = await _run_chat_turn(session_row)

    assert captured_kwargs["session_id"] == session_id
    assert captured_kwargs["conversation_id"] is None


@pytest.mark.asyncio
async def test_missing_store_kind_key_defaults_to_legacy_behavior() -> None:
    """Defensive: a session row with NO store_kind key at all (shouldn't
    happen post-Task-6, but matters as a defensive default for any future
    code path that forgets to stamp it) must NOT be treated as
    conversations-backed — session.get('store_kind') returns None, which
    != 'conversations'."""
    session_id = str(uuid4().int % (2**62))
    session_row = {
        "id": session_id,
        "user_id": "11111111-1111-1111-1111-111111111111",
        "agent_slug": "script_ai",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
        # no "store_kind" key at all
    }

    captured_kwargs = await _run_chat_turn(session_row)

    assert captured_kwargs["session_id"] == session_id
    assert captured_kwargs["conversation_id"] is None
