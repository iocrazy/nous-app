# backend/tests/test_nous_governance.py
"""nous master control gates: global default-OFF, per-module default-ON."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.governance import ai_governance as gov


@pytest.mark.asyncio
async def test_global_default_off_when_absent():
    with patch.object(gov, "_read_raw", new=AsyncMock(return_value=None)):
        assert await gov.is_nous_globally_enabled() is False


@pytest.mark.asyncio
async def test_global_true_when_set():
    with patch.object(gov, "_read_raw", new=AsyncMock(return_value=True)):
        assert await gov.is_nous_globally_enabled() is True


@pytest.mark.asyncio
async def test_module_allowed_false_when_global_off():
    # Global off → module always disallowed regardless of per-module value.
    async def _read(key):
        return None  # global absent (off), module absent

    with patch.object(gov, "_read_raw", side_effect=_read):
        assert await gov.is_nous_allowed("transcription") is False


@pytest.mark.asyncio
async def test_module_default_on_when_global_on():
    async def _read(key):
        if key == "nous.user_enabled":
            return True
        return None  # per-module absent → default-on

    with patch.object(gov, "_read_raw", side_effect=_read):
        assert await gov.is_nous_allowed("transcription") is True


@pytest.mark.asyncio
async def test_module_off_when_explicitly_disabled():
    async def _read(key):
        if key == "nous.user_enabled":
            return True
        if key == "ai_module.caption.nous_allowed":
            return False
        return None

    with patch.object(gov, "_read_raw", side_effect=_read):
        assert await gov.is_nous_allowed("caption") is False
