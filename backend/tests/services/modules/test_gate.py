"""Tests for the module gate dependency factory (spec 2026-08-03 §2.1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_gate_disabled_raises_typed_503():
    from app.services.modules.gate import require_module

    check = require_module("shares")
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": False, "visible": False}),
    ):
        with pytest.raises(HTTPException) as exc:
            await check()
    assert exc.value.status_code == 503
    assert exc.value.detail == {"code": "MODULE_DISABLED", "module": "shares"}


@pytest.mark.asyncio
async def test_gate_enabled_passes():
    from app.services.modules.gate import require_module

    check = require_module("shares")
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value={"enabled": True, "visible": True}),
    ):
        assert await check() is None


@pytest.mark.asyncio
async def test_gate_fails_open_on_missing_config():
    """No stored row (raw=None) → default-ON module passes (fail-open)."""
    from app.services.modules.gate import require_module

    check = require_module("todolist")
    with patch(
        "app.services.modules.registry._read_raw",
        new=AsyncMock(return_value=None),
    ):
        assert await check() is None


def test_gate_unknown_module_raises_at_factory_time():
    from app.services.modules.gate import require_module

    with pytest.raises(KeyError):
        require_module("no-such-module")
