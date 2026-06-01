# backend/app/services/media/parsers/soda_music/ugc_formatter.py
"""Map a qishui UGC ``videoOptions`` dict into a parsed_media-shaped video dict.

Mirrors ``formatter.format_track`` but for a ``media_type='video'`` UGC clip
scraped from the share page (§A.2.1). The MP4 ``url`` is an expiring
``*.douyinvod.com`` direct link; the download workflow re-resolves it at
download time, so it is carried through ``video_download_urls`` only as a hint.
"""

from __future__ import annotations

from typing import Any

from app.core.utils import Utils


def format_ugc_video(
    vo: dict[str, Any], *, ugc_video_id: str, original_url: str
) -> dict[str, Any]:
    """Build the parsed_data dict for a single qishui UGC video."""
    cover = vo.get("coverURL")
    mp4_url = vo.get("url")
    return {
        "platform_id": ugc_video_id,
        "original_url": original_url,
        "source_platform": "qishui",
        "media_type": "video",
        "title": vo.get("videoName") or "untitled",
        "author": vo.get("artistName"),
        "duration": Utils.format_duration(int(vo.get("duration") or 0)),
        "cover_urls": [cover] if cover else None,
        "video_download_urls": [mp4_url] if mp4_url else [],
        "published_at": None,
        "metadata": {
            "ext": "mp4",
            "width": vo.get("width"),
            "height": vo.get("height"),
            "duration_ms": vo.get("duration"),
            "group_download_level": vo.get("group_download_level"),
            "hasCopyright": vo.get("hasCopyright"),
            "ugc_video_id": ugc_video_id,
        },
    }
