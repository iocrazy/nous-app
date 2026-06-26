"""Scoped read path for agent_memory (Phase A). The ONLY recall entry point —
the scope predicate is mandatory, so no caller can issue an unscoped query."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import text

from app.db.session import read_scope

# Ranked, scope-isolated recall. Isolation predicate:
#   own rows (any visibility) OR shared rows of a team the caller belongs to.
# search_tsv is the GENERATED tsvector; ts_rank gives BM25-like relevance.
_RECALL_SQL = text(
    """
    SELECT id, title, body_md, kind,
           ts_rank(search_tsv, websearch_to_tsquery('english', :q)) AS score
    FROM public.agent_memory
    WHERE status = 'active'
      AND search_tsv @@ websearch_to_tsquery('english', :q)
      AND (
        owner_user_id = :user_id
        OR (visibility = 'shared' AND team_id = ANY(:team_ids))
      )
    ORDER BY score DESC
    LIMIT :limit
    """
)


async def recall_rows(
    *, query: str, user_id: str, team_ids: List[int], limit: int
) -> List[Dict[str, Any]]:
    """Run the scoped recall SELECT. Returns raw mapping dicts (or [])."""
    async with read_scope() as session:
        result = await session.execute(
            _RECALL_SQL,
            {
                "q": query,
                "user_id": user_id,
                "team_ids": team_ids,
                "limit": limit,
            },
        )
        return [dict(m) for m in result.mappings().all()]


__all__ = ["recall_rows"]
