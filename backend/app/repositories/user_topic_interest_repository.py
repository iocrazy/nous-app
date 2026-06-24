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
        """Hotspot ids in the window ranked by cosine similarity to the user's
        interest embedding (closest first). Empty when no interest embedding or
        no embedded hotspots."""
        rows = await db_engine.fetch_all(
            """
            SELECT h.id::text AS id
              FROM public.hotspots h
              JOIN public.user_topic_interests i ON i.user_id = :uid
             WHERE i.embedding IS NOT NULL
               AND h.embedding IS NOT NULL
               AND h.captured_at >= now() - make_interval(hours => :win)
             ORDER BY h.embedding <=> i.embedding
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
