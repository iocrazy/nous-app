"""unified_storage_enabled() — the admin-DB switch for the unified WRITE path.

The Module Control Center owns the real control (`storage.unified_storage`,
default OFF, fail-CLOSED); the FEATURE_UNIFIED_STORAGE env setting is only a
dev/test short-circuit that forces the unified track without a DB read.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.library.storage_flag import unified_storage_enabled

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_env_short_circuit_skips_db(monkeypatch):
    from app.services.library import storage_flag

    monkeypatch.setattr(storage_flag.settings, "FEATURE_UNIFIED_STORAGE", True)
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(side_effect=AssertionError("DB must not be consulted")),
    ):
        assert await unified_storage_enabled() is True


async def test_admin_switch_on(monkeypatch):
    from app.services.library import storage_flag

    monkeypatch.setattr(storage_flag.settings, "FEATURE_UNIFIED_STORAGE", False)
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": True, "visible": False}),
    ):
        assert await unified_storage_enabled() is True


async def test_default_off_when_unset(monkeypatch):
    from app.services.library import storage_flag

    monkeypatch.setattr(storage_flag.settings, "FEATURE_UNIFIED_STORAGE", False)
    with patch(
        "app.services.modules.registry._read_raw", new=AsyncMock(return_value=None)
    ):
        assert await unified_storage_enabled() is False


async def test_fail_closed_on_read_error(monkeypatch):
    """registry._read_raw never raises (it catches internally), but garbage
    blobs must still parse to the module default: OFF."""
    from app.services.library import storage_flag

    monkeypatch.setattr(storage_flag.settings, "FEATURE_UNIFIED_STORAGE", False)
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value="{not-json"),
    ):
        assert await unified_storage_enabled() is False
