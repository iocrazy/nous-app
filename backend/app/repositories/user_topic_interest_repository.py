"""Per-user interest profile + interest-ranked hotspot retrieval.

Vector ops (embedding storage, cosine ranking) go through the SQLAlchemy engine
+ pgvector. Interest embeddings cross the boundary as ``CAST(:vec AS vector)``
text literals — NOT ``:vec::vector`` (SQLAlchemy text() leaves a bind param
unbound when it's immediately followed by ::, see mig312). NEVER reads env.
"""

from __future__ import annotations

from typing import Any, Optional

from app.db import engine as db_engine


class UserTopicInterestRepository:
    async def get_interest(self, user_id: str) -> Optional[dict[str, Any]]:
        """The user's interest row, or None if unset. ``has_embedding`` tells the
        caller whether interest-ranking is possible yet."""
        return await db_engine.fetch_one(
            """
            SELECT interest_text,
                   (embedding IS NOT NULL) AS has_embedding,
                   updated_at::text AS updated_at
              FROM public.user_topic_interests
             WHERE user_id = :uid
            """,
            {"uid": user_id},
        )

    async def set_interest(
        self, user_id: str, *, interest_text: str, vec: Optional[str]
    ) -> None:
        """Upsert the user's interest text + embedding (a pgvector text literal,
        or None when the embedding provider is unconfigured/failed)."""
        await db_engine.execute(
            """
            INSERT INTO public.user_topic_interests
                (user_id, interest_text, embedding, updated_at)
            VALUES (
                :uid, :txt,
                CASE WHEN :vec IS NULL THEN NULL ELSE CAST(:vec AS vector) END,
                now()
            )
            ON CONFLICT (user_id) DO UPDATE SET
                interest_text = EXCLUDED.interest_text,
                embedding = EXCLUDED.embedding,
                updated_at = now()
            """,
            {"uid": user_id, "txt": interest_text, "vec": vec},
        )

    async def rank_hotspot_ids(
        self, user_id: str, *, window_hours: int = 72, limit: int = 100
    ) -> list[str]:
        """Hotspot ids in the window for the user's "For You" feed.

        The interest text is treated as KEYWORDS (whitespace-separated): a
        hotspot must match at least one keyword in its title/body to qualify —
        that's the per-user filter. Within the matched set, ranking prefers
        embedding cosine similarity to the interest vector (semantic boost), and
        falls back to recency when embeddings aren't available. So it works even
        when the embedding provider is unconfigured (keyword-only), and gets
        sharper when embeddings exist. Empty interest_text → no keyword filter
        (pure embedding/recency rank, the original behaviour). Empty list when
        nothing matches or no interest is set."""
        rows = await db_engine.fetch_all(
            """
            WITH me AS (
                SELECT interest_text,
                       embedding,
                       regexp_split_to_array(
                           lower(trim(coalesce(interest_text, ''))), '\\s+'
                       ) AS words
                  FROM public.user_topic_interests
                 WHERE user_id = :uid
            )
            SELECT h.id::text AS id
              FROM public.hotspots h, me
             WHERE h.captured_at >= now() - make_interval(hours => :win)
               AND (
                   -- no keywords set → don't filter (original embedding rank)
                   me.words IS NULL
                   OR array_length(me.words, 1) IS NULL
                   OR (array_length(me.words, 1) = 1 AND me.words[1] = '')
                   -- otherwise keep hotspots matching any keyword in title/body
                   OR EXISTS (
                       SELECT 1 FROM unnest(me.words) AS w
                        WHERE w <> ''
                          AND (
                              lower(h.title) LIKE '%' || w || '%'
                              OR lower(coalesce(h.content_original, '')) LIKE '%' || w || '%'
                          )
                   )
               )
             ORDER BY
               CASE
                   WHEN me.embedding IS NOT NULL AND h.embedding IS NOT NULL
                   THEN (h.embedding <=> me.embedding)
               END NULLS LAST,
               h.captured_at DESC
             LIMIT :lim
            """,
            {"uid": user_id, "win": window_hours, "lim": limit},
        )
        return [r["id"] for r in (rows or [])]


def get_user_topic_interest_repository() -> "UserTopicInterestRepository":
    return UserTopicInterestRepository()


__all__ = [
    "UserTopicInterestRepository",
    "get_user_topic_interest_repository",
]
