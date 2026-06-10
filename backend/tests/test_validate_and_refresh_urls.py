"""Tests for download-time URL HEAD validation hardening.

Background — production P1 found 2026-06-10: ``validate_and_refresh_urls``
cleared ``parsed_media.video_download_urls`` in the DB whenever ALL HEAD
probes failed — including on transient DNS/timeout blips (e.g.
aweme.snssdk.com occasionally not resolving for 30s). With the re-parse
chain broken too, that nuked perfectly good URLs and pushed the download
into the yt-dlp HEVC fallback (black screen, audio only).

New contract pinned here:
  - transient failures (network errors, 5xx, 429) → NO DB write, NO
    re-parse; soft-fail so the caller attempts the GET download anyway
  - permanent failures (403/404/410 — expired/blocked CDN URL) →
    re-parse for fresh URLs, but NEVER NULL the DB column first; old
    URLs survive a failed re-parse
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.tasks import download_helpers as dh


def test_is_permanent_url_failure_classifier():
    # definitive HTTP rejections → permanent
    assert dh.is_permanent_url_failure("HTTP 403") is True
    assert dh.is_permanent_url_failure("HTTP 404") is True
    assert dh.is_permanent_url_failure("HTTP 410") is True
    # rate-limit / server-side blips → transient
    assert dh.is_permanent_url_failure("HTTP 429") is False
    assert dh.is_permanent_url_failure("HTTP 500") is False
    assert dh.is_permanent_url_failure("HTTP 502") is False
    # network-level errors (str(exception)) → transient
    assert dh.is_permanent_url_failure("timed out") is False
    assert (
        dh.is_permanent_url_failure("[Errno 8] nodename nor servname provided")
        is False
    )
    assert dh.is_permanent_url_failure("") is False


def _media(**overrides) -> dict:
    media = {
        "platform_id": "999",
        "original_url": "https://v.douyin.com/abc/",
        "video_download_urls": ["https://cdn.example/v1", "https://cdn.example/v2"],
        "source_platform": "douyin",
    }
    media.update(overrides)
    return media


def test_first_accessible_url_short_circuits():
    with (
        patch.object(
            dh, "check_url_accessible", new=AsyncMock(return_value=(True, "ok"))
        ),
        patch.object(dh, "ensure_download_urls") as ensure,
    ):
        media, ok, reason = dh.validate_and_refresh_urls(
            "999", _media(), "video_download_urls", "video"
        )

    assert ok is True
    assert reason == "ok"
    ensure.assert_not_called()


def test_transient_failure_does_not_clear_urls_or_reparse():
    with (
        patch.object(
            dh,
            "check_url_accessible",
            new=AsyncMock(return_value=(False, "timed out")),
        ),
        patch.object(dh, "ensure_download_urls") as ensure,
        patch(
            "app.repositories.media_repository.MediaRepository.update",
            new=AsyncMock(),
        ) as repo_update,
    ):
        media, ok, reason = dh.validate_and_refresh_urls(
            "999", _media(), "video_download_urls", "video"
        )

    assert ok is False
    assert "timed out" in reason
    # the whole point: a DNS blip must not destroy state or trigger re-parse
    ensure.assert_not_called()
    repo_update.assert_not_awaited()
    assert media["video_download_urls"] == [
        "https://cdn.example/v1",
        "https://cdn.example/v2",
    ]


def test_permanent_failure_reparses_without_nulling_db():
    def fake_ensure(platform_id, media, needed_types):
        media["video_download_urls"] = ["https://cdn.example/fresh"]
        return media

    head_results = iter(
        [
            (False, "HTTP 403"),  # old URL 1
            (False, "HTTP 403"),  # old URL 2
            (True, "ok"),  # fresh URL
        ]
    )

    async def fake_head(url, timeout=10.0):
        return next(head_results)

    with (
        patch.object(dh, "check_url_accessible", side_effect=fake_head),
        patch.object(dh, "ensure_download_urls", side_effect=fake_ensure) as ensure,
        patch(
            "app.repositories.media_repository.MediaRepository.update",
            new=AsyncMock(),
        ) as repo_update,
    ):
        media, ok, reason = dh.validate_and_refresh_urls(
            "999", _media(), "video_download_urls", "video"
        )

    assert ok is True
    ensure.assert_called_once()
    # hardening: the old "clear DB column, then re-parse" write is gone —
    # a failed re-parse must leave the previous URLs intact in the DB
    repo_update.assert_not_awaited()
    assert media["video_download_urls"] == ["https://cdn.example/fresh"]


def test_permanent_failure_with_failed_reparse_keeps_db_untouched():
    with (
        patch.object(
            dh,
            "check_url_accessible",
            new=AsyncMock(return_value=(False, "HTTP 404")),
        ),
        patch.object(dh, "ensure_download_urls", side_effect=lambda p, m, t: m),
        patch(
            "app.repositories.media_repository.MediaRepository.update",
            new=AsyncMock(),
        ) as repo_update,
    ):
        media, ok, reason = dh.validate_and_refresh_urls(
            "999", _media(), "video_download_urls", "video"
        )

    assert ok is False
    assert reason == "re-parse returned no URLs"
    repo_update.assert_not_awaited()


def test_no_urls_short_circuits():
    media, ok, reason = dh.validate_and_refresh_urls(
        "999", _media(video_download_urls=[]), "video_download_urls", "video"
    )
    assert ok is False
    assert reason == "no URLs available"
