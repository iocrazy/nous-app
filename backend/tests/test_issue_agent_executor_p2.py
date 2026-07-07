"""issue_agent_executor is the SECOND caller of the turn engine
(``run_session_turn(trigger='issue_dispatch')``) — chat() is the first,
covered by test_task6_run_recorder_store_dispatch.py.

Conversations Phase 3, Task 6 collapsed the 1:1 dual-store compatibility
layer — ``ConversationsAiStore`` is now the sole ``MessageStore``
implementation, so this file drives a single scenario instead of the old
off/shadow/on parametrization.

The test below drives the REAL dispatch code: ``issue_session.
get_or_create_issue_session`` (real function) creates a session through a
hand-rolled fake ``MessageStore`` — injected by monkeypatching the
``AILibraryChatService`` symbol ``issue_session.py`` imported into its own
namespace (it constructs ``AILibraryChatService()`` with no injectable
store param, so the store factory/class is the seam). A second,
separately-constructed ``AILibraryChatService(store=fake_store)`` —
matching production, where each request builds its own service — then runs
the REAL ``run_session_turn(trigger="issue_dispatch")`` (buffered/no
chunk_callback; see the note below on why) against that session id, wrapped
in the same runner/composer/RunRecorder mocks
test_task6_run_recorder_store_dispatch.py uses. This proves: session
creation lands on the store, the FinishIssue tool is really injected into
the composed prompt, ``trigger`` reaches the RunRecorder kwargs unchanged,
and the store-kind → session_id/conversation_id dispatch (Task 6's
contract) also holds for this trigger.

Why this calls ``run_session_turn`` directly instead of through
``run_issue_agent()``: ``run_issue_agent`` always passes a ``chunk_callback``,
which routes ``_run_session_turn_inner`` down the STREAMING branch
(``runner.stream_turn``). Calling ``run_session_turn`` without a
``chunk_callback`` takes the buffered ``runner.run_turn`` branch instead,
which is what test_task6_run_recorder_store_dispatch.py already relies on
and is the faithful way to observe the FinishIssue-injection /
RunRecorder-kwargs contract that Task 6 established.

``run_issue_agent()``'s OWN logic (nudge/content selection, trigger
forwarding, FinishIssue outcome extraction via the real
``extract_issue_outcome``) never read the routing flag and is already
covered mode-agnostically by tests/test_issue_agent_executor.py — that
coverage isn't duplicated here.

Bugfix regression (see ``test_issue_agent_run_through_real_streaming_path_
surfaces_finish_issue`` below): the STREAMING branch previously discarded
structured ``tool_call_delta`` chunks entirely — ``stream_turn`` never built
a ``tool_call_trace`` and ``ai_library_chat_service`` hard-coded
``tool_calls_trace = []`` in that branch, never appending. Since
``run_issue_agent`` ALWAYS streams, this meant 100% of real issue turns lost
their FinishIssue declaration — ``extract_issue_outcome`` always saw ``[]``
and returned ``(None, None)``, so issue lifecycle routing (auto-close /
needs_input / bounded continuation) silently degraded to the ``in_review``
default. That test drives ``run_issue_agent()`` through the REAL streaming
path (a real ``AgentRunner.stream_turn`` wired to a fake adapter, not a
mocked ``run_session_turn``) to prove the fix.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.services.ai.tools.finish_issue_tool import (
    extract_issue_outcome,
    finish_issue_handler,
)

pytestmark = pytest.mark.asyncio


class _RunRecorderCM:
    """Async context manager standing in for RunRecorder(...)."""

    def __init__(self, recorder: MagicMock) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> MagicMock:
        return self._recorder

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeStore:
    """Minimal MessageStore stub — same shape as
    test_task6_run_recorder_store_dispatch.py's ``_FakeStore``."""

    def __init__(self, session_row: Dict[str, Any]) -> None:
        self._session_row = session_row

    async def create_session(self, **_kw: Any) -> Dict[str, Any]:
        return self._session_row

    async def get_session(self, *, session_id: int) -> Optional[Dict[str, Any]]:
        return self._session_row

    async def get_messages(
        self, *, session_id: int, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return []

    async def append_user_message(
        self, *, session_id: int, user_id: str, content: str, attachments: Any = None
    ) -> Dict[str, Any]:
        return {
            "id": "u-1",
            "session_id": session_id,
            "role": "user",
            "content": content,
        }

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


def _default_tool_calls() -> List[Dict[str, Any]]:
    return [
        {
            "name": "FinishIssue",
            "args": {"outcome": "completed"},
            "result": {
                "acknowledged": True,
                "outcome": "completed",
                "reason": "shipped",
            },
        }
    ]


async def test_issue_session_and_turn_link_via_conversation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real get_or_create_issue_session -> real create_session on a fake
    ConversationsAiStore-shaped store -> real run_session_turn
    (trigger="issue_dispatch", buffered) against that session id."""
    from app.db import engine as db_engine_module
    from app.schemas.ai_library import ComposedSystemPrompt
    from app.services.ai.chat import ai_library_chat_service as chat_service_module
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.issues import issue_session as session_module

    agent_id = uuid4()
    user_id = uuid4()
    issue_id = 4242
    session_id_int = 555555

    session_row = {
        "id": session_id_int,
        "user_id": str(user_id),
        "agent_slug": "issue_agent",
        "agent_id": str(agent_id),
        "title": "Ship the thing",
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
        "store_kind": "conversations",
    }
    store = _FakeStore(session_row)

    def _service_factory(*_a: Any, **_kw: Any) -> AILibraryChatService:
        # A fresh AILibraryChatService per construction — matches production
        # (one per call site) — wrapping the SAME fake store so state
        # persists across call sites exactly like a real DB would.
        return AILibraryChatService(store=store)

    # issue_session.py constructs its own AILibraryChatService() with no
    # injectable store param -- monkeypatch the symbol it resolved into its
    # own namespace (the store-factory seam).
    monkeypatch.setattr(session_module, "AILibraryChatService", _service_factory)

    issue_row = {
        "ai_session_id": None,
        "title": "Ship the thing",
        "assignee_agent_id": str(agent_id),
        "created_by_user_id": str(user_id),
        "assignee_user_id": None,
    }

    async def _fake_fetch_one(
        sql: str, params: Optional[dict] = None
    ) -> Optional[dict]:
        assert "public.issues" in sql
        return dict(issue_row)

    executed: List[Any] = []

    async def _fake_execute(sql: str, params: Optional[dict] = None) -> int:
        executed.append((sql, params))
        return 1  # backfill "wins" -- no concurrent-writer race in this test

    monkeypatch.setattr(db_engine_module, "fetch_one", _fake_fetch_one)
    monkeypatch.setattr(db_engine_module, "execute", _fake_execute)

    fake_agent_record = {
        "id": str(agent_id),
        "slug": "issue_agent",
        "model": "qwen-max",
        "budget_per_run_cents": None,
        "fallback_models": [],
    }
    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_id = AsyncMock(return_value=fake_agent_record)
    fake_agent_repo.get_by_slug = AsyncMock(return_value=fake_agent_record)
    monkeypatch.setattr(session_module, "get_agent_repository", lambda: fake_agent_repo)
    monkeypatch.setattr(
        chat_service_module, "get_agent_repository", lambda: fake_agent_repo
    )

    # --- Step 1: real get_or_create_issue_session -> real create_session
    # against the fake store. ---
    session_id = await session_module.get_or_create_issue_session(issue_id)
    assert session_id == str(session_id_int)
    assert executed, "backfill UPDATE should have run for a brand-new session"

    # --- Step 2: real run_session_turn(trigger="issue_dispatch"), buffered
    # (no chunk_callback) -- see module docstring for why buffered. ---
    composed = ComposedSystemPrompt(
        agent_id=agent_id,
        agent_slug="issue_agent",
        model="qwen-max",
        temperature=0.7,
        max_tokens=4096,
        system_message="You are the issue agent.",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp-issue",
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    runner = MagicMock()
    captured_turn: Dict[str, Any] = {}

    async def _run_turn(composed_arg, *, user_messages, recorder):
        # Capture what the FinishIssue-injection block actually wired,
        # BEFORE the turn's `finally` clears runner.finish_issue_handler.
        captured_turn["tools"] = list(composed_arg.tools or [])
        captured_turn["system_message"] = composed_arg.system_message
        captured_turn["finish_issue_handler"] = runner.finish_issue_handler
        return {"content": "did the thing", "tool_calls": _default_tool_calls()}

    runner.run_turn = AsyncMock(side_effect=_run_turn)

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 3
    recorder.completion_tokens = 5
    recorder.set_summaries = MagicMock()

    captured_kwargs: Dict[str, Any] = {}

    def _fake_run_recorder(**kwargs: Any) -> _RunRecorderCM:
        captured_kwargs.update(kwargs)
        return _RunRecorderCM(recorder)

    fake_stack = MagicMock()
    fake_stack.runner = runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.agent_memory_facts = []
    fake_stack.primary_model = "qwen-max"
    fake_stack.fallback_chain_active = False

    with (
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
        turn_service = AILibraryChatService(store=store)
        result = await turn_service.run_session_turn(
            session_id,
            user_id=user_id,
            content="Task: Ship the thing",
            trigger="issue_dispatch",
        )

    # trigger reaches RunRecorder kwargs unchanged, and store_kind=
    # 'conversations' links via conversation_id, not session_id (Task 6
    # contract -- agent_runs.session_id FKs the legacy ai_sessions table,
    # would 23503 on a conversations id).
    assert captured_kwargs["trigger"] == "issue_dispatch"
    assert captured_kwargs["session_id"] is None
    assert captured_kwargs["conversation_id"] == session_id_int
    assert isinstance(captured_kwargs["conversation_id"], int)

    # FinishIssue tool actually injected for this trigger.
    tool_names = [t["function"]["name"] for t in captured_turn["tools"]]
    assert "FinishIssue" in tool_names
    assert captured_turn["finish_issue_handler"] is finish_issue_handler
    assert "FinishIssue" in captured_turn["system_message"]

    # Outcome extraction from the returned tool_calls still works.
    outcome, reason = extract_issue_outcome(result.get("tool_calls"))
    assert outcome == "completed"
    assert reason == "shipped"


class _FinishIssueStreamingAdapter:
    """Scripted adapter: emits a FinishIssue tool_call in iteration 1 (via
    tool_call_delta, the real streaming shape), then a plain no-tool-call
    reply in iteration 2 -- a realistic "declare, then answer" issue turn.
    """

    def __init__(self) -> None:
        self.iter = 0

    async def call(self, composed: Any, messages: Any) -> dict:
        # Non-streaming fallback -- unused by this test (stream() always
        # exists here) but required by the adapter contract.
        return {"choices": [{"message": {"content": "fallback"}}]}

    async def stream(self, composed: Any, messages: Any):
        self.iter += 1
        if self.iter == 1:
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "function": {"name": "FinishIssue", "arguments": ""},
                        },
                    ]
                }
            )
            yield StreamChunk(
                tool_call_delta={
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {
                                "arguments": json.dumps(
                                    {
                                        "outcome": "completed",
                                        "reason": "shipped it",
                                    }
                                ),
                            },
                        },
                    ]
                }
            )
            yield StreamChunk(
                finish_reason="tool_calls",
                usage={"prompt_tokens": 10, "completion_tokens": 3},
            )
        else:
            yield StreamChunk(delta_text="Done — shipped it.")
            yield StreamChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 20, "completion_tokens": 5},
            )


async def test_issue_agent_run_through_real_streaming_path_surfaces_finish_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE regression test for the streaming tool_call_trace bug.

    ``run_issue_agent`` ALWAYS passes a ``chunk_callback`` (it streams tokens
    to Redis as they arrive), so production issue turns run exclusively
    through ``runner.stream_turn`` -- never ``run_turn``. This drives that
    real path end to end: real ``get_or_create_issue_session``, a real
    ``AgentRunner`` instance (not mocked) wired to a fake adapter whose
    ``stream()`` emits a FinishIssue tool_call, and the real
    ``run_session_turn`` / streaming branch of ``ai_library_chat_service``.
    Only ``build_agent_runner_stack`` / ``PromptComposer`` / ``RunRecorder``
    are stubbed (same seam test_task6_run_recorder_store_dispatch.py and the
    sibling test above use) -- ``run_session_turn`` itself is never mocked.

    Before the fix: ``result["tool_calls"]`` was always ``[]`` on this path,
    so ``extract_issue_outcome`` returned ``(None, None)`` even though the
    agent DID call FinishIssue -- the declaration was silently dropped
    between ``stream_turn`` and the chat service's streaming branch.
    """
    from app.db import engine as db_engine_module
    from app.schemas.ai_library import ComposedSystemPrompt
    from app.services.ai.chat import ai_library_chat_service as chat_service_module
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.issues import issue_agent_executor as executor_module
    from app.services.issues import issue_session as session_module

    agent_id = uuid4()
    user_id = uuid4()
    issue_id = 4343
    session_id_int = 666666

    session_row = {
        "id": session_id_int,
        "user_id": str(user_id),
        "agent_slug": "issue_agent",
        "agent_id": str(agent_id),
        "title": "Ship the thing",
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
        "store_kind": "conversations",
    }
    store = _FakeStore(session_row)

    def _service_factory(*_a: Any, **_kw: Any) -> AILibraryChatService:
        return AILibraryChatService(store=store)

    # issue_session.py's seam (existing pattern, sibling test above) --
    # AND issue_agent_executor.py's OWN seam: it hardcodes
    # ``AILibraryChatService()`` with no injectable store param (unlike
    # issue_session.py, this wasn't previously patched because the sibling
    # test above deliberately bypasses run_issue_agent() and calls
    # run_session_turn directly). Patching both symbols is what makes it
    # possible to drive run_issue_agent() itself against the fake store.
    monkeypatch.setattr(session_module, "AILibraryChatService", _service_factory)
    monkeypatch.setattr(executor_module, "AILibraryChatService", _service_factory)

    issue_row = {
        "ai_session_id": None,
        "title": "Ship the thing",
        "assignee_agent_id": str(agent_id),
        "created_by_user_id": str(user_id),
        "assignee_user_id": None,
    }

    async def _fake_fetch_one(
        sql: str, params: Optional[dict] = None
    ) -> Optional[dict]:
        assert "public.issues" in sql
        return dict(issue_row)

    async def _fake_execute(sql: str, params: Optional[dict] = None) -> int:
        return 1

    monkeypatch.setattr(db_engine_module, "fetch_one", _fake_fetch_one)
    monkeypatch.setattr(db_engine_module, "execute", _fake_execute)

    fake_agent_record = {
        "id": str(agent_id),
        "slug": "issue_agent",
        "model": "qwen-max",
        "budget_per_run_cents": None,
        "fallback_models": [],
    }
    fake_agent_repo = MagicMock()
    fake_agent_repo.get_by_id = AsyncMock(return_value=fake_agent_record)
    fake_agent_repo.get_by_slug = AsyncMock(return_value=fake_agent_record)
    monkeypatch.setattr(session_module, "get_agent_repository", lambda: fake_agent_repo)
    monkeypatch.setattr(
        chat_service_module, "get_agent_repository", lambda: fake_agent_repo
    )

    composed = ComposedSystemPrompt(
        agent_id=agent_id,
        agent_slug="issue_agent",
        model="qwen-max",
        temperature=0.7,
        max_tokens=4096,
        system_message="You are the issue agent.",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="fp-issue-stream",
    )
    composer = MagicMock()
    composer.compose = AsyncMock(return_value=composed)

    # A REAL AgentRunner -- not a mock -- driven by the fake adapter above.
    # This is the crux of the test: it exercises stream_turn's actual
    # tool-execution loop, not a stand-in.
    real_runner = AgentRunner(adapter=_FinishIssueStreamingAdapter(), skill_tool=None)

    recorder = MagicMock()
    recorder.run_id = uuid4()
    recorder.prompt_tokens = 3
    recorder.completion_tokens = 5
    recorder.set_summaries = MagicMock()
    recorder.heartbeat = AsyncMock()
    recorder.check_cancelled = AsyncMock(return_value=False)
    recorder.record_usage = MagicMock()
    recorder.record_event = AsyncMock()

    captured_kwargs: Dict[str, Any] = {}

    def _fake_run_recorder(**kwargs: Any) -> _RunRecorderCM:
        captured_kwargs.update(kwargs)
        return _RunRecorderCM(recorder)

    fake_stack = MagicMock()
    fake_stack.runner = real_runner
    fake_stack.graph_facts = []
    fake_stack.user_context = None
    fake_stack.agent_memory_facts = []
    fake_stack.primary_model = "qwen-max"
    fake_stack.fallback_chain_active = False

    published_chunks: List[str] = []
    published_messages: List[Dict[str, Any]] = []

    with (
        patch(
            "app.services.ai.chat.ai_library_chat_service.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.PromptComposer",
            return_value=composer,
        ),
        patch(
            "app.services.ai.chat.ai_library_chat_service.RunRecorder",
            side_effect=_fake_run_recorder,
        ),
        patch.object(
            executor_module,
            "publish_chunk",
            AsyncMock(side_effect=lambda iid, d: published_chunks.append(d)),
        ),
        patch.object(
            executor_module,
            "publish_message",
            AsyncMock(side_effect=lambda iid, row, **k: published_messages.append(row)),
        ),
        patch.object(executor_module, "publish_status", AsyncMock()),
    ):
        session_id = await session_module.get_or_create_issue_session(issue_id)
        assert session_id == str(session_id_int)

        result = await executor_module.run_issue_agent(
            issue={"id": issue_id, "title": "Ship the thing", "description": ""},
            agent_id=str(agent_id),
            user_id=str(user_id),
        )

    assert captured_kwargs["trigger"] == "issue_dispatch"
    assert published_chunks, "chunk_callback must have really fired (streaming path)"
    assert published_messages and "Done" in published_messages[0]["content"]

    # THE bug, fixed: the streaming path now surfaces the FinishIssue
    # declaration instead of losing it to an empty tool_calls_trace.
    assert result["outcome"] == "completed"
    assert result["reason"] == "shipped it"
    assert "Done" in result["content"]
