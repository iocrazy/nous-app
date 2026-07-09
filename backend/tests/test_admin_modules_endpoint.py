"""Task 3 — unified `/modules` admin endpoint tests.

Tests for ``GET /admin/settings/modules`` and
``PUT /admin/settings/modules/{module_id}`` in
``app.api.admin.settings_router``.

Follows the direct-function-call pattern used by
``test_admin_ai_governance_settings.py`` (there is no ``admin_client``
TestClient fixture in this codebase) — call the router functions directly,
patching their collaborators.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_get_modules_lists_both():
    """With no stored rows, GET /modules returns both registered modules with
    their own registry defaults (topic fails open, distribution fails closed)."""
    from app.api.admin.settings_router import get_modules
    from app.schemas.admin import ModuleSummaryResponse

    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    with patch(
        "app.services.modules.registry._read_raw", new=AsyncMock(return_value=None)
    ):
        result = await get_modules(fake_auth)

    ids = {m.id for m in result}
    assert ids == {"topic-inspiration", "distribution"}
    for m in result:
        assert isinstance(m, ModuleSummaryResponse)
        assert set(m.model_dump().keys()) == {
            "id",
            "key",
            "label",
            "enabled",
            "visible",
            "enabled_default",
            "visible_default",
        }

    topic = next(m for m in result if m.id == "topic-inspiration")
    assert topic.enabled_default is True
    assert topic.visible_default is True
    assert topic.enabled is True
    assert topic.visible is True

    dist = next(m for m in result if m.id == "distribution")
    assert dist.enabled_default is False
    assert dist.visible_default is False
    assert dist.enabled is False
    assert dist.visible is False


@pytest.mark.asyncio
async def test_put_module_persists_and_returns_summary():
    """PUT persists both switches via write_module_state (which upserts through
    the system_settings repo) and returns the refreshed summary; the audit log
    is written with the module's key."""
    from app.api.admin.settings_router import update_module
    from app.schemas.admin import ModuleSwitchUpdate

    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    repo = MagicMock()
    repo.upsert_setting = AsyncMock(
        side_effect=lambda key, value, updated_by: {"key": key, "value": value}
    )

    with patch(
        "app.repositories.admin.system_settings_repository.get_system_settings_repository",
        return_value=repo,
    ):
        with patch(
            "app.api.admin.settings_router.create_audit_log", new=AsyncMock()
        ) as audit_mock:
            result = await update_module(
                "distribution",
                ModuleSwitchUpdate(enabled=True, visible=True),
                fake_auth,
            )

    assert result.id == "distribution"
    assert result.enabled is True
    assert result.visible is True

    repo.upsert_setting.assert_called_once_with(
        "distribution.module", {"enabled": True, "visible": True}, "admin-1"
    )
    audit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_put_unknown_module_id_404():
    """An unknown module id 404s before ever touching the repo/audit log."""
    from app.api.admin.settings_router import update_module
    from app.schemas.admin import ModuleSwitchUpdate

    fake_auth = MagicMock()
    fake_auth.user_id = "admin-1"

    with pytest.raises(HTTPException) as exc:
        await update_module(
            "does-not-exist",
            ModuleSwitchUpdate(enabled=True, visible=True),
            fake_auth,
        )

    assert exc.value.status_code == 404
