# backend/tests/memory/test_write_memory_routing.py
from unittest.mock import AsyncMock, patch

import pytest

from app.workflows import write_memory


@pytest.mark.asyncio
async def test_graph_write_routes_through_l3_provider():
    provider = AsyncMock()
    provider.record_turn = AsyncMock(return_value=True)
    provider.enabled = lambda: True

    with (
        patch(
            "app.workflows.write_memory.memory_registry.l3_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "app.workflows.write_memory.get_memory_prefs",
            new=AsyncMock(return_value=type("P", (), {"learn": True})()),
        ),
    ):
        ok = await write_memory._write_graph_episode(
            user_id="u1",
            session_id="s1",
            run_id="r1",
            iteration=0,
            user_msgs=["hi"],
            asst_msgs=["hello"],
        )

    assert ok is True
    turn = provider.record_turn.await_args.args[0]
    assert turn.user_id == "u1" and turn.user_msgs == ["hi"]


@pytest.mark.asyncio
async def test_graph_write_skips_prefs_read_when_provider_disabled():
    provider = type("P", (), {"enabled": lambda self: False})()
    prefs_reader = AsyncMock()
    with (
        patch(
            "app.workflows.write_memory.memory_registry.l3_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch("app.workflows.write_memory.get_memory_prefs", new=prefs_reader),
    ):
        ok = await write_memory._write_graph_episode(
            user_id="u1",
            session_id="s1",
            run_id="r1",
            iteration=0,
            user_msgs=["hi"],
            asst_msgs=["hello"],
        )
    assert ok is False
    prefs_reader.assert_not_awaited()  # gate order: disabled → no settings read


@pytest.mark.asyncio
async def test_honcho_write_routes_through_l2_provider():
    provider = AsyncMock()
    provider.record_turn = AsyncMock(return_value=True)
    provider.enabled = lambda: True

    with (
        patch(
            "app.workflows.write_memory.memory_registry.l2_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch(
            "app.workflows.write_memory.get_memory_prefs",
            new=AsyncMock(return_value=type("P", (), {"learn": True})()),
        ),
    ):
        ok = await write_memory._write_honcho_turn(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            user_msgs=["hi"],
            asst_msgs=["hello"],
        )

    assert ok is True
    turn = provider.record_turn.await_args.args[0]
    assert turn.user_id == "u1" and turn.user_msgs == ["hi"]


@pytest.mark.asyncio
async def test_honcho_write_skips_prefs_read_when_provider_disabled():
    provider = type("P", (), {"enabled": lambda self: False})()
    prefs_reader = AsyncMock()
    with (
        patch(
            "app.workflows.write_memory.memory_registry.l2_provider",
            new=AsyncMock(return_value=provider),
        ),
        patch("app.workflows.write_memory.get_memory_prefs", new=prefs_reader),
    ):
        ok = await write_memory._write_honcho_turn(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            user_msgs=["hi"],
            asst_msgs=["hello"],
        )
    assert ok is False
    prefs_reader.assert_not_awaited()  # gate order: disabled → no settings read


@pytest.mark.asyncio
async def test_honcho_write_returns_false_when_slot_disabled_none():
    prefs_reader = AsyncMock()
    with (
        patch(
            "app.workflows.write_memory.memory_registry.l2_provider",
            new=AsyncMock(return_value=None),
        ),
        patch("app.workflows.write_memory.get_memory_prefs", new=prefs_reader),
    ):
        ok = await write_memory._write_honcho_turn(
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            user_msgs=["hi"],
            asst_msgs=["hello"],
        )
    assert ok is False
    prefs_reader.assert_not_awaited()
