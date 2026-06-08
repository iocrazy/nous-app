"""§2.4b: parse_workflow async-conversion body test.

Drives the real ``parse_workflow`` body (``inspect.unwrap`` past the
@DBOS.workflow decorator — same approach as test_pass4 / transcode thumbnail
scope tests, since the decorator refuses to run before DBOS.launch() but
preserves the inner coroutine via @wraps). All @DBOS.steps are patched: the
sync ones return canned data (called WITHOUT await, exactly as DBOS runs them
from an async workflow), and the now-async pure-DB steps
(save_media_step / auto_tag_step) are AsyncMocks that MUST be awaited.

Proves: (a) parse_workflow is async and its body awaits the async DB steps,
(b) sync steps are still invoked no-await, (c) the workflow returns success.
This is the async-orchestration proof for the sync→async parse_workflow
conversion (the pattern itself — async @DBOS.workflow + Queue.enqueue + mixed
sync/async steps — is already proven live by soda/download/extract_audio).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_USER = "11111111-1111-1111-1111-111111111111"


async def test_parse_workflow_is_async():
    from app.workflows import parse as p

    # The decorator wraps it; unwrap to the user function and assert it's a
    # coroutine function (the sync→async §2.4b conversion).
    inner = inspect.unwrap(p.parse_workflow)
    assert inspect.iscoroutinefunction(inner)
    # The two hoisted pure-DB steps are async too.
    assert inspect.iscoroutinefunction(inspect.unwrap(p.save_media_step))
    assert inspect.iscoroutinefunction(inspect.unwrap(p.auto_tag_step))


async def test_parse_workflow_body_awaits_async_db_steps():
    from app.workflows import parse as p

    save_media = AsyncMock(return_value={"id": "m1", "resource_id": "r1"})
    auto_tag = AsyncMock(return_value=None)
    dispatch = MagicMock(return_value={"dispatched": True})
    fetched = {
        "aweme_detail": {"text_extra": []},
        "parsed_data": {
            "platform_id": "p1",
            "media_type": 0,
            "title": "Clip",
            "description": "d",
            "cover_urls": [],
        },
    }

    with (
        patch.object(p, "mark_parse_processing_step", MagicMock()),
        patch.object(p, "update_parse_subtitle_step", MagicMock()),
        patch.object(p, "extract_url_step", MagicMock(side_effect=lambda u: u)),
        patch.object(p, "fetch_and_parse_step", MagicMock(return_value=fetched)),
        patch.object(p, "save_media_step", save_media),
        patch.object(p, "update_parse_tracking_step", MagicMock()),
        patch.object(p, "auto_tag_step", auto_tag),
        patch.object(p, "dispatch_download_step", dispatch),
        patch.object(p, "log_parse_outcome_step", MagicMock()),
    ):
        body = inspect.unwrap(p.parse_workflow)
        result = await body("https://x/v", _USER, platform="douyin")

    assert result["status"] == "success"
    assert result["platform_id"] == "p1"
    # The async pure-DB steps were AWAITED (AsyncMock records the await).
    save_media.assert_awaited_once()
    auto_tag.assert_awaited_once()
    # The sync download dispatch step ran (no-await) and its result flowed out.
    dispatch.assert_called_once()
    assert result["download_dispatch"] == {"dispatched": True}


async def test_parse_workflow_raises_when_save_media_returns_none():
    # save_media_step → None must raise (DBOS marks ERROR), not silently
    # "succeed" — the §2.4b conversion must preserve the raise-on-failure rule.
    from app.workflows import parse as p

    fetched = {
        "aweme_detail": {"text_extra": []},
        "parsed_data": {"platform_id": "p1", "media_type": 0, "title": "t"},
    }
    with (
        patch.object(p, "mark_parse_processing_step", MagicMock()),
        patch.object(p, "update_parse_subtitle_step", MagicMock()),
        patch.object(p, "extract_url_step", MagicMock(side_effect=lambda u: u)),
        patch.object(p, "fetch_and_parse_step", MagicMock(return_value=fetched)),
        patch.object(p, "save_media_step", AsyncMock(return_value=None)),
    ):
        body = inspect.unwrap(p.parse_workflow)
        with pytest.raises(RuntimeError, match="save_metadata_only failed"):
            await body("https://x/v", _USER, platform="douyin")
