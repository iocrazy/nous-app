"""Topic-group clustering data access (pgvector via the SQLAlchemy engine).

Vector similarity (``<=>`` cosine distance) is a Postgres/pgvector operation, so
this repo speaks SQL through ``app.db.engine`` rather than PostgREST. Vectors
cross the boundary as pgvector text literals (``[v1,v2,...]``) cast with
``::vector``. BIGINT ids are bound as ints (asyncpg int8 codec is strict).
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from app.db import engine as db_engine


def _to_int(v: Any) -> int:
    """Coerce a snowflake id (str/int) to int for BIGINT binds."""
    return int(str(v))


class TopicGroupRepository:
    async def list_unclustered(
        self, *, window_hours: int = 48, limit: int = 60
    ) -> list[dict[str, Any]]:
        """Embedded hotspots in the window that aren't in a group yet.

        Returns ``id`` (text), ``source_id`` (text), ``title``, and ``vec``
        (the embedding as a pgvector text literal)."""
        rows = await db_engine.fetch_all(
            """
            SELECT id::text AS id, source_id::text AS source_id,
                   title, embedding::text AS vec
              FROM public.hotspots
             WHERE embedding IS NOT NULL
               AND topic_group_id IS NULL
               AND captured_at >= now() - make_interval(hours => :win)
             ORDER BY captured_at DESC
             LIMIT :lim
            """,
            {"win": window_hours, "lim": limit},
        )
        return rows or []

    async def nearest_group(
        self, vec: str, *, window_hours: int = 48
    ) -> Optional[dict[str, Any]]:
        """Most-similar active group to ``vec`` (a pgvector text literal).

        Returns ``{"id": text, "sim": float}`` or None when no active group
        exists. ``sim`` is cosine similarity (1 - cosine distance)."""
        return await db_engine.fetch_one(
            """
            SELECT id::text AS id, 1 - (embedding <=> CAST(:vec AS vector)) AS sim
              FROM public.topic_groups
             WHERE embedding IS NOT NULL
               AND last_seen >= now() - make_interval(hours => :win)
             ORDER BY embedding <=> CAST(:vec AS vector)
             LIMIT 1
            """,
            {"vec": vec, "win": window_hours},
        )

    async def create_group(self, *, label: str, vec: str) -> Optional[str]:
        """Create a group seeded with this hotspot's centroid. Returns its id."""
        gid = await db_engine.execute_returning_val(
            """
            INSERT INTO public.topic_groups
                (label, embedding, source_count, first_seen, last_seen)
            VALUES (:label, CAST(:vec AS vector), 1, now(), now())
            RETURNING id::text
            """,
            {"label": (label or "")[:200], "vec": vec},
        )
        return str(gid) if gid is not None else None

    async def assign_hotspot(self, hotspot_id: Any, group_id: Any) -> None:
        await db_engine.execute(
            "UPDATE public.hotspots SET topic_group_id = :g WHERE id = :h",
            {"g": _to_int(group_id), "h": _to_int(hotspot_id)},
        )

    async def recompute_group(self, group_id: Any) -> None:
        """Refresh a group's cross-source count, member source labels, and
        recency after a new member. The centroid stays the seed member's vector
        (tight threshold makes drift unnecessary). ``source_labels`` is the same
        aggregate as ``source_count`` over the same members — it powers the feed
        tooltip that shows WHICH platforms a cross-source topic appears on."""
        gid = _to_int(group_id)
        await db_engine.execute(
            """
            UPDATE public.topic_groups SET
                source_count = (
                    SELECT count(DISTINCT source_id)
                      FROM public.hotspots WHERE topic_group_id = :g
                ),
                source_labels = COALESCE((
                    SELECT array_agg(DISTINCT source_label ORDER BY source_label)
                      FROM public.hotspots
                     WHERE topic_group_id = :g
                       AND source_label IS NOT NULL
                       AND source_label <> ''
                ), '{}'),
                last_seen = now()
            WHERE id = :g
            """,
            {"g": gid},
        )


def get_topic_group_repository() -> "TopicGroupRepository":
    return TopicGroupRepository()


__all__ = ["TopicGroupRepository", "get_topic_group_repository", "_to_int"]


# Defensive: surface engine-unconfigured early with a clear message rather than
# a deep AttributeError, mirroring governance's degrade-gracefully posture.
def _engine_ready() -> bool:
    try:
        return db_engine.is_configured()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[topic-cluster] engine check failed: {e}")
        return False
