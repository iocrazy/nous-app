"""On-demand lyrics top-up.

Lyrics live in ``parsed_media.metadata.lyrics`` ({lrc, lines}), written at parse
time. A track can lack lyrics because it was parsed before lyric support existed
(legacy) or the source returned none at parse time. This service re-fetches the
lyric from the source and merges it back into metadata — fetch what's missing,
without re-downloading audio.

Dispatch is by ``source_platform`` so other platforms (netease, qq, ...) can be
added later. Today only ``qishui`` (Soda) is wired; Soda lyrics come from the
lightweight ``track_v2`` detail call (no PlayAuth needed).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.repositories.media_repository import MediaRepository
from app.services.media.parsers.soda_music.lyrics import lyrics_payload_from_track


class LyricsFetchUnsupported(Exception):
    """Raised when the track's source platform has no lyrics-fetch path yet."""


async def _fetch_qishui_lyrics(platform_id: str, user_id: str) -> dict[str, Any]:
    """Fetch + parse lyrics for a Soda track via the track_v2 detail call."""
    from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie
    from app.services.media.parsers.soda_music.soda_api import SodaApiClient

    cookie = await get_soda_cookie(user_id)
    data = await SodaApiClient(cookie=cookie).get_track_v2(platform_id)
    track = data.get("track", {}) or {}
    return lyrics_payload_from_track(track)


async def fetch_and_persist_lyrics(media_id: str, user_id: str) -> dict[str, Any]:
    """Re-fetch lyrics for ``media_id`` and persist into metadata.lyrics.

    Returns the ``{"lrc", "lines"}`` payload (empty when the track genuinely has
    no lyrics, e.g. instrumental). Raises ``LyricsFetchUnsupported`` for platforms
    without a fetch path, ``LookupError`` when the media row is missing.
    """
    repo = MediaRepository()
    row = await repo.get_by_id(media_id)
    if row is None:
        raise LookupError(f"media not found: {media_id}")

    platform = (row.get("source_platform") or "").lower()
    if platform == "qishui":
        payload = await _fetch_qishui_lyrics(row["platform_id"], user_id)
    else:
        raise LyricsFetchUnsupported(platform or "unknown")

    # Merge into metadata (jsonb is replaced wholesale on update, so preserve
    # the rest of the object and only swap the lyrics key).
    merged = {**(row.get("metadata") or {}), "lyrics": payload}
    await repo.update(row["platform_id"], {"metadata": merged})
    logger.info(
        "lyrics top-up: media={} platform={} lines={}",
        media_id,
        platform,
        len(payload.get("lines") or []),
    )
    return payload
