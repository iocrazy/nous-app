# backend/tests/memory/test_honcho_provider.py
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory.provider import MemoryLayer, MemoryTurn
from app.services.ai.memory.providers.honcho_provider import HonchoProvider


def _turn():
    return MemoryTurn(
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        run_id="r1",
        iteration=0,
        user_msgs=["hi", "still me"],
        asst_msgs=["hello", "yo"],
    )


@pytest.mark.asyncio
async def test_record_turn_posts_latest_exchange_with_resolved_workspace():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=True),
        add_chat_turn=AsyncMock(return_value=True),
    )
    prov = HonchoProvider()
    assert prov.name == "honcho"
    assert prov.layer == MemoryLayer.L2

    with (
        patch(
            "app.services.ai.memory.providers.honcho_provider.get_honcho_memory_service",
            return_value=svc,
        ),
        patch(
            "app.services.ai.memory.providers.honcho_provider._resolve_team_workspace",
            new=AsyncMock(return_value="team-42"),
        ),
    ):
        ok = await prov.record_turn(_turn())

    assert ok is True
    svc.add_chat_turn.assert_awaited_once_with(
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        user_message="still me",
        assistant_message="yo",
        workspace_id="team-42",
    )


@pytest.mark.asyncio
async def test_get_context_wraps_user_representation():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=True),
        get_user_representation=AsyncMock(return_value="the user likes brevity"),
    )
    with patch(
        "app.services.ai.memory.providers.honcho_provider.get_honcho_memory_service",
        return_value=svc,
    ):
        out = await HonchoProvider().get_context(user_id="u1", workspace_id="team-42")
    assert out == "the user likes brevity"
    svc.get_user_representation.assert_awaited_once_with(
        user_id="u1", workspace_id="team-42"
    )


@pytest.mark.asyncio
async def test_reload_closes_and_clears_client():
    closed = {"v": False}

    class _Client:
        async def aclose(self):
            closed["v"] = True

    svc = SimpleNamespace(config=SimpleNamespace(enabled=True), client=_Client())
    with patch(
        "app.services.ai.memory.providers.honcho_provider.get_honcho_memory_service",
        return_value=svc,
    ):
        await HonchoProvider().reload()
    assert closed["v"] is True
    assert svc.client is None


@pytest.mark.asyncio
async def test_record_turn_returns_false_when_disabled():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=False),
        add_chat_turn=AsyncMock(return_value=True),
    )
    with (
        patch(
            "app.services.ai.memory.providers.honcho_provider.get_honcho_memory_service",
            return_value=svc,
        ),
        patch(
            "app.services.ai.memory.providers.honcho_provider._resolve_team_workspace",
            new=AsyncMock(return_value="team-42"),
        ),
    ):
        ok = await HonchoProvider().record_turn(_turn())

    assert ok is False
    svc.add_chat_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_operative_delegates_to_operative():
    svc = SimpleNamespace(
        config=SimpleNamespace(operative=lambda: True),
    )
    with patch(
        "app.services.ai.memory.providers.honcho_provider.get_honcho_memory_service",
        return_value=svc,
    ):
        result = await HonchoProvider().is_operative()
    assert result is True
