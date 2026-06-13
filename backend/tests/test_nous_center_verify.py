"""Tests for the nous-center verify-protocol helper."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.services.canvas.nous_center_client import (
    NousCenterClient,
    NousCenterError,
)
from app.services.canvas.nous_center_verify import verify_nous_center


def _settings(**overrides: Any) -> Any:
    base = dict(
        NOUS_CENTER_BASE_URL="https://nous.test",
        NOUS_CENTER_TOKEN="tok",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ============================================================
# ping_workflows (client level)
# ============================================================


class TestPingWorkflows:
    @pytest.mark.asyncio
    @respx.mock
    async def test_200_returns_payload(self):
        respx.get("https://nous.test/workflows", params={"limit": "1"}).mock(
            return_value=httpx.Response(
                200, json={"items": [{"slug": "x"}], "next_cursor": None}
            )
        )
        client = NousCenterClient(base_url="https://nous.test", token="tok")
        payload = await client.ping_workflows()
        assert payload["items"][0]["slug"] == "x"

    @pytest.mark.asyncio
    @respx.mock
    async def test_401_raises_with_status_code(self):
        respx.get("https://nous.test/workflows", params={"limit": "1"}).mock(
            return_value=httpx.Response(401, text="bad token")
        )
        client = NousCenterClient(base_url="https://nous.test", token="tok")
        with pytest.raises(NousCenterError) as exc_info:
            await client.ping_workflows()
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @respx.mock
    async def test_transport_failure_raises(self):
        respx.get("https://nous.test/workflows", params={"limit": "1"}).mock(
            side_effect=httpx.ConnectError("refused")
        )
        client = NousCenterClient(base_url="https://nous.test", token="tok")
        with pytest.raises(NousCenterError) as exc_info:
            await client.ping_workflows()
        assert "transport failed" in str(exc_info.value)


# ============================================================
# verify_nous_center (service level)
# ============================================================


@pytest.mark.asyncio
async def test_missing_config_returns_in_band_error():
    settings = SimpleNamespace(NOUS_CENTER_BASE_URL=None, NOUS_CENTER_TOKEN=None)
    result = await verify_nous_center(settings)
    assert result["ok"] is False
    assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_missing_token_returns_in_band_error():
    settings = _settings(NOUS_CENTER_TOKEN="")
    result = await verify_nous_center(settings)
    assert result["ok"] is False
    assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_happy_path_reports_visible_workflows():
    fake_client = AsyncMock()
    fake_client.ping_workflows = AsyncMock(
        return_value={"items": [{"slug": "a"}, {"slug": "b"}]}
    )
    with patch(
        "app.services.canvas.nous_center_verify._build_client",
        return_value=fake_client,
    ):
        result = await verify_nous_center(_settings())
    assert result["ok"] is True
    assert result["workflows_visible"] == 2
    assert result["base_url"] == "https://nous.test"


@pytest.mark.asyncio
async def test_ping_failure_returns_in_band_error_with_status_code():
    fake_client = AsyncMock()
    fake_client.ping_workflows = AsyncMock(
        side_effect=NousCenterError("HTTP 401: bad token", status_code=401)
    )
    with patch(
        "app.services.canvas.nous_center_verify._build_client",
        return_value=fake_client,
    ):
        result = await verify_nous_center(_settings())
    assert result["ok"] is False
    assert "HTTP 401" in result["error"]
    assert result["status_code"] == 401
    assert result["base_url"] == "https://nous.test"


@pytest.mark.asyncio
async def test_payload_missing_items_treated_as_zero_visible():
    fake_client = AsyncMock()
    fake_client.ping_workflows = AsyncMock(return_value={"next_cursor": None})
    with patch(
        "app.services.canvas.nous_center_verify._build_client",
        return_value=fake_client,
    ):
        result = await verify_nous_center(_settings())
    assert result["ok"] is True
    assert result["workflows_visible"] == 0
