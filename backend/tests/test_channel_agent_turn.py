"""Unit tests for run_channel_agent_turn (Team Chat PHASE-2, Task 4).

Tests verify gate ordering, security enforcement (iron law), and correct
scoping of resource_fetch calls.  Heavy runtime deps are mocked so the suite
is fast and does not need a real DB or LLM provider.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ─── Fixtures / builders ───────────────────────────────────────────────────────

CHANNEL: dict = {"id": 42, "team_id": 7}
SUMMONER: str = str(uuid4())  # must be a valid UUID string (UUID() is called)
AGENT_SLUG: str = "script_ai"
AGENT_ID = uuid4()


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
    """Build a ChatCaps instance directly (bypass agent dict parsing)."""
    from app.services.ai.permissions.agent_chat_caps import ChatCaps

    return ChatCaps(
        enabled=enabled,
        read_team_resources=read,
        auto_broadcast=False,
        allowed_team_ids=team_ids,
    )


def _make_runner() -> MagicMock:
    """AgentRunner mock — run_turn is replaced per test."""
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
    """ComposedSystemPrompt mock with a list tools attribute."""
    composed = MagicMock()
    composed.agent_id = AGENT_ID
    composed.model = "qwen-max"
    composed.tools = []

    # model_copy must return an object with the same agent_id / model so that
    # UUID(summoner_user_id) calls inside RunRecorder still see a UUID.
    composed.model_copy = lambda **_kw: composed
    return composed


def _make_recorder_cm() -> MagicMock:
    """Async context manager that mimics RunRecorder."""
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


# ─── Patch targets (all module-level imports in channel_agent_turn) ────────────

_MOD = "app.services.chat.channel_agent_turn"


# ─── Test 1: agent not found → None ───────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_not_found_returns_none() -> None:
    """Gate 1: get_by_slug returns None → function returns None immediately.

    The runtime stack must NOT be built.
    """
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=None)

    with (
        patch(f"{_MOD}.get_agent_repository", return_value=mock_repo),
        patch(f"{_MOD}.build_agent_runner_stack") as mock_stack_fn,
    ):
        from app.services.chat.channel_agent_turn import run_channel_agent_turn

        result = await run_channel_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            channel=CHANNEL,
        )

    assert result is None
    mock_stack_fn.assert_not_called()


# ─── Test 2: caps.enabled=False → None ────────────────────────────────────────


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
        from app.services.chat.channel_agent_turn import run_channel_agent_turn

        result = await run_channel_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            channel=CHANNEL,
        )

    assert result is None
    mock_stack_fn.assert_not_called()


# ─── Test 3: allows_team=False → None ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_caps_wrong_team_returns_none() -> None:
    """Gate 2b: allowed_team_ids restricts to team 999; channel.team_id=7 → None."""
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=_make_agent())

    # team_ids=(999,) → only team 999 allowed; CHANNEL["team_id"]=7 is blocked.
    restricted_caps = _make_caps(enabled=True, team_ids=(999,))

    with (
        patch(f"{_MOD}.get_agent_repository", return_value=mock_repo),
        patch(f"{_MOD}.agent_chat_caps", return_value=restricted_caps),
        patch(f"{_MOD}.build_agent_runner_stack") as mock_stack_fn,
    ):
        from app.services.chat.channel_agent_turn import run_channel_agent_turn

        result = await run_channel_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            channel=CHANNEL,
        )

    assert result is None
    mock_stack_fn.assert_not_called()


# ─── Test 4: happy path — returns content, handler passes correct scope ────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_happy_path_returns_content_and_handler_scoped() -> None:
    """Happy path: returns "hello from agent".

    Verifies that the resource_fetch_handler bound during the turn passes
    user_id=SUMMONER and team_id=CHANNEL["team_id"] (the iron law).
    The handler is invoked INSIDE mock run_turn so the patch context is active.
    """
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=_make_agent())

    mock_chat_repo = MagicMock()
    mock_chat_repo.recent_messages = AsyncMock(
        return_value=[
            {
                "sender_type": "user",
                "content_type": "text",
                "body": {"text": "hello @script_ai"},
            }
        ]
    )

    runner = _make_runner()
    resource_fetch_calls: list[dict] = []

    async def _fake_resource_fetch(**kwargs: object) -> dict:
        resource_fetch_calls.append(dict(kwargs))
        return {"content": "file content"}

    async def _fake_run_turn(
        composed: object,
        *,
        user_messages: object,
        recorder: object,
    ) -> dict:
        # Invoke handler now while all patches are still active.
        handler = runner.resource_fetch_handler
        assert handler is not None, "resource_fetch_handler must be set before run_turn"
        await handler({"resource_id": "res-123"})
        return {"content": "hello from agent"}

    runner.run_turn = _fake_run_turn

    fake_stack = _make_stack(runner)
    fake_composed = _make_composed()

    with (
        patch(f"{_MOD}.get_agent_repository", return_value=mock_repo),
        patch(f"{_MOD}.agent_chat_caps", return_value=_make_caps()),
        patch(f"{_MOD}.get_chat_repository", return_value=mock_chat_repo),
        patch(f"{_MOD}.get_skill_repository", return_value=MagicMock()),
        patch(
            f"{_MOD}.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(f"{_MOD}.PromptComposer", _make_composer(fake_composed)),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
        patch(f"{_MOD}.provider_key_for_model", return_value="qwen"),
        patch(
            "app.services.ai.tools.resource_fetch_tool.resource_fetch",
            _fake_resource_fetch,
        ),
    ):
        from app.services.chat.channel_agent_turn import run_channel_agent_turn

        result = await run_channel_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            channel=CHANNEL,
        )

    assert result == "hello from agent"
    assert len(resource_fetch_calls) == 1, "resource_fetch must be called exactly once"
    call = resource_fetch_calls[0]
    assert (
        call["user_id"] == SUMMONER
    ), "user_id must be the summoner (not service_role)"
    assert call["team_id"] == CHANNEL["team_id"], "team_id must be the channel team"


# ─── Test 5: read_team_resources=False → handler refuses, resource_fetch not called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_read_team_resources_handler_refuses() -> None:
    """CHAT-PERM-10: read_team_resources=False → handler returns error dict.

    resource_fetch must NOT be called at all.
    """
    mock_repo = MagicMock()
    mock_repo.get_by_slug = AsyncMock(return_value=_make_agent())

    mock_chat_repo = MagicMock()
    mock_chat_repo.recent_messages = AsyncMock(return_value=[])

    runner = _make_runner()
    resource_fetch_calls: list[dict] = []
    handler_results: list[dict] = []

    async def _tracking_resource_fetch(**kwargs: object) -> dict:
        resource_fetch_calls.append(dict(kwargs))
        return {"content": "should never be returned"}

    async def _fake_run_turn(
        composed: object,
        *,
        user_messages: object,
        recorder: object,
    ) -> dict:
        handler = runner.resource_fetch_handler
        assert handler is not None
        # Call the handler — it must refuse without touching resource_fetch.
        res = await handler({"resource_id": "res-999"})
        handler_results.append(res)
        return {"content": "reply"}

    runner.run_turn = _fake_run_turn

    fake_stack = _make_stack(runner)
    fake_composed = _make_composed()
    no_read_caps = _make_caps(enabled=True, read=False)

    with (
        patch(f"{_MOD}.get_agent_repository", return_value=mock_repo),
        patch(f"{_MOD}.agent_chat_caps", return_value=no_read_caps),
        patch(f"{_MOD}.get_chat_repository", return_value=mock_chat_repo),
        patch(f"{_MOD}.get_skill_repository", return_value=MagicMock()),
        patch(
            f"{_MOD}.build_agent_runner_stack",
            AsyncMock(return_value=fake_stack),
        ),
        patch(f"{_MOD}.PromptComposer", _make_composer(fake_composed)),
        patch(f"{_MOD}.RunRecorder", return_value=_make_recorder_cm()),
        patch(f"{_MOD}.provider_key_for_model", return_value="qwen"),
        patch(
            "app.services.ai.tools.resource_fetch_tool.resource_fetch",
            _tracking_resource_fetch,
        ),
    ):
        from app.services.chat.channel_agent_turn import run_channel_agent_turn

        result = await run_channel_agent_turn(
            agent_slug=AGENT_SLUG,
            summoner_user_id=SUMMONER,
            channel=CHANNEL,
        )

    assert result == "reply"
    assert handler_results == [
        {"error": "this agent is not permitted to read team files"}
    ], "handler must refuse with the canonical error message"
    assert (
        resource_fetch_calls == []
    ), "resource_fetch must NOT be called when read_team_resources=False"
