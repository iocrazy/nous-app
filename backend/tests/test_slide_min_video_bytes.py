"""Regression test for the Live Photo (图文) slide download size-gate bug.

Background — 2026-06-19: a douyin image-text post that is actually 24 Live
Photos (实况图 — each "image" carries a short embedded video) downloaded 0/24
slides. The formatter correctly routed the 24 clips into video_download_urls
and the slide downloader correctly fetched them (with the douyin Referer), but
`download_file` deletes any .mp4 smaller than 200KB as a "CDN error response".
Live Photo clips are 2-3s loops (~70-180KB measured), so every real slide was
silently deleted → 0/24, image_download_status=failed.

The fix parameterizes the floor: `download_file(..., min_video_bytes=...)`
defaults to 200KB (correct for full videos) and `download_slide_item` passes a
much smaller floor so legitimately-small Live Photo clips survive. ffprobe
integrity verification remains the real gate against non-video responses.
"""

from __future__ import annotations

import importlib
import inspect
import re


def _downloader_source() -> str:
    mod = importlib.import_module("app.services.media.downloader.downloader")
    return inspect.getsource(mod)


def test_download_file_has_min_video_bytes_param() -> None:
    """download_file must expose a tunable min-size floor, not a hardcoded
    200KB literal in the .mp4 reject check."""
    source = _downloader_source()
    assert re.search(r"def download_file\([^)]*min_video_bytes", source, re.DOTALL), (
        "download_file lost its min_video_bytes parameter — the 200KB floor is "
        "back to hardcoded and will delete small Live Photo slides again."
    )
    # The reject check must compare against the parameter, never a literal.
    assert (
        "actual_size < min_video_bytes" in source
    ), "the .mp4 too-small check must use min_video_bytes."
    assert not re.search(r"actual_size < 200 \* 1024", source), (
        "a hardcoded `actual_size < 200 * 1024` reject is back — Live Photo "
        "slides (~70-180KB) will be silently deleted as CDN errors."
    )


def test_slide_download_uses_small_floor() -> None:
    """download_slide_item must pass a sub-200KB min_video_bytes so Live Photo
    clips are not rejected by the full-video floor."""
    source = _downloader_source()
    anchor = source.find("async def download_slide_item")
    assert anchor != -1, "download_slide_item moved/renamed — update this test."
    body = source[anchor : anchor + 2600]
    m = re.search(r"min_video_bytes\s*=\s*(\d+)\s*\*\s*1024", body)
    assert m, (
        "download_slide_item must pass an explicit min_video_bytes to "
        "download_file (Live Photo slides are smaller than the 200KB default)."
    )
    assert int(m.group(1)) < 200, (
        f"slide min_video_bytes floor is {m.group(1)}KB — must be < 200KB or "
        "real Live Photo clips get deleted as CDN errors again."
    )
