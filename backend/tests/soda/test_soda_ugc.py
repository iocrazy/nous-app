"""Tests for the qishui UGC video resolution layer (Phase 6 Tasks 1-3).

A qishui UGC video is a plain unencrypted MP4 resolved by scraping the share
page HTML (``GET …/qishui/share/ugc_video?ugc_video_id=<id>``) → the
``window._ROUTER_DATA`` JSON → ``loaderData.ugc_video_page.videoOptions``.

These tests exercise:
- ``extract_router_data`` (pure HTML scrape) + ``get_ugc_video`` (client method).
- ``format_ugc_video`` (videoOptions → parsed_media-shaped video dict).
- ``resolve_qishui_metadata`` routing a ugc_video url to the video path.

Network is exercised against an injected fake client (no real requests).
"""

from __future__ import annotations

import asyncio

from app.services.media.parsers.soda_music.parse_entry import resolve_qishui_metadata
from app.services.media.parsers.soda_music.soda_api import (
    SodaApiClient,
    extract_router_data,
)
from app.services.media.parsers.soda_music.ugc_formatter import format_ugc_video

# A realistic videoOptions dict (blueprint §A.2.1).
VIDEO_OPTIONS = {
    "url": "https://x.douyinvod.com/v.mp4",
    "videoName": "Clip",
    "artistName": "Bob",
    "coverURL": "https://c/cover.jpg",
    "duration": 12,  # SECONDS (float in the wild, e.g. 34.968) — not ms
    "width": 720,
    "height": 1280,
    "group_download_level": 2,
    "hasCopyright": False,
}


def _share_html(video_options: dict) -> str:
    import json

    blob = json.dumps(
        {"loaderData": {"ugc_video_page": {"videoOptions": video_options}}}
    )
    return (
        "<html><head></head><body>"
        f"<script>window._ROUTER_DATA = {blob}</script>"
        "</body></html>"
    )


# --- Task 1: extract_router_data --------------------------------------------


def test_extract_router_data_happy():
    vo = extract_router_data(_share_html(VIDEO_OPTIONS))
    assert vo == VIDEO_OPTIONS


def test_extract_router_data_garbage_returns_none():
    assert extract_router_data("<html>no router data here</html>") is None
    assert extract_router_data("") is None
    assert extract_router_data("window._ROUTER_DATA = {not json") is None


def test_extract_router_data_html_escaped():
    # The JSON blob arrives HTML-entity escaped (&quot; instead of ").
    raw = _share_html(VIDEO_OPTIONS)
    escaped = raw.replace('"', "&quot;")
    # Sanity: plain html.unescape round-trips the blob back to valid JSON.
    assert "&quot;" in escaped
    vo = extract_router_data(escaped)
    assert vo == VIDEO_OPTIONS


def test_extract_router_data_real_page_shape_no_window_prefix_with_decoy():
    # Regression (2026-06-01 prod): the real share page emits
    # ``_ROUTER_DATA = {...}`` with NO ``window.`` prefix, and ALSO carries
    # decoy hydration JS later (``_ROUTER_DATA={}`` + ``_ROUTER_DATA=JSON.parse
    # (...)``). The original extractor searched for the literal
    # "window._ROUTER_DATA" → not found → None → SodaApiError on every real
    # UGC download. The fix iterates every ``_ROUTER_DATA = {`` assignment and
    # returns the first that yields a real videoOptions.
    import json

    blob = json.dumps(
        {
            "loaderData": {
                "ugc_video_layout": None,
                "ugc_video_page": {
                    "video_id": "7637354879229798257",
                    "videoOptions": VIDEO_OPTIONS,
                },
            }
        }
    )
    html = (
        "<div><!--/$--></div>"
        f'<script async="" data-script-src="modern-inline">_ROUTER_DATA = {blob}</script>'
        "<script>function initRouterData(e){try{"
        "_ROUTER_DATA=JSON.parse(r.textContent)}catch(r){_ROUTER_DATA={}}}"
        "</script>"
    )
    vo = extract_router_data(html)
    assert vo == VIDEO_OPTIONS


def test_extract_router_data_skips_empty_decoy_before_real():
    # If an empty ``_ROUTER_DATA={}`` appears BEFORE the real one, the
    # extractor must skip it (no videoOptions) and keep scanning.
    import json

    real = json.dumps(
        {"loaderData": {"ugc_video_page": {"videoOptions": VIDEO_OPTIONS}}}
    )
    html = f"<script>_ROUTER_DATA={{}}</script><script>_ROUTER_DATA = {real}</script>"
    vo = extract_router_data(html)
    assert vo == VIDEO_OPTIONS


def test_extract_router_data_missing_video_options_returns_none():
    bad = '<script>window._ROUTER_DATA = {"loaderData": {}}</script>'
    assert extract_router_data(bad) is None


# --- Task 1: get_ugc_video ---------------------------------------------------


class _FakeResp:
    def __init__(
        self,
        text: str,
        status: int = 200,
        url: str = "",
        headers: dict | None = None,
    ):
        self.text = text
        self.status_code = status
        self.url = url
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Captures the GET url and returns a canned response with ``.text``."""

    def __init__(self, resp: _FakeResp):
        self._resp = resp
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._resp


def test_get_ugc_video_builds_url_and_returns_options():
    fake = _FakeClient(_FakeResp(_share_html(VIDEO_OPTIONS)))
    client = SodaApiClient(cookie="sessionid=xyz", client_factory=lambda: fake)

    vo = asyncio.run(client.get_ugc_video("UV1"))

    assert vo == VIDEO_OPTIONS
    call = fake.calls[0]
    assert call["url"] == (
        "https://music.douyin.com/qishui/share/ugc_video?ugc_video_id=UV1"
    )
    # share page uses a browser UA + cookie, NOT the LunaPC API headers.
    assert call["headers"]["Cookie"] == "sessionid=xyz"
    assert call["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert call["headers"]["Referer"] == "https://music.douyin.com/"


def test_get_ugc_video_raises_when_no_video_options():
    fake = _FakeClient(_FakeResp("<html>no data</html>"))
    client = SodaApiClient(cookie="c", client_factory=lambda: fake)

    import pytest

    from app.services.media.parsers.soda_music.soda_api import SodaApiError

    with pytest.raises(SodaApiError):
        asyncio.run(client.get_ugc_video("UV1"))


def test_get_ugc_video_rejects_oversized_content_length():
    # An upstream advertising a huge content-length is rejected before the
    # body is read into resp.text.
    import pytest

    from app.services.media.parsers.soda_music.soda_api import (
        MAX_SHARE_PAGE_BYTES,
        SodaApiError,
    )

    resp = _FakeResp(
        _share_html(VIDEO_OPTIONS),
        headers={"content-length": str(MAX_SHARE_PAGE_BYTES + 1)},
    )
    fake = _FakeClient(resp)
    client = SodaApiClient(cookie="c", client_factory=lambda: fake)

    with pytest.raises(SodaApiError):
        asyncio.run(client.get_ugc_video("UV1"))


def test_get_ugc_video_rejects_oversized_body():
    # An upstream that lies about content-length (or omits it) but streams an
    # oversized body is still rejected after decoding.
    import pytest

    from app.services.media.parsers.soda_music import soda_api
    from app.services.media.parsers.soda_music.soda_api import SodaApiError

    orig = soda_api.MAX_SHARE_PAGE_BYTES
    soda_api.MAX_SHARE_PAGE_BYTES = 32  # shrink so the test stays small
    try:
        # Valid HTML but well over the (shrunk) 32-byte cap, no content-length.
        resp = _FakeResp(_share_html(VIDEO_OPTIONS))
        fake = _FakeClient(resp)
        client = SodaApiClient(cookie="c", client_factory=lambda: fake)
        with pytest.raises(SodaApiError):
            asyncio.run(client.get_ugc_video("UV1"))
    finally:
        soda_api.MAX_SHARE_PAGE_BYTES = orig


# --- Task 2: format_ugc_video ------------------------------------------------


def test_format_ugc_video():
    pd = format_ugc_video(
        VIDEO_OPTIONS,
        ugc_video_id="UV1",
        original_url="https://music.douyin.com/qishui/share/ugc_video?ugc_video_id=UV1",
    )

    assert pd["platform_id"] == "UV1"
    assert pd["original_url"].endswith("ugc_video_id=UV1")
    assert pd["source_platform"] == "qishui"
    assert pd["media_type"] == "video"
    assert pd["title"] == "Clip"
    assert pd["author"] == "Bob"
    assert pd["duration"] == "00:12"  # 12s → 12000ms → Utils.format_duration
    assert pd["cover_urls"] == ["https://c/cover.jpg"]
    assert pd["video_download_urls"] == ["https://x.douyinvod.com/v.mp4"]
    assert pd["published_at"] is None

    md = pd["metadata"]
    assert md["ext"] == "mp4"
    assert md["width"] == 720
    assert md["height"] == 1280
    assert md["duration_ms"] == 12000  # 12 seconds × 1000
    assert md["duration_seconds"] == 12
    assert md["group_download_level"] == 2
    assert md["hasCopyright"] is False
    assert md["ugc_video_id"] == "UV1"


def test_format_ugc_video_defaults_on_missing_fields():
    pd = format_ugc_video({}, ugc_video_id="UV2", original_url="http://x")

    assert pd["title"] == "untitled"
    assert pd["author"] is None
    assert pd["duration"] == "00:00"
    assert pd["cover_urls"] is None
    assert pd["video_download_urls"] == []


# --- Task 3: resolve_qishui_metadata ugc branch ------------------------------


class _UgcApi:
    """Fake api exposing get_ugc_video (track_id-in-query bypasses classify)."""

    def __init__(self):
        self.calls: list[str] = []

    async def get_ugc_video(self, ugc_video_id: str) -> dict:
        self.calls.append(ugc_video_id)
        return VIDEO_OPTIONS


def test_resolve_qishui_metadata_ugc(monkeypatch):
    api = _UgcApi()

    # The ugc branch best-effort enriches with douyin stats. Patch the helper
    # to exercise the wiring (id threaded through) + the merge into parsed_data,
    # without touching the network.
    enrich_calls: list[dict] = []

    async def _fake_enrich(parsed_data, *, video_id, user_id):
        enrich_calls.append({"video_id": video_id, "user_id": user_id})
        out = dict(parsed_data)
        out["like_count"] = 100
        out["published_at"] = "2024-09-22T00:00:00+00:00"
        return out

    monkeypatch.setattr(
        "app.services.media.parsers.soda_music.parse_entry."
        "enrich_ugc_with_douyin_stats",
        _fake_enrich,
    )

    pd = asyncio.run(
        resolve_qishui_metadata(
            url=("https://music.douyin.com/qishui/share/ugc_video?ugc_video_id=UV1"),
            user_id="u1",
            api=api,
        )
    )

    assert pd["media_type"] == "video"
    assert pd["platform_id"] == "UV1"
    assert pd["video_download_urls"] == ["https://x.douyinvod.com/v.mp4"]
    assert api.calls == ["UV1"]
    # Enrichment invoked with the UGC video_id (== douyin aweme_id) and merged.
    assert enrich_calls == [{"video_id": "UV1", "user_id": "u1"}]
    assert pd["like_count"] == 100
    assert pd["published_at"] == "2024-09-22T00:00:00+00:00"
