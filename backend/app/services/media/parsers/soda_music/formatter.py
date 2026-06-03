# backend/app/services/media/parsers/soda_music/formatter.py
"""Map a Soda `track` dict (from track_v2) into a parsed_media-shaped dict.

Output is consumed by MediaService.save_metadata_only via the MediaCreate
schema (app/schemas/media.py). Soda-specific extras (album, stats, colors,
credits, lyrics, chosen quality) live under `metadata`; engagement counts are
also mirrored to the top-level columns that already exist on parsed_media.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.utils import Utils
from app.services.media.parsers.soda_music.lyrics import lyrics_payload_from_track
from app.services.media.parsers.soda_music.soda_api import cover_url

EXT_BY_FORMAT = {"flac": "flac", "mp4": "m4a", "m4a": "m4a", "aac": "m4a", "mp3": "mp3"}


def _ext_for(chosen: dict[str, Any]) -> str:
    fmt = str(chosen.get("Format") or chosen.get("format") or "").lower()
    return EXT_BY_FORMAT.get(fmt, "m4a")


def _release_datetime(release_date: Any) -> datetime | None:
    """Album release_date is a unix timestamp (seconds) → datetime, else None."""
    try:
        ts = int(release_date)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def format_track(
    track: dict[str, Any], chosen: dict[str, Any], *, original_url: str
) -> dict[str, Any]:
    """Build the parsed_data dict for a single Soda track."""
    artists = track.get("artists") or []
    album = track.get("album") or {}
    stats = track.get("stats") or {}

    cover_urls: list[str] = []
    if album.get("url_cover"):
        cover_urls = [cover_url(album["url_cover"])]

    return {
        "platform_id": str(track.get("id")),
        "original_url": original_url,
        "source_platform": "qishui",
        "media_type": "audio",
        "title": track.get("name") or "untitled",
        "duration": Utils.format_duration(int(track.get("duration") or 0)),
        "published_at": _release_datetime(album.get("release_date")),
        "author": artists[0].get("name") if artists else None,
        "music_name": track.get("name"),
        "cover_urls": cover_urls or None,
        "favorite_count": stats.get("count_collected"),
        "comment_count": stats.get("count_comment"),
        "share_count": stats.get("count_shared"),
        "metadata": {
            "ext": _ext_for(chosen),
            "album": album,
            "artists": artists,
            "stats": stats,
            "colors": track.get("colors") or {},
            "tags": track.get("tags") or [],
            "song_maker_team": track.get("song_maker_team") or {},
            "duration_ms": track.get("duration"),
            "chorus": track.get("chorus") or {},
            "lyrics": lyrics_payload_from_track(track),
            "quality": {
                "Quality": chosen.get("Quality") or chosen.get("quality"),
                "Format": chosen.get("Format") or chosen.get("format"),
                "Bitrate": chosen.get("Bitrate") or chosen.get("bitrate"),
            },
        },
    }
