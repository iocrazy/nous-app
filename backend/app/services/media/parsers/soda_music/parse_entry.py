"""Entry helper: a qishui URL → parsed_media-shaped dict.

Classifies the URL (track_id / ugc_video_id in query, or a short link needing a
HEAD redirect), then resolves the right metadata:

- ``track`` → ``parse_track`` (audio, via the LunaPC JSON API).
- ``ugc_video`` → ``get_ugc_video`` + ``format_ugc_video`` (video, scraped from
  the share page; Phase 6).

A ``playlist`` (or unknown) url shouldn't reach single-resolve — raise. The
download workflow re-resolves the stream/MP4 separately, so no download plan is
threaded from here.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from app.services.media.parsers.soda_music.soda_api import SodaApiClient, SodaApiError
from app.services.media.parsers.soda_music.soda_parser import parse_track
from app.services.media.parsers.soda_music.ugc_enrich import (
    enrich_ugc_with_douyin_stats,
)
from app.services.media.parsers.soda_music.ugc_formatter import format_ugc_video


async def _classify(url: str, client: Any) -> tuple[str, str]:
    """Classify a qishui url into ``(kind, content_id)``.

    Fast path: an explicit ``track_id`` / ``ugc_video_id`` in the query. Else
    HEAD-follow the short link and classify the landing url.
    """
    query = parse_qs(urlparse(url).query)
    if "track_id" in query:
        return "track", query["track_id"][0]
    if "ugc_video_id" in query:
        return "ugc_video", query["ugc_video_id"][0]
    content = await client.resolve_short_link(url)
    if content is None:
        raise SodaApiError(f"could not classify qishui url: {url}")
    return content.kind, content.content_id


async def resolve_qishui_metadata(
    *,
    url: str,
    user_id: str | None,
    want_quality: str = "lossless",
    api: Any | None = None,
) -> dict[str, Any]:
    """Resolve a qishui URL to a parsed_media-shaped dict (metadata only)."""
    client = api
    if client is None:
        from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie

        cookie = await get_soda_cookie(user_id)
        client = SodaApiClient(cookie=cookie)

    kind, content_id = await _classify(url, client)

    if kind == "track":
        parsed_data, _plan = await parse_track(
            client, track_id=content_id, want_quality=want_quality, original_url=url
        )
        return parsed_data

    if kind == "ugc_video":
        vo = await client.get_ugc_video(content_id)
        pd = format_ugc_video(vo, ugc_video_id=content_id, original_url=url)
        # The qishui share page has no engagement stats / publish time, but the
        # UGC video_id IS a douyin aweme_id. Best-effort enrich from douyin
        # (never fails the parse).
        pd = await enrich_ugc_with_douyin_stats(
            pd, video_id=content_id, user_id=user_id
        )
        return pd

    raise SodaApiError(f"qishui {kind} not supported for single resolve")
