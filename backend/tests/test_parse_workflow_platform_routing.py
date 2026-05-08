"""Regression tests for parse_workflow platform routing.

Background — production P1 found 2026-05-08:
`fetch_and_parse_step` always called the douyin-only `fetch_and_parse`
helper regardless of platform, so bilibili / youtube URLs were forced
through ABogus → IesParser → DrissionPage and 100% failed when
DrissionPage timed out waiting for a douyin API response that never
came (DrissionPage was opening a bilibili page).

The fix threads `platform` from the API edge through `parse_workflow` →
`fetch_and_parse_step`, branching to `fetch_and_parse_ytdlp` for
non-douyin URLs. These tests pin:
  - the new yt-dlp helper's data shape (so auto_tag_media keeps working)
  - the parse_workflow + step signatures (so a future refactor can't
    silently drop the `platform` kwarg again)
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest


@pytest.fixture
def fake_ytdlp_info() -> dict:
    """Minimal yt-dlp info_dict — only the fields _map_metadata_to_media
    actually reads. Real ones carry ~80 fields; we keep the surface tight
    so a schema drift in yt-dlp doesn't fail this regression test."""
    return {
        "id": "BV1w5okBAEte",
        "title": "Test Bilibili Video",
        "description": "A test description",
        "duration": 209.0,
        "width": 480,
        "height": 852,
        "filesize_approx": 3912919,
        "uploader": "Test Author",
        "thumbnail": "https://example.com/cover.jpg",
        "tags": ["语音项目", "模型选型", "踩坑"],
        "like_count": 100,
        "comment_count": 10,
        "webpage_url": "https://www.bilibili.com/video/BV1w5okBAEte",
    }


def test_parse_workflow_accepts_platform_kwarg():
    """parse_workflow MUST accept a `platform` kwarg — that's how the
    API edge tells the workflow which parser to use. Default must be
    "douyin" for backward-compat with workflows queued before the fix."""
    from app.workflows.parse import parse_workflow

    sig = inspect.signature(parse_workflow)
    assert "platform" in sig.parameters
    assert sig.parameters["platform"].default == "douyin"


def test_fetch_and_parse_step_accepts_platform_kwarg():
    """The step layer must also accept platform — without this, even if
    parse_workflow has it, the step keeps calling the douyin helper."""
    from app.workflows.parse import fetch_and_parse_step

    # @DBOS.step preserves the wrapped function's signature on most
    # decorator implementations. If this assertion ever fails post-DBOS
    # upgrade, switch to checking the inner function via .__wrapped__.
    sig = inspect.signature(fetch_and_parse_step)
    assert "platform" in sig.parameters
    assert sig.parameters["platform"].default == "douyin"


def test_fetch_and_parse_ytdlp_maps_tags_to_text_extra(fake_ytdlp_info):
    """auto_tag_media reads `aweme_detail["text_extra"][n]["hashtag_name"]`.
    yt-dlp gives us a flat tags list — this test pins the conversion so
    auto_tag keeps working for non-douyin platforms without touching
    classification_service."""
    from app.services.media.parsers.parse_helpers import fetch_and_parse_ytdlp

    async def _async_return(*_a, **_kw):
        return fake_ytdlp_info

    with patch(
        "app.services.media.parsers.ytdlp_service.YtdlpService.fetch_metadata",
        side_effect=_async_return,
    ), patch("app.boundary.validate_url") as mock_validate:
        from app.boundary import ValidatedURL

        mock_validate.return_value = ValidatedURL(
            "https://www.bilibili.com/video/BV1w5okBAEte"
        )

        aweme_detail, parsed_data = fetch_and_parse_ytdlp(
            "https://www.bilibili.com/video/BV1w5okBAEte",
            video_bool=True,
            cover_bool=True,
            user_id="u1",
        )

    assert aweme_detail["text_extra"] == [
        {"hashtag_name": "语音项目"},
        {"hashtag_name": "模型选型"},
        {"hashtag_name": "踩坑"},
    ]
    assert parsed_data["title"] == "Test Bilibili Video"
    assert parsed_data["need_download_video"] is True
    assert parsed_data["need_download_cover"] is True
    assert parsed_data["need_download_music"] is False


def test_fetch_and_parse_ytdlp_handles_empty_tags():
    """Some yt-dlp extractors return no tags — ensure we still get a
    well-formed empty `text_extra` list rather than a KeyError or
    None.get()-style crash downstream in auto_tag_media."""
    from app.services.media.parsers.parse_helpers import fetch_and_parse_ytdlp

    info_no_tags = {
        "id": "v1",
        "title": "Untagged",
        "description": "",
        "duration": 60.0,
    }

    async def _async_return(*_a, **_kw):
        return info_no_tags

    with patch(
        "app.services.media.parsers.ytdlp_service.YtdlpService.fetch_metadata",
        side_effect=_async_return,
    ), patch("app.boundary.validate_url") as mock_validate:
        from app.boundary import ValidatedURL

        mock_validate.return_value = ValidatedURL("https://example.com/v1")

        aweme_detail, _parsed = fetch_and_parse_ytdlp(
            "https://example.com/v1",
            video_bool=True,
            cover_bool=True,
        )

    assert aweme_detail["text_extra"] == []
