"""Process-local TTL cache for ``/media/{id}`` path resolution.

Extracted from ``main.py`` so version-content write paths can INVALIDATE a
resource's entry when its current bytes change (overwrite / new version /
set-current). Without invalidation the detail page (whose primary URL is
``/media/{id}``) serves stale content for up to ``TTL_SECONDS`` after an edit —
the exact "save, reload, still old" symptom the text-resource editor exposed.

Keyed by ``(media_id, file_type)``; ``invalidate(media_id)`` drops every
file_type for that id. Module-level dict = one cache per worker process
(same lifetime/semantics as the previous closure-local dict in main.py).
"""

from __future__ import annotations

import time
from typing import NamedTuple, Optional

TTL_SECONDS = 300  # 5 minutes


class MediaCacheEntry(NamedTuple):
    file_path: str
    creator_id: Optional[str]
    team_ids: tuple[str, ...]
    cached_at: float


_cache: dict[tuple[str, str], MediaCacheEntry] = {}


def get(media_id: str, file_type: str) -> Optional[MediaCacheEntry]:
    """Return a live (non-expired) entry, or None. Evicts on expiry."""
    key = (str(media_id), file_type)
    entry = _cache.get(key)
    if entry is None:
        return None
    if time.time() - entry.cached_at >= TTL_SECONDS:
        del _cache[key]
        return None
    return entry


def put(
    media_id: str,
    file_type: str,
    file_path: str,
    creator_id: Optional[str],
    team_ids: tuple[str, ...],
) -> MediaCacheEntry:
    entry = MediaCacheEntry(file_path, creator_id, team_ids, time.time())
    _cache[(str(media_id), file_type)] = entry
    return entry


def invalidate(media_id: str) -> int:
    """Drop every file_type entry for ``media_id``. Returns the count removed.

    Accepts int or str ids — the key is always the stringified id (the value
    that arrives in the ``/media/{id}`` URL path)."""
    mid = str(media_id)
    keys = [k for k in _cache if k[0] == mid]
    for k in keys:
        del _cache[k]
    return len(keys)


def clear() -> None:
    """Drop all entries (test helper / full reset)."""
    _cache.clear()
