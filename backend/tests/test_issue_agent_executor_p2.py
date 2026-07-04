"""Task 7 (Conversations Phase 2): issue_agent_executor is the SECOND caller
of the turn engine (``run_session_turn(trigger='issue_dispatch')``) — chat()
is the first, covered by test_task6_run_recorder_store_dispatch.py. This
proves the issue path is unaffected across all three ``FEATURE_DIRECT_
CONVERSATIONS`` modes (off / shadow / on).

Two groups of tests, at two different seams:

* **Group A (``Test*StoreRouting``)** — drives the REAL dispatch code:
  ``issue_session.get_or_create_issue_session`` (real function) creates a
  session through a REAL ``RoutedAiStore(legacy=AsyncMock(), new=AsyncMock())``
  — same technique ``tests/test_store_router.py`` uses — injected by
  monkeypatching the ``AILibraryChatService`` symbol ``issue_session.py``
  imported into its own namespace (it constructs ``AILibraryChatService()``
  with no injectable store param, so the store factory/class is the seam,
  per the Task 7 brief). A second, separately-constructed
  ``AILibraryChatService(store=RoutedAiStore(...))`` — matching production,
  where each request builds its own service — then runs the REAL
  ``run_session_turn(trigger="issue_dispatch")`` (buffered/no chunk_callback;
  see the module-level note below on why) against that session id, wrapped
  in the same runner/composer/RunRecorder mocks
  test_task6_run_recorder_store_dispatch.py uses. This proves: session
  creation lands on the store the mode dictates, the FinishIssue tool is
  really injected into the composed prompt, ``trigger`` reaches the
  RunRecorder kwargs unchanged, and the store-kind → session_id/
  conversation_id dispatch (Task 6's contract) also holds for this trigger.

* **Group B (``test_run_issue_agent_*``)** — drives the actual
  ``run_issue_agent()`` entry point with a fake ``AILibraryChatService``
  (same style as the existing ``test_issue_agent_executor.py`` harness),
  parametrized across the three modes, proving the executor's OWN logic
  (nudge/content selection, trigger forwarding, FinishIssue outcome
  extraction via the real ``extract_issue_outcome``) is mode-agnostic — it
  never reads the flag itself, so it must behave identically regardless.

Why Group A calls ``run_session_turn`` directly instead of through
``run_issue_agent()``: ``run_issue_agent`` always passes a ``chunk_callback``,
which routes ``_run_session_turn_inner`` down the STREAMING branch
(``runner.stream_turn``). That branch currently discards structured
``tool_call_delta`` chunks (see ``ai_library_chat_service.py``, the
``if chunk.tool_call_delta: pass`` line) — a pre-existing characteristic of
the streaming path, unrelated to Phase-2 store routing and out of scope to
change here. Calling ``run_session_turn`` without a ``chunk_callback`` takes
the buffered ``runner.run_turn`` branch instead, which is what
test_task6_run_recorder_store_dispatch.py already relies on and is the
faithful way to observe the FinishIssue-injection / RunRecorder-kwargs
contract that Task 6 established and this task must prove still holds for
``trigger="issue_dispatch"``. See the P2 Task 7 report for the full note.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ai.tools.finish_issue_tool import (
    extract_issue_outcome,
    finish_issue_handler,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared plumbing mocks (same boundary as test_task6_run_recorder_store_dispatch)
# ---------------------------------------------------------------------------


class _RunRecorderCM:
    """Async context manager standing in for RunRecorder(...)."""

    def __init__(self, recorder: MagicMock) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> MagicMock:
        return self._recorder

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


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


# ---------------------------------------------------------------------------
# Group A — real RoutedAiStore + real run_session_turn(trigger="issue_dispatch")
# ---------------------------------------------------------------------------


async def _drive_store_routing(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> Dict[str, Any]:
    """Create an issue session under `mode`, then run a real, buffered
    ``run_session_turn(trigger="issue_dispatch")`` turn against it.

    Returns everything the per-mode tests assert on: the two store mocks
    (for call-site assertions), the captured RunRecorder kwargs, the turn
    result, and the composed prompt's tools/handler captured at the moment
    the runner was invoked (before the FinishIssue handler gets cleared in
    the turn's ``finally`` block).
    """
    from app.core.config import settings
    from app.db import engine as db_engine_module
    from app.schemas.ai_library import ComposedSystemPrompt
    from app.services.ai.chat import ai_library_chat_service as chat_service_module
    from app.services.ai.chat.ai_library_chat_service import AILibraryChatService
    from app.services.ai.chat.store_router import RoutedAiStore
    from app.services.issues import issue_session as session_module

    monkeypatch.setattr(settings, "FEATURE_DIRECT_CONVERSATIONS", mode)

    agent_id = uuid4()
    user_id = uuid4()
    issue_id = 4242
    session_id_int = 555555

    fake_legacy = AsyncMock()
    fake_new = AsyncMock()

    legacy_row = {
        "id": session_id_int,
        "user_id": str(user_id),
        "agent_slug": "issue_agent",
        "agent_id": str(agent_id),
        "title": "Ship the thing",
        "total_tokens": 0,
        "message_count": 0,
        "team_id": None,
        "project_id": None,
        "store_kind": "legacy",
    }
    conversations_row = {**legacy_row, "store_kind": "conversations"}

    if mode == "on":
        # Fresh session created directly on the new store; legacy never
        # creates it. The probe inside RoutedAiStore._resolve_with_probe
        # (mode == "on") asks legacy first and must miss.
        fake_new.create_session.return_value = conversations_row
        fake_legacy.get_session.return_value = None
        fake_new.get_session.return_value = conversations_row
        owning_row = conversations_row
    else:
        # off/shadow: legacy is authoritative for the create; shadow ALSO
        # best-effort mirrors into new (never looked up by session_id).
        fake_legacy.create_session.return_value = legacy_row
        fake_new.create_session.return_value = conversations_row
        fake_legacy.get_session.return_value = legacy_row
        owning_row = legacy_row

    owning_store = fake_new if mode == "on" else fake_legacy
    owning_store.get_messages.return_value = []
    owning_store.append_user_message.return_value = {
        "id": "u-1",
        "role": "user",
        "content": "hi",
    }
    owning_store.append_assistant_message.return_value = {
        "id": "a-1",
        "role": "assistant",
        "content": "did the thing",
        "metadata_json": {},
    }
    owning_store.bump_counters.return_value = None

    def _service_factory(*_a: Any, **_kw: Any) -> AILibraryChatService:
        # A fresh RoutedAiStore per construction — matches production (one
        # AILibraryChatService() per call site) — wrapping the SAME
        # underlying fake stores so state persists across call sites
        # exactly like a real DB would.
        return AILibraryChatService(
            store=RoutedAiStore(legacy=fake_legacy, new=fake_new)
        )

    # issue_session.py constructs its own AILibraryChatService() with no
    # injectable store param -- monkeypatch the symbol it resolved into its
    # own namespace (the store-factory seam the Task 7 brief calls out).
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
    # dispatch through RoutedAiStore. ---
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
            "app.services.ai.chat.ai_library_chat_service.get_adapter",
            return_value=MagicMock(),
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
        turn_service = AILibraryChatService(
            store=RoutedAiStore(legacy=fake_legacy, new=fake_new)
        )
        result = await turn_service.run_session_turn(
            session_id,
            user_id=user_id,
            content="Task: Ship the thing",
            trigger="issue_dispatch",
        )

    return {
        "mode": mode,
        "session_id": session_id,
        "session_id_int": session_id_int,
        "fake_legacy": fake_legacy,
        "fake_new": fake_new,
        "owning_row": owning_row,
        "run_recorder_kwargs": captured_kwargs,
        "captured_turn": captured_turn,
        "result": result,
    }


@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_off_shadow_session_and_turn_land_on_legacy(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    out = await _drive_store_routing(monkeypatch, mode)

    # Session creation authoritative store: legacy.
    out["fake_legacy"].create_session.assert_awaited_once()
    if mode == "off":
        out["fake_new"].create_session.assert_not_awaited()
    else:
        # shadow: best-effort mirror into new ALSO happens, but never
        # drives what the caller (issue_session) sees -- session_id above
        # already asserted == the legacy id.
        out["fake_new"].create_session.assert_awaited_once()

    # Per-session ops (get_session/messages/append/bump) never touch the
    # new store in off/shadow -- legacy is authoritative for every read.
    out["fake_legacy"].get_session.assert_awaited()
    out["fake_new"].get_session.assert_not_awaited()
    out["fake_legacy"].append_user_message.assert_awaited_once()
    out["fake_legacy"].append_assistant_message.assert_awaited_once()
    out["fake_new"].append_user_message.assert_not_awaited()
    out["fake_new"].append_assistant_message.assert_not_awaited()

    # trigger reaches RunRecorder kwargs unchanged, and store_kind='legacy'
    # keeps the byte-identical session_id path (Task 6 contract), not
    # conversation_id.
    kwargs = out["run_recorder_kwargs"]
    assert kwargs["trigger"] == "issue_dispatch"
    assert kwargs["session_id"] == out["session_id"]
    assert kwargs["conversation_id"] is None

    # FinishIssue tool actually injected for this trigger.
    tool_names = [t["function"]["name"] for t in out["captured_turn"]["tools"]]
    assert "FinishIssue" in tool_names
    assert out["captured_turn"]["finish_issue_handler"] is finish_issue_handler
    assert "FinishIssue" in out["captured_turn"]["system_message"]

    # Outcome extraction from the returned tool_calls still works.
    outcome, reason = extract_issue_outcome(out["result"].get("tool_calls"))
    assert outcome == "completed"
    assert reason == "shipped"


async def test_on_mode_session_and_turn_land_on_new_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = await _drive_store_routing(monkeypatch, "on")

    # A NEW session in 'on' mode is created directly on the conversations
    # store; legacy never creates it.
    out["fake_new"].create_session.assert_awaited_once()
    out["fake_legacy"].create_session.assert_not_awaited()

    # Per-session ops: the second AILibraryChatService instance (built for
    # the turn) has an empty owner cache, so it must PROBE legacy first
    # (miss) before routing reads to the new store -- exactly the
    # dual-serving behavior test_store_router.py documents.
    out["fake_legacy"].get_session.assert_awaited()
    out["fake_new"].get_session.assert_awaited()
    out["fake_new"].append_user_message.assert_awaited_once()
    out["fake_new"].append_assistant_message.assert_awaited_once()
    out["fake_legacy"].append_user_message.assert_not_awaited()
    out["fake_legacy"].append_assistant_message.assert_not_awaited()

    # trigger reaches RunRecorder kwargs unchanged, and store_kind=
    # 'conversations' links via conversation_id, not session_id (Task 6
    # contract -- agent_runs.session_id FKs ai_sessions, would 23503 on a
    # conversations id).
    kwargs = out["run_recorder_kwargs"]
    assert kwargs["trigger"] == "issue_dispatch"
    assert kwargs["session_id"] is None
    assert kwargs["conversation_id"] == out["session_id_int"]
    assert isinstance(kwargs["conversation_id"], int)

    tool_names = [t["function"]["name"] for t in out["captured_turn"]["tools"]]
    assert "FinishIssue" in tool_names
    assert out["captured_turn"]["finish_issue_handler"] is finish_issue_handler

    outcome, reason = extract_issue_outcome(out["result"].get("tool_calls"))
    assert outcome == "completed"
    assert reason == "shipped"


# ---------------------------------------------------------------------------
# Group B — run_issue_agent() itself, mode-parametrized, fake chat service
# (same seam test_issue_agent_executor.py already uses)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
async def test_run_issue_agent_forwards_trigger_and_extracts_outcome_every_mode(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """run_issue_agent never reads FEATURE_DIRECT_CONVERSATIONS itself -- its
    trigger-forwarding + FinishIssue outcome extraction must behave
    identically in all three modes. Mocks at the AILibraryChatService
    boundary, same as test_issue_agent_executor.py's existing tests."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "FEATURE_DIRECT_CONVERSATIONS", mode)

    from app.services.issues import issue_agent_executor as executor_module

    monkeypatch.setattr(
        executor_module,
        "get_or_create_issue_session",
        AsyncMock(return_value="sess-p2-1"),
    )
    chat_svc = AsyncMock()
    chat_svc.run_session_turn = AsyncMock(
        return_value={
            "assistant_message": {"content": "did it"},
            "tool_calls": _default_tool_calls(),
        }
    )
    monkeypatch.setattr(executor_module, "AILibraryChatService", lambda: chat_svc)
    monkeypatch.setattr(executor_module, "publish_chunk", AsyncMock())
    monkeypatch.setattr(executor_module, "publish_message", AsyncMock())
    monkeypatch.setattr(executor_module, "publish_status", AsyncMock())

    out = await executor_module.run_issue_agent(
        issue={"id": 909, "title": "do the thing", "description": "details"},
        agent_id="agent-x",
        user_id="user-x",
    )

    chat_svc.run_session_turn.assert_awaited_once()
    call_kwargs = chat_svc.run_session_turn.await_args.kwargs
    assert call_kwargs["trigger"] == "issue_dispatch"

    assert out["content"] == "did it"
    assert out["outcome"] == "completed"
    assert out["reason"] == "shipped"
