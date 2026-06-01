"""Tests for best-effort douyin enrichment of qishui UGC videos.

A qishui UGC video's ``video_id`` IS a valid douyin aweme_id. The qishui share
page carries no engagement stats / publish time, but the douyin VIDEO PAGE
(``https://www.douyin.com/video/<id>``) sometimes serves a clean HTML page
whose ``_ROUTER_DATA`` carries the full stats. ``enrich_ugc_with_douyin_stats``
fetches that page and merges the stats + publish time the qishui page lacks.

This is best-effort and INTERMITTENT by nature: douyin's anti-scrape often
serves a JS-VM-protected page (``jsvmprt``, no ``_ROUTER_DATA``) instead. When
protected, stats stay hidden and the parse proceeds unchanged.

The HTML fetch is injected via the ``html_fetcher=`` param so tests exercise
the real ``_ROUTER_DATA`` parsing against realistic douyin-page HTML shapes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app.services.media.parsers.soda_music.ugc_enrich import (
    enrich_ugc_with_douyin_stats,
    extract_douyin_aweme_item,
)

# A parsed_data dict as produced by format_ugc_video (stats absent, time None).
BASE_PARSED = {
    "platform_id": "UV1",
    "source_platform": "qishui",
    "media_type": "video",
    "title": "clip",
    "published_at": None,
    "metadata": {"ext": "mp4", "ugc_video_id": "7637354879229798257"},
}

CREATE_TIME = 1778210253


def _clean_html() -> str:
    """A douyin video page embedding _ROUTER_DATA with a full aweme item under
    a dynamic page key (``video_(id)/page``)."""
    router_data = {
        "loaderData": {
            "video_(7637354879229798257)/page": {
                "videoInfoRes": {
                    "item_list": [
                        {
                            "aweme_id": "7637354879229798257",
                            "create_time": CREATE_TIME,
                            "desc": "clip",
                            "statistics": {
                                "digg_count": 914067,
                                "comment_count": 44119,
                                "share_count": 87658,
                                "collect_count": 201590,
                            },
                        }
                    ]
                }
            }
        },
        "errors": None,
    }
    blob = json.dumps(router_data, ensure_ascii=False)
    return (
        f"<html><head></head><body><script>_ROUTER_DATA = {blob}</script></body></html>"
    )


_CLEAN_HTML = _clean_html()

_JSVMPRT_HTML = (
    "<html><head><script>window.jsvmprt = 1;</script></head><body></body></html>"
)

_NO_ITEMLIST_HTML = (
    "<html><body><script>"
    '_ROUTER_DATA = {"loaderData":{"x/page":{}}}'
    "</script></body></html>"
)


def _fetcher(html: str | None):
    async def _f(video_id: str):
        return html

    return _f


def _raising_fetcher():
    async def _f(video_id: str):
        raise RuntimeError("douyin down")

    return _f


# --- extract_douyin_aweme_item -------------------------------------------------


def test_extract_douyin_aweme_item_happy():
    item = extract_douyin_aweme_item(_CLEAN_HTML)
    assert item is not None
    assert item["aweme_id"] == "7637354879229798257"
    assert item["statistics"]["digg_count"] == 914067


def test_extract_douyin_aweme_item_jsvmprt_returns_none():
    assert extract_douyin_aweme_item(_JSVMPRT_HTML) is None


def test_extract_douyin_aweme_item_no_itemlist_returns_none():
    assert extract_douyin_aweme_item(_NO_ITEMLIST_HTML) is None


# --- enrich_ugc_with_douyin_stats ---------------------------------------------


def test_enrich_success_merges_stats_and_time():
    snapshot = json.loads(json.dumps(BASE_PARSED))

    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED,
            video_id="7637354879229798257",
            html_fetcher=_fetcher(_CLEAN_HTML),
        )
    )

    assert out["like_count"] == 914067
    assert out["comment_count"] == 44119
    assert out["share_count"] == 87658
    assert out["favorite_count"] == 201590
    assert isinstance(out["published_at"], datetime)
    assert out["published_at"] == datetime.fromtimestamp(CREATE_TIME, tz=timezone.utc)
    # Original keys preserved.
    assert out["platform_id"] == "UV1"
    assert out["title"] == "clip"
    # Immutability: new dict, input untouched.
    assert out is not BASE_PARSED
    assert BASE_PARSED == snapshot
    assert "like_count" not in BASE_PARSED
    assert BASE_PARSED["published_at"] is None


def test_enrich_jsvmprt_returns_unchanged():
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", html_fetcher=_fetcher(_JSVMPRT_HTML)
        )
    )
    assert out == BASE_PARSED
    assert "like_count" not in out
    assert out["published_at"] is None


def test_enrich_fetch_none_returns_unchanged():
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", html_fetcher=_fetcher(None)
        )
    )
    assert out == BASE_PARSED


def test_enrich_fetcher_raises_returns_unchanged():
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", html_fetcher=_raising_fetcher()
        )
    )
    assert out == BASE_PARSED


def test_enrich_missing_create_time_or_stats():
    router_data = {
        "loaderData": {
            "video_(X)/page": {"videoInfoRes": {"item_list": [{"aweme_id": "X"}]}}
        }
    }
    html = (
        "<html><body><script>_ROUTER_DATA = "
        + json.dumps(router_data)
        + "</script></body></html>"
    )
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", html_fetcher=_fetcher(html)
        )
    )
    assert "like_count" not in out
    assert "comment_count" not in out
    assert out["published_at"] is None  # unchanged from base


def test_enrich_user_id_accepted_but_ignored():
    # user_id is accepted for caller-signature stability; passing it must work.
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED,
            video_id="7637354879229798257",
            user_id="u1",
            html_fetcher=_fetcher(_CLEAN_HTML),
        )
    )
    assert out["like_count"] == 914067
