"""Entry helper: a qishui URL → parsed_media-shaped dict.

Classifies the URL (track_id in query, or short link needing a HEAD redirect),
then resolves track metadata. UGC-video links are out of scope for Phase 2
(deferred to Phase 6) — raise a clear error. The download workflow re-resolves
the stream separately, so no download plan is threaded from here.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from app.services.media.parsers.soda_music.soda_api import SodaApiClient, SodaApiError
from app.services.media.parsers.soda_music.soda_parser import parse_track


async def _track_id_from_url(url: str, client: Any) -> str:
    query = parse_qs(urlparse(url).query)
    if "track_id" in query:
        return query["track_id"][0]
    content = await client.resolve_short_link(url)
    if content is None:
        raise SodaApiError(f"could not classify qishui url: {url}")
    if content.kind != "track":
        raise SodaApiError(
            f"qishui {content.kind} not supported in Phase 2 (UGC video is Phase 6)"
        )
    return content.content_id


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

    track_id = await _track_id_from_url(url, client)
    parsed_data, _plan = await parse_track(
        client, track_id=track_id, want_quality=want_quality, original_url=url
    )
    return parsed_data
