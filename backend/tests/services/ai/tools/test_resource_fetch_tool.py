"""Verify ResourceFetch dispatch + available_refs gate + error shapes."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.ai.tools.resource_fetch_tool import resource_fetch


@pytest.mark.asyncio
async def test_rejects_unreferenced_id():
    result = await resource_fetch(
        resource_id="999",
        mode=None,
        args=None,
        user_id="u",
        available_refs={"1", "2"},
        request_cache={},
    )
    assert "error" in result
    assert "not referenced" in result["error"]


@pytest.mark.asyncio
async def test_uses_cache_on_second_call():
    cache: dict = {}
    with patch(
        "app.services.ai.tools.resource_fetch_tool._fetch_dispatch",
        return_value={"content": "hello"},
    ) as dispatch_mock:
        await resource_fetch(
            resource_id="1",
            mode="excerpt",
            args=None,
            user_id="u",
            available_refs={"1"},
            request_cache=cache,
        )
        await resource_fetch(
            resource_id="1",
            mode="excerpt",
            args=None,
            user_id="u",
            available_refs={"1"},
            request_cache=cache,
        )
    assert dispatch_mock.call_count == 1


@pytest.mark.asyncio
async def test_inaccessible_returns_error_not_raise():
    with patch(
        "app.services.ai.tools.resource_fetch_tool._fetch_dispatch",
        side_effect=PermissionError("nope"),
    ):
        result = await resource_fetch(
            resource_id="1",
            mode=None,
            args=None,
            user_id="u",
            available_refs={"1"},
            request_cache={},
        )
    assert "error" in result
    assert "not accessible" in result["error"]
