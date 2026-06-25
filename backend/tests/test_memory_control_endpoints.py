"""Admin memory control-plane endpoints (Phase 2a)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _provider(name: str, healthy: bool):
    p = MagicMock()
    p.name = name
    p.health = AsyncMock(return_value=healthy)
    p.reload = AsyncMock()
    return p


@pytest.mark.asyncio
async def test_get_memory_control_lists_slots_with_health():
    from app.api.admin.settings_router import get_memory_control

    l2, l3 = _provider("honcho", True), _provider("graphiti", False)
    with (
        patch(
            "app.api.admin.settings_router.memory_registry.l2_provider",
            new=AsyncMock(return_value=l2),
        ),
        patch(
            "app.api.admin.settings_router.memory_registry.l3_provider",
            new=AsyncMock(return_value=l3),
        ),
    ):
        resp = await get_memory_control(MagicMock())

    by_slot = {s.slot: s for s in resp.slots}
    assert by_slot["l2"].provider == "honcho" and by_slot["l2"].health is True
    assert by_slot["l3"].provider == "graphiti" and by_slot["l3"].health is False


@pytest.mark.asyncio
async def test_get_memory_control_reports_none_when_slot_disabled():
    from app.api.admin.settings_router import get_memory_control

    with (
        patch(
            "app.api.admin.settings_router.memory_registry.l2_provider",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.admin.settings_router.memory_registry.l3_provider",
            new=AsyncMock(return_value=_provider("graphiti", True)),
        ),
    ):
        resp = await get_memory_control(MagicMock())

    by_slot = {s.slot: s for s in resp.slots}
    assert by_slot["l2"].provider == "none" and by_slot["l2"].health is False


@pytest.mark.asyncio
async def test_set_memory_slot_upserts_and_rejects_invalid():
    from fastapi import HTTPException

    from app.api.admin.settings_router import set_memory_slot
    from app.schemas.admin import MemorySlotUpdate

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(return_value={})
    auth = MagicMock()
    auth.user_id = "admin-1"

    with (
        patch(
            "app.api.admin.settings_router.get_system_settings_repository",
            return_value=repo,
        ),
        patch(
            "app.api.admin.settings_router.memory_registry.l2_provider",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.api.admin.settings_router.memory_registry.l3_provider",
            new=AsyncMock(return_value=_provider("graphiti", True)),
        ),
    ):
        # valid: l2 → none
        await set_memory_slot(MemorySlotUpdate(slot="l2", provider="none"), auth)
        repo.upsert_setting.assert_awaited_once_with(
            "memory.l2_provider", "none", "admin-1"
        )
        # invalid: l2 → graphiti (wrong layer)
        with pytest.raises(HTTPException) as exc:
            await set_memory_slot(
                MemorySlotUpdate(slot="l2", provider="graphiti"), auth
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_reload_memory_slot_calls_provider_reload():
    from app.api.admin.settings_router import reload_memory_slot

    l3 = _provider("graphiti", True)
    with patch(
        "app.api.admin.settings_router.memory_registry.l3_provider",
        new=AsyncMock(return_value=l3),
    ):
        resp = await reload_memory_slot("l3", MagicMock())

    assert resp.ok is True and resp.reloaded == "graphiti"
    l3.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reload_rejects_bad_slot_and_handles_none():
    from fastapi import HTTPException

    from app.api.admin.settings_router import reload_memory_slot

    with pytest.raises(HTTPException) as exc:
        await reload_memory_slot("l9", MagicMock())
    assert exc.value.status_code == 400

    with patch(
        "app.api.admin.settings_router.memory_registry.l2_provider",
        new=AsyncMock(return_value=None),
    ):
        resp = await reload_memory_slot("l2", MagicMock())
    assert resp.ok is False and resp.reloaded is None
