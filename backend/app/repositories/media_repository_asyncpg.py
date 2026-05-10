"""asyncpg + Supavisor implementation of MediaRepository.

Phase 4 of the supabase-py → asyncpg migration. Lands in slices so
each PR stays reviewable:

  - Phase 4a: ``parsed_media`` table CRUD (6 methods) — this file.
    + 9 methods that wrap these (check_*, get_music_data, mark_*,
    mark_download_failed) automatically benefit via MRO since they
    call ``self.get_by_platform_id`` / ``self.update`` internally.
  - Phase 4b: bulk + list methods (mark_stale_downloads_failed,
    get_pending_downloads, get_all, get_user_media_list) — pending
  - Phase 4c: search + statistics — pending

Strategy: same multiple-inheritance Strangler Fig pattern as
``ResourcesRepositoryAsyncpg``. The factory in ``media_repository.py``
returns this subclass when ``USE_ASYNCPG_MEDIA`` is on; unmigrated
methods inherit the legacy supabase-py path via MRO.

Type notes:
  - ``parsed_media.id`` is UUID (not bigint — the videos→parsed_media
    rename in migration 066 didn't change the type, and migration 051
    skipped this table). asyncpg's uuid codec accepts str input, so
    no coercion helper needed (unlike Phase 3's ``_bigint`` for
    ``resources.id``).
  - ``parsed_media.platform_id`` is TEXT — str works directly.

Behavioural parity vs legacy:
  - Same return shapes (dict | None, dict, bool)
  - Same enum / datetime preprocessing on create + update
  - Same elaborate APIError-style logging on update (now adapted to
    asyncpg.exceptions; both surface useful info on .args[0])
  - INFO log on every successful create / update / delete
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from loguru import logger

from app.core.enums import DownloadStatus
from app.db.repository_base import AsyncpgRepository
from app.repositories.media_repository import MediaRepository


def _normalize_for_pg(data: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce DownloadStatus enum + datetime values to wire-friendly
    types for the parsed_media table. Returns a NEW dict — caller's
    input is not mutated (mirrors immutability rule)."""
    out = dict(data)
    for field in (
        "video_download_status",
        "music_download_status",
        "cover_download_status",
        "image_download_status",
    ):
        v = out.get(field)
        if isinstance(v, DownloadStatus):
            out[field] = v.value
    for field in ("published_at", "download_time"):
        v = out.get(field)
        if isinstance(v, datetime):
            out[field] = v.isoformat()
    return out


class MediaRepositoryAsyncpg(AsyncpgRepository, MediaRepository):
    """asyncpg-backed MediaRepository for the ``parsed_media`` table.

    Overrides 6 hot-path CRUD methods. Wrapper methods (check_*,
    mark_*, get_music_data) inherit unchanged — they call into
    ``self.get_by_platform_id`` / ``self.update`` and Python MRO
    resolves those to the asyncpg overrides automatically."""

    TABLE = "parsed_media"

    # ── Core CRUD ───────────────────────────────────────────────────

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            normalized = _normalize_for_pg(data)
            row = await self.insert(**normalized)
            logger.info(f"Created parsed_media: {data.get('platform_id')}")
            return row or {}
        except Exception as e:
            logger.error(f"Failed to create parsed_media: {e}")
            raise

    async def get_by_platform_id(self, platform_id: str) -> Optional[Dict[str, Any]]:
        """Lookup by external platform_id (text). Used by every
        parse + dedup probe — the most-called read on this table."""
        return await self.fetch_one(
            "SELECT * FROM parsed_media WHERE platform_id = $1 LIMIT 1",
            platform_id,
        )

    async def get_by_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        """Lookup by primary key (UUID). asyncpg's uuid codec accepts
        str input — no coercion needed."""
        return await self.fetch_one(
            "SELECT * FROM parsed_media WHERE id = $1 LIMIT 1",
            media_id,
        )

    async def update(
        self, platform_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """UPDATE parsed_media by platform_id. Sets updated_at to now
        before binding (matches legacy)."""
        try:
            normalized = _normalize_for_pg(data)
            normalized["updated_at"] = datetime.now().isoformat()

            cols = list(normalized.keys())
            set_pairs = ", ".join(f'"{c}" = ${i + 1}' for i, c in enumerate(cols))
            sql = (
                f'UPDATE "parsed_media" SET {set_pairs} '
                f"WHERE platform_id = ${len(cols) + 1} RETURNING *"
            )
            row = await self.fetch_one(sql, *normalized.values(), platform_id)
            logger.info(f"Updated parsed_media: {platform_id}")
            return row
        except Exception as e:
            # Mirror legacy's verbose APIError unwrapping. asyncpg
            # exceptions don't have .message/.code/.details/.hint —
            # they put info in .args[0] and __dict__ — so we surface
            # whatever we can find. The shape stays close enough that
            # log-grepping by Bug D pattern still works.
            logger.error(
                f"Failed to update parsed_media platform_id={platform_id}: "
                f"type={type(e).__name__} repr={e!r} "
                f"args={getattr(e, 'args', None)!r} "
                f"detail={getattr(e, 'detail', None)!r} "
                f"sqlstate={getattr(e, 'sqlstate', None)!r}"
            )
            raise

    async def delete(self, platform_id: str) -> bool:
        try:
            await self.execute(
                "DELETE FROM parsed_media WHERE platform_id = $1",
                platform_id,
            )
            logger.info(f"Deleted parsed_media: {platform_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete parsed_media: {e}")
            return False

    async def get_downloaded_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Cross-user dedup probe — find a fully-downloaded record
        for this platform_id from ANY user. Same projection as
        legacy (id, download_path, storage_size, cover_download_path,
        source_platform, platform_id) — keep fields stable for callers."""
        return await self.fetch_one(
            "SELECT id, download_path, storage_size, cover_download_path, "
            "       source_platform, platform_id "
            "FROM parsed_media "
            "WHERE platform_id = $1 "
            "  AND video_download_status = $2 "
            "  AND download_path IS NOT NULL "
            "LIMIT 1",
            platform_id,
            DownloadStatus.COMPLETED.value,
        )


__all__ = ["MediaRepositoryAsyncpg"]
