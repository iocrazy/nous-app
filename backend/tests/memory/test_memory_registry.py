# backend/tests/memory/test_memory_registry.py
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.memory import registry
from app.services.ai.memory.providers.graphiti_provider import GraphitiProvider
from app.services.ai.memory.providers.honcho_provider import HonchoProvider


@pytest.mark.asyncio
async def test_defaults_select_current_providers():
    # No row → default honcho / graphiti (zero behavior change).
    with patch.object(
        registry, "_read_provider_setting", new=AsyncMock(side_effect=lambda k, d: d)
    ):
        l2 = await registry.l2_provider()
        l3 = await registry.l3_provider()
    assert isinstance(l2, HonchoProvider)
    assert isinstance(l3, GraphitiProvider)


@pytest.mark.asyncio
async def test_none_disables_slot():
    with patch.object(
        registry, "_read_provider_setting", new=AsyncMock(return_value="none")
    ):
        assert await registry.l2_provider() is None
        assert await registry.l3_provider() is None


@pytest.mark.asyncio
async def test_unknown_value_falls_back_to_current_provider():
    with patch.object(
        registry, "_read_provider_setting", new=AsyncMock(return_value="mem0")
    ):
        # Mem0 not implemented in Phase 1 → safe fallback to current default.
        assert isinstance(await registry.l2_provider(), HonchoProvider)
