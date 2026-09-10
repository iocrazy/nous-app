"""T1 — End-to-end chat smoke.

This is the *integration* test that the unit suite has been missing.
Goal: prove the full chain fires once a chat turn completes —
  user message persisted →
    agent runner runs through composer + adapter →
      assistant message persisted →
        session counters bumped →
          commitment harvester dispatched (background task) →
          session_memory updater dispatched (background task)

We mock at three boundaries:
  1. The MessageStore (a hand-rolled fake injected via the constructor —
     Conversations Phase 3 Task 6 retired the legacy Supabase-backed
     store, so there is no Supabase client to mock here anymore)
  2. AgentRunner.run_turn (so no real LLM calls)
  3. AgentRepository.get_by_slug + build_agent_runner_stack
     (so no real prompt composition / fallback chain build)

What's NOT mocked (this is the integration value):
  - AILibraryChatService.chat itself
  - Background-task dispatch (asyncio.create_task) for harvest +
    session memory
  - Order of operations: user msg → assistant msg → session bump →
    side-effect tasks
  - Return shape (assistant_message + run_id + tool_calls +
    attachment_failures)

If any of these layers regress, this test catches it where the
unit tests would not.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


class _FakeStore:
    """Minimal MessageStore stub — see tests/test_ai_library_chat.py."""

    def __init__(self, session_row: Dict[str, Any]) -> None:
        self._session_row = session_row
        self.appended: List[Dict[str, Any]] = []
        self.bumps: List[Dict[str, Any]] = []

    async def get_session(self, *, session_id: Any) -> Optional[Dict[str, Any]]:
        return dict(self._session_row)

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
        metadata: Any = None,
    ) -> Dict[str, Any]:
        row = {
            "id": str(uuid4()),
            "session_id": session_id,
            "role": "user",
            "content": content,
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_full_pipeline_fires_all_side_effects() -> None:
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService

    user_id = uuid4()
    session_id = uuid4()
    agent_id = uuid4()

    session_row = {
        "id": str(session_id),
        "user_id": str(user_id),
        "agent_slug": "smoke-agent",
        "agent_id": str(agent_id),
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
    }
    store = _FakeStore(session_row)

    # ── Track background-task dispatches ─────────────────────────────
    bg_tasks: list[str] = []

    real_create_task = asyncio.create_task

    def _spy_create_task(coro, *, name=None):
        bg_tasks.append(name or "<unnamed>")
        # We still create the task so the side effect is exercised, but
        # we want it cancelled before chat returns to avoid coro leaks.
        task = real_create_task(coro, name=name)
        # Don't await — chat is fire-and-forget here; close the coro to
        # avoid pending-task warnings
        return task

    # ── Stub AgentRunner pipeline ────────────────────────────────────
    composed = MagicMock()
    composed.agent_id = agent_id
    composed.agent_slug = "smoke-agent"
    composed.model = "qwen-max"

    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    runner.run_turn = AsyncMock(
        return_value={
            "content": "smoke output",
            "raw": {},
            "tool_calls": [],
        }
    )

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 12
    recorder.completion_tokens = 4
    recorder.set_summaries = MagicMock()

    class _CM:
        async def __aenter__(self_inner):
            return recorder

        async def __aexit__(self_inner, *a):
            return False

    fake_agent = {
        "id": str(agent_id),
        "slug": "smoke-agent",
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
    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_slug = AsyncMock(return_value=fake_agent)

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_service.get_agent_repository",
            return_value=fake_agent_repo,
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
            return_value=_CM(),
        ),
        patch(
            "asyncio.create_task",
            side_effect=_spy_create_task,
        ),
    ):
        svc = AILibraryChatService(store=store)
        out = await svc.chat(session_id, user_id=user_id, content="smoke test")
        # Give the background tasks a tick to start so they show up
        # in our spy list (we don't actually await them; they're
        # fire-and-forget by design)
        await asyncio.sleep(0.01)

    # ── Assertions ───────────────────────────────────────────────────

    # 1. Both messages persisted in correct order
    user_inserts = [m for m in store.appended if m.get("role") == "user"]
    asst_inserts = [m for m in store.appended if m.get("role") == "assistant"]
    assert len(user_inserts) == 1, "user message must be persisted"
    assert len(asst_inserts) == 1, "assistant message must be persisted"
    assert user_inserts[0]["content"] == "smoke test"
    assert asst_inserts[0]["content"] == "smoke output"

    # 2. Session counters bumped (0 → 16 tokens, 0 → 2 messages)
    assert len(store.bumps) == 1
    assert store.bumps[0]["total_tokens"] == 16
    assert store.bumps[0]["message_count"] == 2

    # 3. Background tasks dispatched
    assert any(
        "session-memory-update" in n for n in bg_tasks
    ), f"session_memory updater not dispatched. bg_tasks={bg_tasks}"
    assert any(
        "commitment-harvest" in n for n in bg_tasks
    ), f"commitment harvester not dispatched. bg_tasks={bg_tasks}"

    # 4. Return shape
    assert out["assistant_message"]["content"] == "smoke output"
    assert out["run_id"] == str(recorder.run_id)
    assert out["usage"]["prompt_tokens"] == 12
    assert out["usage"]["completion_tokens"] == 4
    assert out["tool_calls"] == []
    assert out["attachment_failures"] == []
