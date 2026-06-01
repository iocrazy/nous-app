"""Tests for best-effort douyin enrichment of qishui UGC videos.

A qishui UGC video's ``video_id`` IS a valid douyin aweme_id. The qishui share
page carries no engagement stats / publish time, but the douyin aweme detail
does. ``enrich_ugc_with_douyin_stats`` fetches the aweme by id and merges the
stats + publish time the qishui page lacks — best-effort, never raising.

A fake parser (matching the ``IesDouyinParser`` surface used by the helper) is
injected via the ``parser=`` param (mirrors how other soda tests fake
collaborators).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.media.parsers.soda_music.ugc_enrich import (
    enrich_ugc_with_douyin_stats,
)

# A parsed_data dict as produced by format_ugc_video (stats absent, time None).
BASE_PARSED = {
    "platform_id": "7637354879229798257",
    "source_platform": "qishui",
    "media_type": "video",
    "title": "Clip",
    "published_at": None,
    "metadata": {"ext": "mp4", "ugc_video_id": "7637354879229798257"},
}

# Sept 2024-ish unix seconds.
CREATE_TIME = 1727000000


class _FakeParser:
    """Mimics IesDouyinParser: classmethod-style ``parse`` + ``_get_user_overrides``
    + ``_fetch_share_page``. The helper picks one entry; we record every call.
    """

    def __init__(self, aweme: dict | None = None, *, raises: bool = False):
        self._aweme = aweme
        self._raises = raises
        self.fetch_calls: list[dict] = []

    async def _get_user_overrides(self, user_id):
        return {}

    async def _fetch_share_page(
        self, video_id, content_type="video", user_agent="", extra_headers=None
    ):
        self.fetch_calls.append(
            {
                "video_id": video_id,
                "content_type": content_type,
                "extra_headers": extra_headers,
            }
        )
        if self._raises:
            raise RuntimeError("douyin down")
        return self._aweme


def _aweme(*, statistics=None, create_time=None) -> dict:
    out: dict = {"aweme_id": "7637354879229798257"}
    if statistics is not None:
        out["statistics"] = statistics
    if create_time is not None:
        out["create_time"] = create_time
    return out


def test_enrich_success_merges_stats_and_published_at():
    aweme = _aweme(
        statistics={
            "digg_count": 100,
            "comment_count": 20,
            "share_count": 5,
            "collect_count": 9,
        },
        create_time=CREATE_TIME,
    )
    parser = _FakeParser(aweme)

    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="7637354879229798257", user_id="u1", parser=parser
        )
    )

    assert out["like_count"] == 100
    assert out["comment_count"] == 20
    assert out["share_count"] == 5
    assert out["favorite_count"] == 9
    assert out["published_at"] == datetime.fromtimestamp(CREATE_TIME, tz=timezone.utc)
    assert isinstance(out["published_at"], datetime)
    # Original keys preserved.
    assert out["platform_id"] == "7637354879229798257"
    assert out["title"] == "Clip"
    assert out["metadata"]["ugc_video_id"] == "7637354879229798257"
    # Fetched by the canonical aweme_id.
    assert parser.fetch_calls[0]["video_id"] == "7637354879229798257"


def test_enrich_does_not_mutate_input():
    aweme = _aweme(statistics={"digg_count": 1}, create_time=CREATE_TIME)
    parser = _FakeParser(aweme)
    snapshot = dict(BASE_PARSED)

    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", user_id=None, parser=parser
        )
    )

    assert out is not BASE_PARSED
    assert BASE_PARSED == snapshot  # input untouched
    assert "like_count" not in BASE_PARSED
    assert BASE_PARSED["published_at"] is None


def test_enrich_parser_returns_none_unchanged():
    parser = _FakeParser(None)
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", user_id=None, parser=parser
        )
    )
    assert out == BASE_PARSED
    assert "like_count" not in out
    assert out["published_at"] is None


def test_enrich_parser_raises_unchanged_no_exception():
    parser = _FakeParser(raises=True)
    # Must NOT raise.
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", user_id="u1", parser=parser
        )
    )
    assert out == BASE_PARSED


def test_enrich_missing_statistics_no_crash():
    aweme = _aweme(create_time=CREATE_TIME)  # no statistics key
    parser = _FakeParser(aweme)
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", user_id=None, parser=parser
        )
    )
    # Stats not set, but publish time merged.
    assert "like_count" not in out
    assert "comment_count" not in out
    assert out["published_at"] == datetime.fromtimestamp(CREATE_TIME, tz=timezone.utc)


def test_enrich_missing_create_time_no_crash():
    aweme = _aweme(statistics={"digg_count": 7})  # no create_time
    parser = _FakeParser(aweme)
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", user_id=None, parser=parser
        )
    )
    assert out["like_count"] == 7
    assert out["published_at"] is None  # unchanged from base


def test_enrich_partial_statistics_only_present_set():
    # Only digg_count present; others absent → only like_count set.
    aweme = _aweme(statistics={"digg_count": 42}, create_time=CREATE_TIME)
    parser = _FakeParser(aweme)
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", user_id=None, parser=parser
        )
    )
    assert out["like_count"] == 42
    assert "comment_count" not in out
    assert "share_count" not in out
    assert "favorite_count" not in out


def test_enrich_bad_create_time_guarded():
    # A non-numeric create_time must not raise; published_at stays None.
    aweme = _aweme(statistics={"digg_count": 1}, create_time="not-a-number")
    parser = _FakeParser(aweme)
    out = asyncio.run(
        enrich_ugc_with_douyin_stats(
            BASE_PARSED, video_id="X", user_id=None, parser=parser
        )
    )
    assert out["like_count"] == 1
    assert out["published_at"] is None
