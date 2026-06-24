from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin
from app.services.topics.adapters.base import HotspotCandidate, make_dedup_key
from app.services.topics.heat import compute_heat

# Columns the free-text search matches against (title + body + AI summary + feed).
_SEARCH_COLUMNS = ("title", "content_original", "ai_summary", "source_label")
# Cap rank_timeline length so a long-lived item's history stays bounded.
_MAX_TIMELINE = 20


def sanitize_search(raw: Optional[str]) -> str:
    """Make a user term safe to embed in a PostgREST ``or_`` ilike filter.

    Drops the ``,()`` characters that delimit PostgREST filter syntax and
    escapes the ``%`` / ``_`` LIKE wildcards. Returns ``""`` when nothing
    usable remains (caller then skips the filter). Capped to bound query size.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return ""
    for ch in (",", "(", ")"):
        cleaned = cleaned.replace(ch, " ")
    cleaned = cleaned.replace("%", r"\%").replace("_", r"\_")
    return " ".join(cleaned.split())[:100]


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
            captured = c.captured_at.isoformat() if c.captured_at else None
            # Seed the timeline with this observation; merged across fetches by
            # upsert_with_heat. rank may be None (unranked source).
            point = {"rank": c.rank, "at": captured}
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
                    "captured_at": captured,
                    "dedup_key": make_dedup_key(source_id, url=c.url, title=c.title),
                    "rank_timeline": [point],
                    "heat": compute_heat([point]),
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

    async def upsert_with_heat(self, rows: list[dict[str, Any]]) -> int:
        """Ingest a fetch batch, accumulating heat over time.

        For a row already seen (same dedup_key) we APPEND its new timeline point
        to the stored history and recompute ``heat`` — never touching the
        LLM enrichment (score/reason/ai_summary/category/tags) or first-seen
        captured_at. New rows are inserted as-is. Returns the count of NEW rows.
        """
        if not rows:
            return 0
        client = await self._client()
        keys = [r["dedup_key"] for r in rows]
        try:
            existing = (
                await client.table(self.TABLE)
                .select("id, dedup_key, rank_timeline")
                .in_("dedup_key", keys)
                .execute()
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"hotspots heat preload failed: {e}")
            return 0

        by_key = {str(r["dedup_key"]): r for r in (existing.data or [])}
        new_rows: list[dict[str, Any]] = []
        for row in rows:
            prior = by_key.get(row["dedup_key"])
            if not prior:
                new_rows.append(row)
                continue
            # Append this observation to the stored history (bounded), recompute.
            merged = (prior.get("rank_timeline") or [])[-(_MAX_TIMELINE - 1) :]
            merged = merged + (row.get("rank_timeline") or [])
            patch = {"rank_timeline": merged, "heat": compute_heat(merged)}
            try:
                await client.table(self.TABLE).update(patch).eq(
                    "id", prior["id"]
                ).execute()
            except Exception as e:  # noqa: BLE001
                logger.error(f"hotspots heat update failed for {prior['id']}: {e}")

        if new_rows:
            try:
                await client.table(self.TABLE).upsert(
                    new_rows, on_conflict="dedup_key", ignore_duplicates=True
                ).execute()
            except Exception as e:  # noqa: BLE001
                logger.error(f"hotspots heat insert failed: {e}")
                return 0
        return len(new_rows)

    async def list_for_date(
        self,
        day: Optional[str],
        category: Optional[str],
        limit: int = 100,
        q: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        client = await self._client()
        query = client.table(self.TABLE).select("*")
        if day:
            query = query.gte("captured_at", f"{day}T00:00:00Z").lte(
                "captured_at", f"{day}T23:59:59Z"
            )
        if category and category != "all":
            query = query.eq("category", category)
        term = sanitize_search(q)
        if term:
            like = f"%{term}%"
            query = query.or_(
                ",".join(f"{col}.ilike.{like}" for col in _SEARCH_COLUMNS)
            )
        result = await query.order("captured_at", desc=True).limit(limit).execute()
        return result.data or []

    async def list_by_ids(
        self, hotspot_ids: list[str], limit: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch specific hotspots (for the saved/hidden views, which span all
        dates). Ordered newest-first. Empty id list short-circuits."""
        if not hotspot_ids:
            return []
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("*")
            .in_("id", hotspot_ids)
            .order("captured_at", desc=True)
            .limit(limit)
            .execute()
        )
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

    async def list_unscored(self, limit: int = 60) -> list[dict[str, Any]]:
        """Most-recent hotspots with no AI score yet (score IS NULL)."""
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("id, source_label, title, content_original")
            .is_("score", "null")
            .order("captured_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def list_unembedded(self, limit: int = 40) -> list[dict[str, Any]]:
        """Most-recent hotspots with no embedding yet (embedding IS NULL)."""
        client = await self._client()
        result = (
            await client.table(self.TABLE)
            .select("id, title, ai_summary, content_original")
            .is_("embedding", "null")
            .order("captured_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    async def patch_embedding(self, hotspot_id: str, embedding: list[float]) -> None:
        """Store a hotspot's embedding vector. The vector is sent as a pgvector
        text literal ``[v1,v2,...]`` which PostgREST casts to the vector column."""
        if not embedding:
            return
        literal = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
        client = await self._client()
        try:
            await client.table(self.TABLE).update({"embedding": literal}).eq(
                "id", hotspot_id
            ).execute()
        except Exception as e:  # noqa: BLE001
            logger.error(f"patch_embedding failed for {hotspot_id}: {e}")

    async def patch_enrichment(self, hotspot_id: str, enrichment: dict) -> None:
        """Write AI enrichment (score/reason/ai_summary/category/tags). Skips None."""
        patch = {
            k: enrichment[k]
            for k in ("score", "reason", "ai_summary", "category", "tags")
            if enrichment.get(k) is not None
        }
        if not patch:
            return
        client = await self._client()
        try:
            await client.table(self.TABLE).update(patch).eq("id", hotspot_id).execute()
        except Exception as e:  # noqa: BLE001
            logger.error(f"patch_enrichment failed for {hotspot_id}: {e}")

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
