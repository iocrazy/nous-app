"""Per-user interest profile + interest-ranked hotspot retrieval.

ORM session scopes (read_scope/write_scope); the vector-bearing method bodies
stay SQL (documented exceptions per the convergence doctrine). Interest
embeddings cross the boundary as ``CAST(:vec AS vector)`` text literals — NOT
``:vec::vector`` (SQLAlchemy text() leaves a bind param unbound when it's
immediately followed by ::, see mig312). NEVER reads env.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import Text as SAText
from sqlalchemy import cast, select, text

from app.core.embedding_space import spaces_compatible_sql
from app.db.session import read_scope, write_scope
from app.models import UserTopicInterests


class UserTopicInterestRepository:
    async def get_interest(self, user_id: str) -> Optional[dict[str, Any]]:
        """The user's interest row, or None if unset. ``has_embedding`` tells the
        caller whether interest-ranking is possible yet."""
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(
                            UserTopicInterests.interest_text,
                            UserTopicInterests.embedding.is_not(None).label(
                                "has_embedding"
                            ),
                            cast(UserTopicInterests.updated_at, SAText).label(
                                "updated_at"
                            ),
                        ).where(UserTopicInterests.user_id == user_id)
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    async def set_interest(
        self,
        user_id: str,
        *,
        interest_text: str,
        vec: Optional[str],
        embedding_model: Optional[str] = None,
    ) -> None:
        """Upsert the user's interest text + embedding (a pgvector text literal,
        or None when the embedding provider is unconfigured/failed) + the
        model that produced it (its space; None with a None vector).

        SQL body kept: the vector CAST is the semantics."""
        # Every ``vec`` bind is typed as text first. A bare ``IS NULL`` on it
        # is untypeable for asyncpg (AmbiguousParameterError), so this upsert
        # failed on every call until the 490 replay caught it. A NULL text
        # casts to a NULL vector, so no CASE is needed for the vector itself.
        async with write_scope() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO public.user_topic_interests
                        (user_id, interest_text, embedding, embedding_model,
                         updated_at)
                    VALUES (
                        :uid, :txt,
                        CAST(CAST(:vec AS text) AS vector),
                        CASE WHEN CAST(:vec AS text) IS NULL THEN NULL
                             ELSE CAST(:model AS text) END,
                        now()
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        interest_text = EXCLUDED.interest_text,
                        embedding = EXCLUDED.embedding,
                        embedding_model = EXCLUDED.embedding_model,
                        updated_at = now()
                    """
                ),
                {
                    "uid": user_id,
                    "txt": interest_text,
                    "vec": vec,
                    "model": embedding_model,
                },
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
        nothing matches or no interest is set.

        The semantic boost only compares vectors of the same embedding space
        (``app.core.embedding_space``); a hotspot embedded by another model
        falls back to recency instead of scoring meaninglessly.

        SQL body kept: the CTE + regexp keyword unnest + ``<=>`` ranking is
        genuine raw-SQL territory."""
        same_space = spaces_compatible_sql("h.embedding_model", "me.embedding_model")
        async with read_scope() as session:
            result = await session.execute(
                text(
                    """
                    WITH me AS (
                        SELECT interest_text,
                               embedding,
                               embedding_model,
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
                                      OR lower(coalesce(h.content_original, ''))
                                         LIKE '%' || w || '%'
                                  )
                           )
                       )
                     ORDER BY
                       CASE
                           WHEN me.embedding IS NOT NULL AND h.embedding IS NOT NULL
                                AND """
                    + same_space
                    + """
                           THEN (h.embedding <=> me.embedding)
                       END NULLS LAST,
                       h.captured_at DESC
                     LIMIT :lim
                    """
                ),
                {"uid": user_id, "win": window_hours, "lim": limit},
            )
            return [str(r["id"]) for r in result.mappings().all()]


def get_user_topic_interest_repository() -> "UserTopicInterestRepository":
    return UserTopicInterestRepository()


__all__ = [
    "UserTopicInterestRepository",
    "get_user_topic_interest_repository",
]
