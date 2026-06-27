"""Scoped read/write path for agent_memory (Phase A + B).

The ONLY recall/write entry point — the scope predicate is mandatory,
so no caller can issue an unscoped query. Phase B adds private-write and
fingerprint-dedup helpers; they never raise (best-effort consolidation).
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from loguru import logger
from sqlalchemy import text

from app.db.session import read_scope, write_scope

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


_INSERT_SQL = text(
    """
    INSERT INTO public.agent_memory
        (scope, owner_user_id, agent_id, visibility, kind, title, body_md,
         when_to_use, fingerprint)
    VALUES
        (:scope, :owner_user_id, :agent_id, 'private', :kind, :title, :body_md,
         :when_to_use, :fingerprint)
    """
)

_FINGERPRINTS_SQL = text(
    """
    SELECT fingerprint FROM public.agent_memory
    WHERE owner_user_id = :owner_user_id AND agent_id = :agent_id
      AND status = 'active' AND fingerprint <> ''
    """
)


async def write_memory_row(
    *,
    owner_user_id: str,
    agent_id: str,
    scope: str,
    kind: str,
    title: str,
    body_md: str,
    when_to_use: str,
    fingerprint: str,
) -> bool:
    """Insert one PRIVATE agent_memory row. Returns False on any error (never raises)."""
    try:
        async with write_scope() as session:
            await session.execute(
                _INSERT_SQL,
                {
                    "scope": scope,
                    "owner_user_id": owner_user_id,
                    "agent_id": agent_id,
                    "kind": kind,
                    "title": title,
                    "body_md": body_md,
                    "when_to_use": when_to_use,
                    "fingerprint": fingerprint,
                },
            )
        return True
    except Exception:  # noqa: BLE001 — consolidation write is best-effort
        logger.warning(f"[agent_memory] write failed for owner={owner_user_id}")
        return False


async def existing_fingerprints(*, owner_user_id: str, agent_id: str) -> Set[str]:
    """Fingerprints already stored for this (owner, agent). Empty set on error."""
    try:
        async with write_scope() as session:
            result = await session.execute(
                _FINGERPRINTS_SQL,
                {"owner_user_id": owner_user_id, "agent_id": agent_id},
            )
            return {str(fp) for fp in result.scalars().all()}
    except Exception:  # noqa: BLE001
        logger.warning(f"[agent_memory] fingerprint read failed owner={owner_user_id}")
        return set()


__all__ = ["existing_fingerprints", "recall_rows", "write_memory_row"]
