"""Cover download has a TOTAL deadline so a slow/stuck CDN can't hang it.

Regression: ``settings.DOWNLOAD_TIMEOUT`` is httpx's PER-READ timeout — a byte
trickle from a throttled/blocked cover CDN never trips it but can hang the fetch
for tens of minutes, leaving ``cover_download_status`` stuck at 'pending' and
dragging the (already video-complete) download workflow to a 'lost' reap at the
30-min ceiling. ``download_cover_by_platform_id`` now wraps each per-URL
``download_file`` in ``asyncio.wait_for(timeout=COVER_DOWNLOAD_TIMEOUT)``: a slow
URL fails fast and we move on, so cover is genuinely best-effort.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

import pytest

from app.core.enums import DownloadStatus
from app.services.media.downloader import downloader as dl_mod
from app.services.media.downloader.downloader import DownloaderService


class _FakeMediaRepo:
    def __init__(self, video_data: dict) -> None:
        self._video_data = video_data
        self.updates: List[dict[str, Any]] = []

    async def get_by_platform_id(self, platform_id: str) -> Optional[dict]:
        return self._video_data

    async def update(self, platform_id: str, changes: dict) -> None:
        self.updates.append(changes)


@pytest.mark.asyncio
async def test_cover_download_times_out_per_url_and_fails_fast(monkeypatch, tmp_path):
    """A hanging cover fetch is bounded by COVER_DOWNLOAD_TIMEOUT, every URL is
    tried, and the result is FAILED — not an indefinite hang."""
    video_data = {
        "id": 315422273676144,
        "title": "test cover",
        "source_platform": "douyin",
        "cover_urls": ["https://cdn/one.jpg", "https://cdn/two.jpg"],
    }
    repo = _FakeMediaRepo(video_data)
    monkeypatch.setattr(dl_mod, "MediaRepository", lambda: repo)
    monkeypatch.setattr(
        dl_mod.Utils,
        "create_web_resource_path",
        lambda platform, media_id: (str(tmp_path), "rel/prefix"),
    )
    # Tighten the deadline so the test is fast; the value is what matters.
    monkeypatch.setattr(dl_mod.settings, "COVER_DOWNLOAD_TIMEOUT", 0.05)

    attempted: List[str] = []

    async def _hang(url, file_path, headers=None, *args, **kwargs):
        attempted.append(url)
        await asyncio.sleep(30)  # would hang forever without the wait_for guard
        return True

    monkeypatch.setattr(DownloaderService, "download_file", staticmethod(_hang))

    # Bound the whole test independently — proves we don't hang even if the fix
    # regressed (wait_for removed → this would raise TimeoutError here).
    result = await asyncio.wait_for(
        DownloaderService.download_cover_by_platform_id(
            platform_id="abc", user_id=None
        ),
        timeout=5.0,
    )

    assert result.cover_download_status == DownloadStatus.FAILED
    # Both URLs were attempted (timeout on URL #1 → moved on to URL #2).
    assert attempted == ["https://cdn/one.jpg", "https://cdn/two.jpg"]
    # The all-failed branch cleared the status to FAILED on the row.
    assert any(
        u.get("cover_download_status") == DownloadStatus.FAILED.value
        for u in repo.updates
    )


@pytest.mark.asyncio
async def test_cover_download_succeeds_on_second_url(monkeypatch, tmp_path):
    """If the first URL times out but the second succeeds, cover completes."""
    video_data = {
        "id": 1,
        "title": "t",
        "source_platform": "douyin",
        "cover_urls": ["https://cdn/slow.jpg", "https://cdn/fast.jpg"],
    }
    repo = _FakeMediaRepo(video_data)
    monkeypatch.setattr(dl_mod, "MediaRepository", lambda: repo)
    monkeypatch.setattr(
        dl_mod.Utils,
        "create_web_resource_path",
        lambda platform, media_id: (str(tmp_path), "rel/prefix"),
    )
    monkeypatch.setattr(dl_mod.settings, "COVER_DOWNLOAD_TIMEOUT", 0.05)

    async def _slow_then_fast(url, file_path, headers=None, *args, **kwargs):
        if "slow" in url:
            await asyncio.sleep(30)
            return True
        return True

    monkeypatch.setattr(
        DownloaderService, "download_file", staticmethod(_slow_then_fast)
    )

    result = await asyncio.wait_for(
        DownloaderService.download_cover_by_platform_id(platform_id="abc"),
        timeout=5.0,
    )

    assert result.cover_download_status == DownloadStatus.COMPLETED
    assert any(
        u.get("cover_download_status") == DownloadStatus.COMPLETED.value
        for u in repo.updates
    )
