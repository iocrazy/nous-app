from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory.provider import MemoryLayer, MemoryTurn
from app.services.ai.memory.providers.graphiti_provider import GraphitiProvider


def _turn():
    return MemoryTurn(
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        run_id="r1",
        iteration=0,
        user_msgs=["hi"],
        asst_msgs=["hello"],
    )


@pytest.mark.asyncio
async def test_record_turn_calls_add_chat_episode_with_exact_args():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=True),
        add_chat_episode=AsyncMock(return_value=True),
    )
    prov = GraphitiProvider()
    assert prov.name == "graphiti"
    assert prov.layer == MemoryLayer.L3
    assert prov.enabled() is False  # no service bound yet → reads via factory

    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        assert prov.enabled() is True
        ok = await prov.record_turn(_turn())

    assert ok is True
    svc.add_chat_episode.assert_awaited_once_with(
        group_id="user-u1",
        name="chat-s1-r1",
        body=svc.add_chat_episode.await_args.kwargs["body"],  # body built internally
        source_description="mediahub chat turn",
    )
    assert svc.add_chat_episode.await_args.kwargs["body"]  # non-empty


@pytest.mark.asyncio
async def test_record_turn_returns_false_when_disabled():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=False), add_chat_episode=AsyncMock()
    )
    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        ok = await GraphitiProvider().record_turn(_turn())
    assert ok is False
    svc.add_chat_episode.assert_not_awaited()


@pytest.mark.asyncio
async def test_reload_clears_cached_config_and_health_uses_is_enabled():
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=True), is_enabled=AsyncMock(return_value=True)
    )
    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        prov = GraphitiProvider()
        await prov.reload()
        assert svc.config is None  # next call re-reads from_settings
        assert await prov.health() is True
