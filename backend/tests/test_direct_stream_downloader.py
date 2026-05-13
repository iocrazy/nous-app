"""Unit tests for DirectStream downloader (PR for migration 216).

Covers:
  * Format selection: best avc1 video + best mp4a audio
  * Codec priority: avc1 > hev1/hvc1 > av01
  * Missing video/audio → DirectStreamUnavailable
  * Empty formats list → DirectStreamUnavailable
  * `_project_formats` keeps the right subset of yt-dlp format fields

Network-level paths (httpx fetch, ffmpeg merge) are covered by the
live NAS spike documented in migration 216 header — repeating them
as full mocks here would test the mocks rather than the integration.
"""

from __future__ import annotations

import pytest

from app.services.media.downloader.direct_stream import (
    DirectStreamUnavailable,
    _pick_best_audio,
    _pick_best_video,
    download_from_ytdlp_formats,
)
from app.services.media.parsers.ytdlp_service import YtdlpService


# ────────────────────────────────────────────────────────────────
# Format projection (ytdlp_service._project_formats)
# ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_project_formats_keeps_required_fields():
    """The projection must keep every field DirectStream relies on:
    url, http_headers, vcodec, acodec, format_id, tbr, filesize."""
    raw = [
        {
            "format_id": "30032",
            "url": "https://cdn/video.m4s",
            "ext": "m4s",
            "vcodec": "avc1.64001F",
            "acodec": "none",
            "tbr": 105.584,
            "filesize_approx": 12240828,
            "width": 1280,
            "height": 720,
            "http_headers": {"Referer": "https://www.bilibili.com/"},
            # Junk fields we shouldn't keep
            "manifest_url": "https://api/dash.mpd",
            "fragments": [],
            "downloader_options": {"http_chunk_size": 10485760},
        }
    ]
    out = YtdlpService._project_formats(raw)
    assert len(out) == 1
    f = out[0]
    assert f["format_id"] == "30032"
    assert f["url"] == "https://cdn/video.m4s"
    assert f["vcodec"] == "avc1.64001F"
    assert f["acodec"] == "none"
    assert f["tbr"] == 105.584
    assert f["filesize"] == 12240828
    assert f["width"] == 1280
    assert f["height"] == 720
    assert f["http_headers"] == {"Referer": "https://www.bilibili.com/"}
    # Junk dropped
    for junk in ("manifest_url", "fragments", "downloader_options"):
        assert junk not in f


@pytest.mark.unit
def test_project_formats_filesize_falls_back_to_approx():
    raw = [{"url": "x", "filesize_approx": 999}]
    assert YtdlpService._project_formats(raw)[0]["filesize"] == 999


@pytest.mark.unit
def test_project_formats_skips_urlless_entries():
    """yt-dlp sometimes emits format entries with no `url` (e.g.
    'storyboard' or manifest-only). Drop those."""
    raw = [
        {"format_id": "sb0", "url": None, "vcodec": "none"},
        {"format_id": "ok", "url": "https://cdn/a.m4s", "vcodec": "avc1"},
        {"format_id": "missing"},  # no url key at all
    ]
    out = YtdlpService._project_formats(raw)
    assert len(out) == 1
    assert out[0]["format_id"] == "ok"


@pytest.mark.unit
def test_project_formats_empty():
    assert YtdlpService._project_formats([]) == []


# ────────────────────────────────────────────────────────────────
# Format selection
# ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_pick_best_video_prefers_avc1_over_hev1_over_av01():
    """For player compatibility we want H.264 (avc1) ahead of H.265/HEVC
    (hev1/hvc1) ahead of AV1 — even if AV1 has higher tbr."""
    fmts = [
        {
            "format_id": "av1_high",
            "url": "u1",
            "vcodec": "av01.0",
            "acodec": "none",
            "tbr": 200,
        },
        {
            "format_id": "hev1_mid",
            "url": "u2",
            "vcodec": "hev1.1",
            "acodec": "none",
            "tbr": 150,
        },
        {
            "format_id": "avc1_low",
            "url": "u3",
            "vcodec": "avc1.64",
            "acodec": "none",
            "tbr": 70,
        },
    ]
    picked = _pick_best_video(fmts)
    assert picked["format_id"] == "avc1_low"


@pytest.mark.unit
def test_pick_best_video_highest_tbr_within_codec_class():
    """Among avc1 formats, the highest tbr wins."""
    fmts = [
        {
            "format_id": "avc1_360",
            "url": "u1",
            "vcodec": "avc1.64001E",
            "acodec": "none",
            "tbr": 70,
        },
        {
            "format_id": "avc1_720",
            "url": "u2",
            "vcodec": "avc1.64001F",
            "acodec": "none",
            "tbr": 105,
        },
        {
            "format_id": "avc1_480",
            "url": "u3",
            "vcodec": "avc1.64001E",
            "acodec": "none",
            "tbr": 90,
        },
    ]
    assert _pick_best_video(fmts)["format_id"] == "avc1_720"


@pytest.mark.unit
def test_pick_best_video_ignores_audio_only_and_combined():
    """Pure-audio streams (acodec set, vcodec=none) and
    combined-stream entries (both set) shouldn't be picked as video."""
    fmts = [
        {
            "format_id": "audio",
            "url": "u1",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "tbr": 999,
        },
        {
            "format_id": "combined",
            "url": "u2",
            "vcodec": "avc1.64",
            "acodec": "mp4a.40.2",
            "tbr": 200,
        },
        {
            "format_id": "video_only",
            "url": "u3",
            "vcodec": "avc1.64",
            "acodec": "none",
            "tbr": 80,
        },
    ]
    assert _pick_best_video(fmts)["format_id"] == "video_only"


@pytest.mark.unit
def test_pick_best_audio_picks_highest_tbr_mp4a():
    fmts = [
        {
            "format_id": "30216",
            "url": "u1",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "tbr": 66,
        },
        {
            "format_id": "30280",
            "url": "u2",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "tbr": 102,
        },
        {
            "format_id": "30232",
            "url": "u3",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "tbr": 69,
        },
    ]
    assert _pick_best_audio(fmts)["format_id"] == "30280"


@pytest.mark.unit
def test_pick_best_video_none_when_no_video_format():
    """No video-only formats → None (caller will raise Unavailable)."""
    fmts = [
        {
            "format_id": "audio",
            "url": "u",
            "vcodec": "none",
            "acodec": "mp4a",
            "tbr": 100,
        }
    ]
    assert _pick_best_video(fmts) is None


@pytest.mark.unit
def test_pick_best_audio_none_when_no_audio_format():
    fmts = [
        {
            "format_id": "video",
            "url": "u",
            "vcodec": "avc1",
            "acodec": "none",
            "tbr": 100,
        }
    ]
    assert _pick_best_audio(fmts) is None


# ────────────────────────────────────────────────────────────────
# Top-level guard (DirectStreamUnavailable)
# ────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_download_raises_unavailable_on_empty_formats():
    with pytest.raises(DirectStreamUnavailable, match="empty"):
        await download_from_ytdlp_formats(
            ytdlp_formats=[],
            output_dir="/tmp/test",
            platform_id="bilibili_test",
        )


@pytest.mark.unit
async def test_download_raises_unavailable_when_audio_missing():
    """Even with video formats present, no audio means we can't
    produce a complete mp4 — fall back to yt-dlp instead of half-
    downloading something useless."""
    fmts = [
        {"format_id": "v", "url": "u", "vcodec": "avc1", "acodec": "none", "tbr": 100},
    ]
    with pytest.raises(DirectStreamUnavailable, match="missing video/audio"):
        await download_from_ytdlp_formats(
            ytdlp_formats=fmts,
            output_dir="/tmp/test",
            platform_id="bilibili_test",
        )


@pytest.mark.unit
async def test_download_raises_unavailable_when_video_missing():
    fmts = [
        {"format_id": "a", "url": "u", "vcodec": "none", "acodec": "mp4a", "tbr": 100},
    ]
    with pytest.raises(DirectStreamUnavailable, match="missing video/audio"):
        await download_from_ytdlp_formats(
            ytdlp_formats=fmts,
            output_dir="/tmp/test",
            platform_id="bilibili_test",
        )
