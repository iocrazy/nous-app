"""Tests for the module gate dependency factory (spec 2026-08-03 §2.1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _clear_gate_cache():
    """The gate caches switch reads for 5s — clear between cases so a patched
    ``_read_raw`` is actually consulted instead of a neighbour's leftovers."""
    from app.core.cache import module_gate_cache

    module_gate_cache.clear()
    yield
    module_gate_cache.clear()


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


@pytest.mark.asyncio
async def test_gate_read_is_cached_within_ttl():
    """Router-level gates run on EVERY request to a gated router, before auth —
    unauthenticated traffic must not turn into one system_settings read each."""
    from app.services.modules.gate import require_module

    check = require_module("shares")
    read = AsyncMock(return_value={"enabled": True, "visible": True})
    with patch("app.services.modules.registry._read_raw", new=read):
        await check()
        first_calls = read.await_count
        assert first_calls == 1
        await check()
        assert read.await_count == first_calls


@pytest.mark.asyncio
async def test_gate_cache_is_per_module_key():
    """One module's cached state must not answer for another's."""
    from app.services.modules.gate import require_module

    shares = require_module("shares")
    todolist = require_module("todolist")

    async def _fake_read(key: str):
        return {"enabled": key != "shares.module", "visible": True}

    with patch("app.services.modules.registry._read_raw", new=_fake_read):
        with pytest.raises(HTTPException):
            await shares()
        assert await todolist() is None


def test_gate_unknown_module_raises_at_factory_time():
    from app.services.modules.gate import require_module

    with pytest.raises(KeyError):
        require_module("no-such-module")
