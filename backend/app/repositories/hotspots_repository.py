from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import or_, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import Hotspots, TopicGroups
from app.services.topics.adapters.base import HotspotCandidate, make_dedup_key
from app.services.topics.heat import compute_heat

# Columns the free-text search matches against (title + body + AI summary + feed).
_SEARCH_COLUMNS = ("title", "content_original", "ai_summary", "source_label")
# Cap rank_timeline length so a long-lived item's history stays bounded.
_MAX_TIMELINE = 20

# Every hotspot column EXCEPT the pgvector embedding — the old ``select("*")``
# returned embedding too, but no consumer ever reads it (the embed pass uses
# list_unembedded's explicit projection), and returning a 2048-float vector on
# every feed row is pure waste. Dropping it is invisible to every caller.
_HOTSPOT_READ_COLS = [c for c in Hotspots.__table__.columns if c.name != "embedding"]


def sanitize_search(raw: Optional[str]) -> str:
    """Make a user term safe to embed in a free-text ILIKE filter.

    Drops the ``,()`` characters (historically PostgREST ``or_`` delimiters)
    and escapes the ``%`` / ``_`` LIKE wildcards. Returns ``""`` when nothing
    usable remains (caller then skips the filter). Capped to bound query size.
    """
    cleaned = (raw or "").strip()
    if not cleaned:
        return ""
    for ch in (",", "(", ")"):
        cleaned = cleaned.replace(ch, " ")
    cleaned = cleaned.replace("%", r"\%").replace("_", r"\_")
    return " ".join(cleaned.split())[:100]


def _coerce(val: Any) -> Any:
    """Match the old PostgREST value types: uuid → str, datetime → ISO str,
    Decimal → float. Native ``tags`` (text[]) list, ``rank_timeline`` /
    ``score_dims`` (jsonb) dict/list pass through unchanged."""
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, datetime.datetime):
        return val.isoformat()
    if isinstance(val, decimal.Decimal):
        return float(val)
    return val


def _shape_joined_row(m: Any) -> Dict[str, Any]:
    """Turn a hotspot+topic_group joined RowMapping into the PostgREST-embed
    shape: the hotspot columns plus a nested ``topic_groups`` object
    ``{"source_count": N, "source_labels": [...]}`` (or ``None`` when the
    hotspot isn't clustered)."""
    source_count = m.get("source_count")
    source_labels = m.get("source_labels")
    out: Dict[str, Any] = {}
    for key, val in m.items():
        if key in ("source_count", "source_labels"):
            continue
        out[key] = _coerce(val)
    # source_count is NOT NULL on topic_groups, so it is None only when the
    # LEFT JOIN found no group → matches PostgREST's null embed.
    out["topic_groups"] = (
        {
            "source_count": source_count,
            "source_labels": list(source_labels) if source_labels is not None else [],
        }
        if source_count is not None
        else None
    )
    return out


def _plain_row(m: Any) -> Dict[str, Any]:
    """Coerce a non-joined RowMapping (or dict) to the REST value types."""
    return {key: _coerce(val) for key, val in m.items()}


class HotspotsRepository:
    TABLE = "hotspots"

    def build_rows(
        self,
        candidates: list[HotspotCandidate],
        *,
        source_id: str,
        category: Optional[str],
        source_label: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        # source_label override: when one upstream fetch is shared across several
        # source rows (dedup by kind+config), each row stamps ITS OWN name here
        # instead of the representative's (which the adapter baked into c).
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
                    "source_label": source_label or c.source_label,
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

    def _prepare_insert_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Coerce a build_rows dict for an asyncpg insert: snowflake source_id
        str → int, ISO ``captured_at`` string → datetime (asyncpg-strict)."""
        out = dict(row)
        if out.get("source_id") is not None:
            out["source_id"] = _bigint(out["source_id"])
        cap = out.get("captured_at")
        if isinstance(cap, str):
            out["captured_at"] = datetime.datetime.fromisoformat(
                cap.replace("Z", "+00:00")
            )
        return out

    async def upsert_ignore(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        try:
            prepared = [self._prepare_insert_row(r) for r in rows]
            stmt = (
                pg_insert(Hotspots)
                .values(prepared)
                .on_conflict_do_nothing(index_elements=["dedup_key"])
                .returning(Hotspots.id)
            )
            async with write_scope() as session:
                result = await session.execute(stmt)
                return len(result.all())
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
        keys = [r["dedup_key"] for r in rows]
        try:
            async with read_scope() as session:
                existing_rows = (
                    await session.execute(
                        select(
                            Hotspots.id,
                            Hotspots.dedup_key,
                            Hotspots.rank_timeline,
                        ).where(Hotspots.dedup_key.in_(keys))
                    )
                ).all()
        except Exception as e:  # noqa: BLE001
            logger.error(f"hotspots heat preload failed: {e}")
            return 0

        by_key = {str(r.dedup_key): r for r in existing_rows}
        new_rows: list[dict[str, Any]] = []
        for row in rows:
            prior = by_key.get(row["dedup_key"])
            if not prior:
                new_rows.append(row)
                continue
            # Append this observation to the stored history (bounded), recompute.
            merged = (prior.rank_timeline or [])[-(_MAX_TIMELINE - 1) :]
            merged = merged + (row.get("rank_timeline") or [])
            patch = {"rank_timeline": merged, "heat": compute_heat(merged)}
            try:
                async with write_scope() as session:
                    await session.execute(
                        sa_update(Hotspots)
                        .where(Hotspots.id == prior.id)
                        .values(**patch)
                    )
            except Exception as e:  # noqa: BLE001
                logger.error(f"hotspots heat update failed for {prior.id}: {e}")

        if new_rows:
            try:
                prepared = [self._prepare_insert_row(r) for r in new_rows]
                async with write_scope() as session:
                    await session.execute(
                        pg_insert(Hotspots)
                        .values(prepared)
                        .on_conflict_do_nothing(index_elements=["dedup_key"])
                    )
            except Exception as e:  # noqa: BLE001
                logger.error(f"hotspots heat insert failed: {e}")
                return 0
        return len(new_rows)

    def _joined_select(self):
        """SELECT the hotspot columns (minus embedding) + the embedded group's
        source_count / source_labels via a LEFT JOIN on topic_group_id."""
        return (
            select(
                *_HOTSPOT_READ_COLS,
                TopicGroups.source_count,
                TopicGroups.source_labels,
            )
            .select_from(Hotspots)
            .outerjoin(TopicGroups, Hotspots.topic_group_id == TopicGroups.id)
        )

    async def list_for_date(
        self,
        day: Optional[str],
        category: Optional[str],
        limit: int = 100,
        q: Optional[str] = None,
        source_ids: Optional[list[str]] = None,
        min_score: Optional[float] = None,
        order_score: bool = False,
        tag_words: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        # source_ids is the caller's visible-source allowlist (system + own,
        # minus hidden). None = no scoping; [] = nothing visible → empty feed.
        # min_score / order_score drive the "Featured" view: a score floor +
        # best-first ordering instead of the default chronological feed.
        # tag_words is the pool-tag filter: keep hotspots whose ``tags`` array
        # overlaps the lower-cased word set (name/name_zh of the picked tags).
        if source_ids is not None and not source_ids:
            return []
        stmt = self._joined_select()
        if source_ids is not None:
            stmt = stmt.where(Hotspots.source_id.in_([_bigint(s) for s in source_ids]))
        if day:
            start = datetime.datetime.fromisoformat(f"{day}T00:00:00+00:00")
            end = datetime.datetime.fromisoformat(f"{day}T23:59:59+00:00")
            stmt = stmt.where(
                Hotspots.captured_at >= start, Hotspots.captured_at <= end
            )
        if category and category != "all":
            stmt = stmt.where(Hotspots.category == category)
        if min_score is not None:
            stmt = stmt.where(Hotspots.score >= min_score)
        if tag_words:
            stmt = stmt.where(Hotspots.tags.overlap([w.lower() for w in tag_words]))
        term = sanitize_search(q)
        if term:
            like = f"%{term}%"
            stmt = stmt.where(
                or_(*[getattr(Hotspots, col).ilike(like) for col in _SEARCH_COLUMNS])
            )
        order_col = Hotspots.score if order_score else Hotspots.captured_at
        stmt = stmt.order_by(order_col.desc()).limit(limit)
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [_shape_joined_row(m) for m in result.mappings().all()]

    async def list_by_ids(
        self,
        hotspot_ids: list[str],
        limit: int = 100,
        source_ids: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """Fetch specific hotspots (for the saved/hidden/For You views, which
        span all dates). Ordered newest-first. Empty id list short-circuits.
        ``source_ids`` (when given) restricts to the caller's visible sources."""
        if not hotspot_ids:
            return []
        if source_ids is not None and not source_ids:
            return []
        stmt = self._joined_select().where(
            Hotspots.id.in_([_bigint(h) for h in hotspot_ids])
        )
        if source_ids is not None:
            stmt = stmt.where(Hotspots.source_id.in_([_bigint(s) for s in source_ids]))
        stmt = stmt.order_by(Hotspots.captured_at.desc()).limit(limit)
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [_shape_joined_row(m) for m in result.mappings().all()]

    async def get_by_id(
        self, hotspot_id: str, source_ids: Optional[list[str]] = None
    ) -> dict | None:
        """A single hotspot. ``source_ids`` (when given) restricts to the
        caller's visible sources, so one user can't open another's private
        hotspot by id."""
        if source_ids is not None and not source_ids:
            return None
        stmt = self._joined_select().where(Hotspots.id == _bigint(hotspot_id))
        if source_ids is not None:
            stmt = stmt.where(Hotspots.source_id.in_([_bigint(s) for s in source_ids]))
        stmt = stmt.limit(1)
        async with read_scope() as session:
            row = (await session.execute(stmt)).mappings().first()
        return _shape_joined_row(row) if row else None

    async def list_unscored(self, limit: int = 60) -> list[dict[str, Any]]:
        """Most-recent hotspots with no AI score yet (score IS NULL)."""
        async with read_scope() as session:
            result = await session.execute(
                select(
                    Hotspots.id,
                    Hotspots.source_id,
                    Hotspots.source_label,
                    Hotspots.title,
                    Hotspots.content_original,
                )
                .where(Hotspots.score.is_(None))
                .order_by(Hotspots.captured_at.desc())
                .limit(limit)
            )
            return [_plain_row(m) for m in result.mappings().all()]

    async def list_unembedded(self, limit: int = 40) -> list[dict[str, Any]]:
        """Most-recent hotspots with no embedding yet (embedding IS NULL)."""
        async with read_scope() as session:
            result = await session.execute(
                select(
                    Hotspots.id,
                    Hotspots.title,
                    Hotspots.ai_summary,
                    Hotspots.content_original,
                )
                .where(Hotspots.embedding.is_(None))
                .order_by(Hotspots.captured_at.desc())
                .limit(limit)
            )
            return [_plain_row(m) for m in result.mappings().all()]

    async def patch_embedding(self, hotspot_id: str, embedding: list[float]) -> None:
        """Store a hotspot's embedding vector. Sent as a pgvector text literal
        ``[v1,v2,...]`` cast to the vector column (same wire form the old
        PostgREST path used) — bypasses the ORM Vector bind processor."""
        if not embedding:
            return
        literal = "[" + ",".join(repr(float(x)) for x in embedding) + "]"
        try:
            async with write_scope() as session:
                await session.execute(
                    text(
                        "UPDATE public.hotspots SET embedding = CAST(:emb AS vector) "
                        "WHERE id = :id"
                    ),
                    {"emb": literal, "id": _bigint(hotspot_id)},
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"patch_embedding failed for {hotspot_id}: {e}")

    async def list_needing_content(
        self, source_ids: list[str], limit: int = 40
    ) -> list[dict[str, Any]]:
        """Hotspots whose body is still empty but that have a real article URL,
        restricted to the given (curated, article-bearing) source ids. Feeds the
        trafilatura content-enrichment pass. Empty source_ids → empty."""
        if not source_ids:
            return []
        async with read_scope() as session:
            result = await session.execute(
                select(Hotspots.id, Hotspots.url, Hotspots.title)
                .where(
                    Hotspots.source_id.in_([_bigint(s) for s in source_ids]),
                    Hotspots.url.is_not(None),
                    or_(
                        Hotspots.content_original.is_(None),
                        Hotspots.content_original == "",
                    ),
                )
                .order_by(Hotspots.captured_at.desc())
                .limit(limit)
            )
            return [_plain_row(m) for m in result.mappings().all()]

    async def patch_content(self, hotspot_id: str, content: str) -> None:
        """Backfill the article body AND clear the embedding so the embed pass
        recomputes the vector from the now-richer text (title + body). Title-only
        embeddings are noisier; re-embedding on real content tightens clustering."""
        if not content or not content.strip():
            return
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_update(Hotspots)
                    .where(Hotspots.id == _bigint(hotspot_id))
                    .values(content_original=content.strip(), embedding=None)
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"patch_content failed for {hotspot_id}: {e}")

    async def patch_enrichment(self, hotspot_id: str, enrichment: dict) -> None:
        """Write AI enrichment (score/reason/ai_summary/category/tags/score_dims).
        Skips None. ``score`` is the code-computed composite; ``score_dims`` holds
        the raw per-dimension scores for later calibration."""
        patch = {
            k: enrichment[k]
            for k in ("score", "reason", "ai_summary", "category", "tags", "score_dims")
            if enrichment.get(k) is not None
        }
        if not patch:
            return
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_update(Hotspots)
                    .where(Hotspots.id == _bigint(hotspot_id))
                    .values(**patch)
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"patch_enrichment failed for {hotspot_id}: {e}")

    async def recent_tag_word_counts(self, days: int = 30) -> dict[str, int]:
        """lower(word) → hotspot count within the window (spec §4.3).

        One aggregate over ``unnest(tags)``; callers match by name/name_zh.
        """
        stmt = text(
            "SELECT lower(w.word) AS word, COUNT(*)::bigint AS cnt "
            "FROM hotspots h, LATERAL unnest(h.tags) AS w(word) "
            "WHERE h.captured_at >= now() - make_interval(days => :days) "
            "GROUP BY lower(w.word)"
        )
        async with read_scope() as session:
            rows = (await session.execute(stmt, {"days": int(days)})).all()
        return {str(r[0]): int(r[1]) for r in rows}

    async def distinct_dates(self, limit_days: int = 60) -> list[str]:
        async with read_scope() as session:
            result = await session.execute(
                select(Hotspots.captured_at)
                .order_by(Hotspots.captured_at.desc())
                .limit(2000)
            )
            rows = result.scalars().all()
        seen: list[str] = []
        for cap in rows:
            d = cap.isoformat()[:10] if cap else ""
            if d and d not in seen:
                seen.append(d)
            if len(seen) >= limit_days:
                break
        return seen


def _bigint(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value))


_repo: HotspotsRepository | None = None


def get_hotspots_repository() -> HotspotsRepository:
    global _repo
    if _repo is None:
        _repo = HotspotsRepository()
    return _repo
