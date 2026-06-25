from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

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
        body=ANY,
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
    # graphiti=object() simulates an already-connected client (non-None).
    # _config_loaded=True simulates config already loaded from settings.
    # reload() must reset BOTH so _ensure_config rebuilds on next async op.
    svc = SimpleNamespace(
        config=SimpleNamespace(enabled=True),
        graphiti=object(),
        _config_loaded=True,
        is_enabled=AsyncMock(return_value=True),
    )
    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        prov = GraphitiProvider()
        await prov.reload()
        assert svc.graphiti is None  # cached client dropped → rebuild triggered
        assert svc._config_loaded is False  # config re-read gate cleared
        assert await prov.health() is True


@pytest.mark.asyncio
async def test_get_context_renders_facts():
    fact1 = SimpleNamespace(fact="Alice prefers dark mode")
    fact2 = SimpleNamespace(fact="Bob uses VIM")
    svc = SimpleNamespace(
        search=AsyncMock(return_value=[fact1, fact2]),
    )
    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        result = await GraphitiProvider().get_context(
            user_id="u1", query="preferences", group_ids=["user-u1"]
        )
    assert result == "Alice prefers dark mode\nBob uses VIM"


@pytest.mark.asyncio
async def test_is_operative_delegates_to_is_enabled():
    svc = SimpleNamespace(is_enabled=AsyncMock(return_value=True))
    with patch(
        "app.services.ai.memory.providers.graphiti_provider.get_graph_memory_service",
        return_value=svc,
    ):
        result = await GraphitiProvider().is_operative()
    assert result is True
