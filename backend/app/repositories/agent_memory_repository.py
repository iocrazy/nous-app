"""Scoped read/write path for agent_memory (Phase A + B).

The ONLY recall/write entry point — the scope predicate is mandatory,
so no caller can issue an unscoped query. Phase B adds private-write and
fingerprint-dedup helpers; they never raise (best-effort consolidation).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from loguru import logger
from sqlalchemy import text

from app.db.session import read_scope, write_scope

# ---------------------------------------------------------------------------
# Aggregate stats queries (Phase C2 observability)
# ---------------------------------------------------------------------------

_STATS_AM_SQL = text(
    """
    SELECT
      count(*) FILTER (WHERE status='active')                          AS total_active,
      count(*) FILTER (WHERE status='active' AND visibility='private') AS vis_private,
      count(*) FILTER (WHERE status='active' AND visibility='shared')  AS vis_shared,
      count(*) FILTER (WHERE status='active' AND scope='agent_user')   AS scope_agent_user,
      count(*) FILTER (WHERE status='active' AND scope='team')         AS scope_team,
      count(*) FILTER (WHERE status='active' AND scope='project')      AS scope_project,
      count(*) FILTER (WHERE status='active')                          AS st_active,
      count(*) FILTER (WHERE status='archived')                        AS st_archived,
      count(*) FILTER (WHERE status='superseded')                      AS st_superseded,
      count(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS created_24h,
      count(*) FILTER (WHERE created_at >= now() - interval '7 days')   AS created_7d,
      max(created_at)                                                   AS last_created_at
    FROM public.agent_memory
    """
)

_STATS_PROMO_SQL = text(
    """
    SELECT
      count(*) FILTER (WHERE status='pending')  AS pending,
      count(*) FILTER (WHERE status='approved') AS approved,
      count(*) FILTER (WHERE status='rejected') AS rejected
    FROM public.agent_memory_promotions
    """
)

_ZERO_STATS: Dict[str, Any] = {
    "total_active": 0,
    "by_visibility": {},
    "by_scope": {},
    "by_status": {},
    "created_24h": 0,
    "created_7d": 0,
    "last_created_at": None,
    "promotions": {"pending": 0, "approved": 0, "rejected": 0},
}

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
        (scope, owner_user_id, agent_id, team_id, project_id, visibility, kind,
         title, body_md, when_to_use, fingerprint)
    VALUES
        (:scope, :owner_user_id, :agent_id, :team_id, :project_id, 'private',
         :kind, :title, :body_md, :when_to_use, :fingerprint)
    """
)

_INSERT_RETURNING_SQL = text(
    """
    INSERT INTO public.agent_memory
        (scope, owner_user_id, agent_id, team_id, project_id, visibility, kind,
         title, body_md, when_to_use, fingerprint)
    VALUES
        (:scope, :owner_user_id, :agent_id, :team_id, :project_id, 'private',
         :kind, :title, :body_md, :when_to_use, :fingerprint)
    RETURNING id
    """
)

_FINGERPRINTS_SQL = text(
    """
    SELECT fingerprint FROM public.agent_memory
    WHERE owner_user_id = :owner_user_id AND agent_id = :agent_id
      AND scope = :scope
      AND team_id IS NOT DISTINCT FROM :team_id
      AND project_id IS NOT DISTINCT FROM :project_id
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
    team_id: Optional[int] = None,
    project_id: Optional[int] = None,
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
                    "team_id": team_id,
                    "project_id": project_id,
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


async def write_memory_row_returning_id(
    *,
    owner_user_id: str,
    agent_id: str,
    scope: str,
    kind: str,
    title: str,
    body_md: str,
    when_to_use: str,
    fingerprint: str,
    team_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> Optional[int]:
    """Insert one PRIVATE agent_memory row and return its new BIGINT id.

    Returns None on any error (never raises). Keeps write_memory_row
    unchanged for backward-compat.
    """
    try:
        async with write_scope() as session:
            result = await session.execute(
                _INSERT_RETURNING_SQL,
                {
                    "scope": scope,
                    "owner_user_id": owner_user_id,
                    "agent_id": agent_id,
                    "team_id": team_id,
                    "project_id": project_id,
                    "kind": kind,
                    "title": title,
                    "body_md": body_md,
                    "when_to_use": when_to_use,
                    "fingerprint": fingerprint,
                },
            )
        return result.scalar_one_or_none()
    except Exception:  # noqa: BLE001 — consolidation write is best-effort
        logger.warning(
            f"[agent_memory] write_returning_id failed for owner={owner_user_id}"
        )
        return None


_USER_TEAM_IDS_SQL = text(
    "SELECT team_id FROM public.team_members WHERE user_id = :uid"
)


async def get_user_team_ids(user_id: str) -> List[int]:
    """The caller's team memberships → list of BIGINT team ids (or []).

    Never raises — a failed lookup degrades to owner-only recall.
    """
    try:
        async with read_scope() as session:
            result = await session.execute(_USER_TEAM_IDS_SQL, {"uid": user_id})
            return [int(t) for t in result.scalars().all()]
    except Exception:  # noqa: BLE001 — recall must degrade, never raise
        logger.warning(f"[agent_memory] team-ids read failed user={user_id}")
        return []


async def existing_fingerprints(
    *,
    owner_user_id: str,
    agent_id: str,
    scope: str,
    team_id: Optional[int],
    project_id: Optional[int],
) -> Set[str]:
    """Fingerprints already stored for this (owner, agent, scope, context). Empty set on error."""
    try:
        async with write_scope() as session:
            result = await session.execute(
                _FINGERPRINTS_SQL,
                {
                    "owner_user_id": owner_user_id,
                    "agent_id": agent_id,
                    "scope": scope,
                    "team_id": team_id,
                    "project_id": project_id,
                },
            )
            return {str(fp) for fp in result.scalars().all()}
    except Exception:  # noqa: BLE001
        logger.warning(f"[agent_memory] fingerprint read failed owner={owner_user_id}")
        return set()


_LIST_USER_SQL = text(
    """
    SELECT id, owner_user_id, title, body_md, kind, scope, visibility,
           when_to_use, created_at
    FROM public.agent_memory
    WHERE status = 'active'
      AND (
        owner_user_id = :user_id
        OR (visibility = 'shared' AND team_id = ANY(:team_ids))
      )
    ORDER BY created_at DESC
    LIMIT :limit
    """
)

_DELETE_USER_SQL = text(
    """
    DELETE FROM public.agent_memory
    WHERE id = :id AND owner_user_id = :user_id
    """
)


async def list_user_memories(
    *, user_id: str, team_ids: List[int], limit: int = 200
) -> List[Dict[str, Any]]:
    """List active memories visible to a user (own + team-shared).

    Identical isolation predicate to recall_rows. Returns raw dicts (or []
    on error; never raises).
    """
    try:
        async with read_scope() as session:
            result = await session.execute(
                _LIST_USER_SQL,
                {"user_id": user_id, "team_ids": team_ids, "limit": limit},
            )
            return [dict(m) for m in result.mappings().all()]
    except Exception:  # noqa: BLE001 — best-effort, never raises
        logger.warning("[agent_memory] list_user_memories failed user={}", user_id)
        return []


async def delete_user_memory(*, memory_id: int, user_id: str) -> bool:
    """Delete one memory row owned by this user.

    Owner-scoped: WHERE id = :id AND owner_user_id = :user_id.
    Returns True if a row was deleted (rowcount > 0), False otherwise.
    Never raises.
    """
    try:
        async with write_scope() as session:
            result = await session.execute(
                _DELETE_USER_SQL,
                {"id": memory_id, "user_id": user_id},
            )
            return result.rowcount > 0
    except Exception:  # noqa: BLE001 — best-effort, never raises
        logger.warning(
            "[agent_memory] delete_user_memory failed memory_id={} user={}",
            memory_id,
            user_id,
        )
        return False


async def get_memory_stats() -> Dict[str, Any]:
    """Return aggregate counts over agent_memory and agent_memory_promotions.

    Runs two FILTER-aggregate queries in one session and maps the result into
    the documented dict shape.  Never raises — returns the zero/empty-shaped
    dict on any error (best-effort observability).
    """
    try:
        async with read_scope() as session:
            am_result = await session.execute(_STATS_AM_SQL)
            am = am_result.mappings().first() or {}

            promo_result = await session.execute(_STATS_PROMO_SQL)
            promo = promo_result.mappings().first() or {}

        last_ts = am.get("last_created_at")
        return {
            "total_active": int(am.get("total_active", 0)),
            "by_visibility": {
                "private": int(am.get("vis_private", 0)),
                "shared": int(am.get("vis_shared", 0)),
            },
            "by_scope": {
                "agent_user": int(am.get("scope_agent_user", 0)),
                "team": int(am.get("scope_team", 0)),
                "project": int(am.get("scope_project", 0)),
            },
            "by_status": {
                "active": int(am.get("st_active", 0)),
                "archived": int(am.get("st_archived", 0)),
                "superseded": int(am.get("st_superseded", 0)),
            },
            "created_24h": int(am.get("created_24h", 0)),
            "created_7d": int(am.get("created_7d", 0)),
            "last_created_at": str(last_ts) if last_ts is not None else None,
            "promotions": {
                "pending": int(promo.get("pending", 0)),
                "approved": int(promo.get("approved", 0)),
                "rejected": int(promo.get("rejected", 0)),
            },
        }
    except Exception:  # noqa: BLE001 — observability is best-effort, never raises
        logger.warning("[agent_memory] get_memory_stats failed — returning zero stats")
        return _ZERO_STATS.copy()


__all__ = [
    "delete_user_memory",
    "existing_fingerprints",
    "get_memory_stats",
    "get_user_team_ids",
    "list_user_memories",
    "recall_rows",
    "write_memory_row",
    "write_memory_row_returning_id",
]
