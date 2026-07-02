"""Unit tests for run_conversation_agent_turn (Unified Conversations Phase 1, Task 6).

Required assertions per brief:
  (a) _build_history maps an agent-sender message to role=assistant and a user
      message to role=user, and renders a type='text' body to its text.
  (b) run_conversation_agent_turn returns None when the agent is not found
      (gate 1 — mock get_agent_repository().get_by_slug / get_by_id → None).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ─── Constants ────────────────────────────────────────────────────────────────

CONVERSATION: dict = {"id": 100, "scope_id": 7}
SUMMONER: str = str(uuid4())
AGENT_SLUG: str = "script_ai"
AGENT_ID = uuid4()

_MOD = "app.services.chat.conversation_agent_turn"


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_agent() -> dict:
    """Minimal ai_agents row with chat enabled for any team."""
    return {
        "id": str(AGENT_ID),
        "slug": AGENT_SLUG,
        "model": "qwen-max",
        "capability_profile": {
            "chat": {
                "enabled": True,
                "read_team_resources": True,
                "auto_broadcast": False,
                "allowed_team_ids": [],
            }
        },
    }


def _make_caps(
    *,
    enabled: bool = True,
    read: bool = True,
    team_ids: tuple = (),
) -> object:
    from app.services.ai.permissions.agent_chat_caps import ChatCaps

    return ChatCaps(
        enabled=enabled,
        read_team_resources=read,
        auto_broadcast=False,
        allowed_team_ids=team_ids,
    )


def _make_runner() -> MagicMock:
    runner = MagicMock()
    runner.resource_fetch_handler = None
    return runner


def _make_stack(runner: MagicMock) -> MagicMock:
    stack = MagicMock()
    stack.runner = runner
    stack.graph_facts = []
    stack.user_context = None
    return stack


def _make_composed() -> MagicMock:
    composed = MagicMock()
    composed.agent_id = AGENT_ID
    composed.model = "qwen-max"
    composed.tools = []

    def _model_copy(**kw: object) -> MagicMock:
        update = kw.get("update", {})
        if "tools" in update:
            composed.tools = update["tools"]
        return composed

    composed.model_copy = _model_copy
    return composed


def _make_recorder_cm() -> MagicMock:
    recorder = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=recorder)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_composer(composed: MagicMock) -> MagicMock:
    instance = MagicMock()
    instance.compose = AsyncMock(return_value=composed)
    cls = MagicMock(return_value=instance)
    return cls


def _make_composer_capturing(composed: MagicMock, captured: dict) -> MagicMock:
    """Like _make_composer, but records the ComposerInput passed to compose()."""

    async def _compose(composer_input: object) -> MagicMock:
        captured["input"] = composer_input
        return composed

    instance = MagicMock()
    instance.compose = _compose
    cls = MagicMock(return_value=instance)
    return cls


# ─── Test (a): _build_history maps roles and renders text body ────────────────


@pytest.mark.unit
def test_build_history_maps_agent_to_assistant() -> None:
    """_build_history: agent sender_type → role=assistant; user → role=user.

    Also verifies that type='text' body is rendered to the text field value.
    """
    from app.services.chat.conversation_agent_turn import _build_history

    msgs = [
        {
            "sender_type": "agent",
            "type": "text",
            "body": {"text": "Hello from agent"},
        },
        {
            "sender_type": "user",
            "type": "text",
            "body": {"text": "Hi there"},
        },
    ]
    result = _build_history(msgs)
    assert len(result) == 2
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == "Hello from agent"
    assert result[1]["role"] == "user"
    assert result[1]["content"] == "Hi there"


@pytest.mark.unit
def test_build_history_user_sender_type_maps_to_user() -> None:
    """_build_history: any non-agent sender_type maps to role=user."""
    from app.services.chat.conversation_agent_turn import _build_history

    msgs = [
        {
            "sender_type": "user",
            "type": "text",
            "body": {"text": "plain user message"},
        },
    ]
    result = _build_history(msgs)
    assert result[0]["role"] == "user"
    assert result[0]["content"] == "plain user message"


@pytest.mark.unit
def test_build_history_empty_returns_empty() -> None:
    """_build_history: empty input → empty output."""
    from app.services.chat.conversation_agent_turn import _build_history

    assert _build_history([]) == []


# ─── Test (b): Gate 1 — agent not found → None ───────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_not_found_returns_none() -> None:
    """Gate 1: get_by_slug returns None → run_conversation_agent_turn returns None.

    The runtime stack must NOT be built.
    """
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=None)

    with (
        patch(f"{_MOD}.get_agent_repository", return_value=mock_repo),
        patch(f"{_MOD}.build_agent_runner_stack") as mock_stack_fn,
    ):
        from app.services.chat.conversation_agent_turn import (
            run_conversation_agent_turn,
        )

        result = await run_conversation_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            conversation=CONVERSATION,
        )

    assert result is None
    mock_stack_fn.assert_not_called()


# ─── Test: Gate 0 — null scope_id → None ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_null_scope_id_returns_none() -> None:
    """Gate 0: conversation['scope_id'] is None → returns None before agent lookup."""
    with patch(f"{_MOD}.get_agent_repository") as mock_repo_fn:
        from app.services.chat.conversation_agent_turn import (
            run_conversation_agent_turn,
        )

        result = await run_conversation_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            conversation={"id": 100, "scope_id": None},
        )

    assert result is None
    mock_repo_fn.assert_not_called()


# ─── Test: Gate 2 — caps disabled → None ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_caps_disabled_returns_none() -> None:
    """Gate 2a: caps.enabled=False → None; runner is NOT invoked."""
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=_make_agent())

    with (
        patch(f"{_MOD}.get_agent_repository", return_value=mock_repo),
        patch(f"{_MOD}.agent_chat_caps", return_value=_make_caps(enabled=False)),
        patch(f"{_MOD}.build_agent_runner_stack") as mock_stack_fn,
    ):
        from app.services.chat.conversation_agent_turn import (
            run_conversation_agent_turn,
        )

        result = await run_conversation_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            conversation=CONVERSATION,
        )

    assert result is None
    mock_stack_fn.assert_not_called()


# ─── Test: history uses `type` field (renamed from content_type) ──────────────


@pytest.mark.unit
def test_render_body_reads_type_field() -> None:
    """_render_body reads the `type` param (renamed from content_type in new schema)."""
    from app.services.chat.conversation_agent_turn import _render_body

    assert _render_body({"text": "hello"}, "text") == "hello"
    assert _render_body({"title": "My Video"}, "media_card") == "[media card: My Video]"
    assert _render_body({"title": "Task X"}, "task_card") == "[task card: Task X]"
    assert _render_body({"name": "Named"}, "media_card") == "[media card: Named]"


# ─── Test: happy path returns content ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_happy_path_returns_content() -> None:
    """Happy path: run_conversation_agent_turn returns the agent reply string."""
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=_make_agent())

    mock_conv_repo = MagicMock()
    mock_conv_repo.recent_messages = AsyncMock(
        return_value=[
            {
                "sender_type": "user",
                "type": "text",
                "body": {"text": "hello @script_ai"},
            }
        ]
    )

    runner = _make_runner()

    async def _fake_run_turn(
        composed: object,
        *,
        user_messages: object,
        recorder: object,
    ) -> dict:
        return {"content": "hello from agent"}

    runner.run_turn = _fake_run_turn

    fake_stack = _make_stack(runner)
    fake_composed = _make_composed()

    with (
        patch(f"{_MOD}.get_agent_repository", return_value=mock_repo),
        patch(f"{_MOD}.agent_chat_caps", return_value=_make_caps()),
        patch(f"{_MOD}.get_conversation_repository", return_value=mock_conv_repo),
        patch(f"{_MOD}.get_skill_repository", return_value=MagicMock()),
        patch(
            f"{_MOD}.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(f"{_MOD}.PromptComposer", _make_composer(fake_composed)),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
        patch(f"{_MOD}.provider_key_for_model", return_value="qwen"),
    ):
        from app.services.chat.conversation_agent_turn import (
            run_conversation_agent_turn,
        )

        result = await run_conversation_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            conversation=CONVERSATION,
        )

    assert result == "hello from agent"


# ─── Test: memory block injection (Phase 1.5, Task 5) ─────────────────────────


async def _run_happy_path_capturing_composer_input(*, memory_block_return: str) -> dict:
    """Shared harness: run the happy path, patching build_memory_block, and
    return the dict holding the ComposerInput passed to compose()."""
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=_make_agent())

    mock_conv_repo = MagicMock()
    mock_conv_repo.recent_messages = AsyncMock(
        return_value=[
            {
                "sender_type": "user",
                "type": "text",
                "body": {"text": "hello @script_ai"},
            }
        ]
    )

    runner = _make_runner()

    async def _fake_run_turn(
        composed: object,
        *,
        user_messages: object,
        recorder: object,
    ) -> dict:
        return {"content": "hello from agent"}

    runner.run_turn = _fake_run_turn

    fake_stack = _make_stack(runner)
    fake_composed = _make_composed()
    captured: dict = {}

    with (
        patch(f"{_MOD}.get_agent_repository", return_value=mock_repo),
        patch(f"{_MOD}.agent_chat_caps", return_value=_make_caps()),
        patch(f"{_MOD}.get_conversation_repository", return_value=mock_conv_repo),
        patch(f"{_MOD}.get_skill_repository", return_value=MagicMock()),
        patch(
            f"{_MOD}.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(
            f"{_MOD}.build_memory_block",
            AsyncMock(return_value=memory_block_return),
        ),
        patch(
            f"{_MOD}.PromptComposer",
            _make_composer_capturing(fake_composed, captured),
        ),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
        patch(f"{_MOD}.provider_key_for_model", return_value="qwen"),
    ):
        from app.services.chat.conversation_agent_turn import (
            run_conversation_agent_turn,
        )

        result = await run_conversation_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            conversation=CONVERSATION,
        )

    assert result == "hello from agent"
    return captured


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_block_appended_to_request_instructions() -> None:
    """When build_memory_block returns text, compose() receives
    untrusted-guard + blank line + block."""
    from app.services.chat.conversation_agent_turn import (
        _UNTRUSTED_CHANNEL_INSTRUCTION,
    )

    captured = await _run_happy_path_capturing_composer_input(
        memory_block_return="## Conversation summary (older messages)\nS"
    )

    composer_input = captured["input"]
    assert composer_input.request_instructions.startswith(
        _UNTRUSTED_CHANNEL_INSTRUCTION
    )
    assert (
        "## Conversation summary (older messages)"
        in composer_input.request_instructions
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_memory_block_leaves_instructions_unchanged() -> None:
    """When build_memory_block returns '', request_instructions is exactly the
    untrusted-channel guard (unchanged)."""
    from app.services.chat.conversation_agent_turn import (
        _UNTRUSTED_CHANNEL_INSTRUCTION,
    )

    captured = await _run_happy_path_capturing_composer_input(memory_block_return="")

    composer_input = captured["input"]
    assert composer_input.request_instructions == _UNTRUSTED_CHANNEL_INSTRUCTION
