from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin
from app.services.topics.adapters.base import HotspotCandidate, make_dedup_key


class HotspotsRepository:
    TABLE = "hotspots"

    async def _client(self):
        return await get_async_supabase_admin()

    def build_rows(
        self,
        candidates: list[HotspotCandidate],
        *,
        source_id: str,
        category: Optional[str],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for c in candidates:
            rows.append(
                {
                    "user_id": None,  # Phase 1: global
                    "source_id": source_id,
                    "source_label": c.source_label,
                    "title": c.title,
                    "url": c.url,
                    "origin_url": c.origin_url,
                    "content_original": c.content,
                    "category": category,
                    "media_url": c.media_url,
                    "cover_url": c.cover_url,
                    "captured_at": c.captured_at.isoformat() if c.captured_at else None,
                    "dedup_key": make_dedup_key(source_id, url=c.url, title=c.title),
                }
            )
        return rows

    async def upsert_ignore(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        client = await self._client()
        try:
            result = (
                await client.table(self.TABLE)
                .upsert(rows, on_conflict="dedup_key", ignore_duplicates=True)
                .execute()
            )
            return len(result.data or [])
        except Exception as e:  # noqa: BLE001
            logger.error(f"hotspots upsert failed: {e}")
            return 0

    async def list_for_date(
        self,
        day: Optional[str],
        category: Optional[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        client = await self._client()
        q = client.table(self.TABLE).select("*")
        if day:
            q = q.gte("captured_at", f"{day}T00:00:00Z").lte(
                "captured_at", f"{day}T23:59:59Z"
            )
        if category and category != "all":
            q = q.eq("category", category)
        result = await q.order("captured_at", desc=True).limit(limit).execute()
        return result.data or []

    async def get_by_id(self, hotspot_id: str) -> dict | None:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .eq("id", hotspot_id)
            .limit(1)
            .execute()
        )
        return (result.data or [None])[0]

    async def distinct_dates(self, limit_days: int = 60) -> list[str]:
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("captured_at")
            .order("captured_at", desc=True)
            .limit(2000)
            .execute()
        )
        seen: list[str] = []
        for r in result.data or []:
            d = (r.get("captured_at") or "")[:10]
            if d and d not in seen:
                seen.append(d)
            if len(seen) >= limit_days:
                break
        return seen
