# app/repositories/episode_repository.py

"""Episode Repository — SQLAlchemy 2.0 ORM data access for the ``episodes``
dimension (spec v3 §2.1).

ORM-only (read_scope / write_scope), matching the house idiom in
``projects_repository.py`` / ``script_scene_repository.py``. Reads swallow +
return None/[] on failure; writes log + re-raise. Every bigint id/FK is
``_bigint``-coerced at the boundary (5.3 trap); read dicts go through
strategy-C value-type parity (datetime → ISO str; bigint ids stay native int).
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import and_
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, update

from app.db import engine as db_engine
from app.db.session import read_scope, write_scope
from app.models import Episodes, ScriptProjects
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

_EPISODES_N2A: Dict[str, str] = _name_to_attr(Episodes)
_EPISODES_ATTRS = {p.key for p in Episodes.__mapper__.column_attrs}
_EPISODE_BIGINT_FIELDS = ("project_id",)


def _bigint(v: Any) -> Optional[int]:
    """Coerce a bigint id/FK bind value to native int; None passes through."""
    return None if v is None else int(v)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE: uuid → str, datetime/date → ISO
    str. Bigint ids/FKs stay native int. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one Episodes row."""
    return _parity(_orm_obj_to_dict(obj, _EPISODES_N2A))


def _episode_write_values(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only mapped columns (graceful no-op for unknown keys) and
    bigint-coerce the id/FK fields."""
    known = {k: v for k, v in data.items() if k in _EPISODES_ATTRS}
    for field in _EPISODE_BIGINT_FIELDS:
        if field in known and known[field] is not None:
            known[field] = _bigint(known[field])
    return known


# ------------------------------------------------------------------ #
# Episode progress aggregate (PR-10a, spec G12) — raw SQL, not ORM. The
# episode -> script -> scene -> shot join chain needs multiple independently
# FILTERed counts over the same leaf table (shots_done, renders_count),
# which doesn't map cleanly onto a single ORM group_by; this follows the
# app.db.engine raw-SQL house idiom (see project_stages_repository.py)
# instead of read_scope. renders_count uses a single FILTER with an OR
# (image_url IS NOT NULL OR video_url IS NOT NULL) rather than two summed
# FILTERed counts — a shot can have both an image and a video (the
# image-then-video generation flow), and summing two separate FILTERs
# would double-count that shot. A primary read like list_by_project — a
# query failure is NOT swallowed here; it propagates so the router's
# generic except->500 fires, same as every sibling read endpoint.
# ------------------------------------------------------------------ #

_PROGRESS_SQL = """
    SELECT
      e.id AS episode_id,
      e.title AS title,
      e.sort_order AS sort_order,
      COUNT(DISTINCT sp.id) AS script_count,
      COUNT(DISTINCT sc.id) AS scene_count,
      COUNT(DISTINCT sh.id) AS shots_total,
      COUNT(DISTINCT sh.id) FILTER (WHERE sh.status = 'done') AS shots_done,
      COUNT(DISTINCT sh.id) FILTER (
        WHERE sh.image_url IS NOT NULL OR sh.video_url IS NOT NULL
      ) AS renders_count
    FROM public.episodes e
    LEFT JOIN public.script_projects sp
      ON sp.episode_id = e.id AND sp.status != 'deleted'
    LEFT JOIN public.script_scenes sc
      ON sc.script_id = sp.id
    LEFT JOIN public.script_shots sh
      ON sh.scene_id = sc.id
    WHERE e.project_id = :project_id
    GROUP BY e.id, e.title, e.sort_order
    ORDER BY e.sort_order ASC
"""


def _derive_episode_status(
    script_count: int,
    scene_count: int,
    shots_total: int,
    shots_done: int,
    renders_count: int,
) -> str:
    """Derive an episode's pipeline status from its counts (spec G12).

    Ladder: no scripts -> planned; has a script -> drafting; shots exist
    but aren't all done -> boarding; all shots done -> boarded; any render
    present -> rendered. Each check runs in ascending order and overrides
    the previous result, so the highest applicable status wins."""
    if script_count == 0:
        return "planned"
    status = "drafting"
    if shots_total > 0 and shots_done < shots_total:
        status = "boarding"
    if shots_total > 0 and shots_done == shots_total:
        status = "boarded"
    if renders_count > 0:
        status = "rendered"
    return status


def _progress_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """One raw SQL row -> JSON-safe progress dict with derived status."""
    script_count = int(row["script_count"] or 0)
    scene_count = int(row["scene_count"] or 0)
    shots_total = int(row["shots_total"] or 0)
    shots_done = int(row["shots_done"] or 0)
    renders_count = int(row["renders_count"] or 0)
    return {
        "episode_id": str(row["episode_id"]),
        "title": row["title"],
        "sort_order": int(row["sort_order"]),
        "script_count": script_count,
        "scene_count": scene_count,
        "shots_total": shots_total,
        "shots_done": shots_done,
        "renders_count": renders_count,
        "status": _derive_episode_status(
            script_count, scene_count, shots_total, shots_done, renders_count
        ),
    }


class EpisodeRepository:
    """Episodes data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    async def progress_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Per-episode progress (script/scene/shot counts + derived status)
        for the workspace shell episodes panel (spec G12). LEFT JOINs so an
        empty episode (no scripts yet) still appears with all-zero counts."""
        rows = (
            await db_engine.fetch_all(
                _PROGRESS_SQL, {"project_id": _bigint(project_id)}
            )
            or []
        )
        return [_progress_row(r) for r in rows]

    async def list_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """All episodes for a project, ordered by sort_order, each annotated
        with ``script_count`` — the number of non-deleted scripts pointing at
        it. The UI gates deletion on this: the ``episode_id`` FK is ON DELETE
        RESTRICT, so a non-empty episode cannot be removed."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Episodes, func.count(ScriptProjects.id))
                    .outerjoin(
                        ScriptProjects,
                        and_(
                            ScriptProjects.episode_id == Episodes.id,
                            ScriptProjects.status != "deleted",
                        ),
                    )
                    .where(Episodes.project_id == _bigint(project_id))
                    .group_by(Episodes.id)
                    .order_by(Episodes.sort_order.asc())
                )
                return [
                    {**_row(ep), "script_count": int(count or 0)}
                    for ep, count in result.all()
                ]
        except Exception as e:
            logger.error(f"Failed to list episodes for project {project_id}: {e}")
            return []

    async def get_by_id(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """A single episode by id, or None."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Episodes).where(Episodes.id == _bigint(episode_id)).limit(1)
                )
                row = result.scalars().first()
                return _row(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get episode {episode_id}: {e}")
            return None

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create an episode.

        When the caller doesn't pass an explicit ``sort_order`` (the
        episodes_router POST path), assign the next slot after the
        project's current max via a correlated subquery in the same
        INSERT (``COALESCE(MAX(sort_order)+1, 1)``) — otherwise every new
        episode lands at the ``episodes.sort_order`` DB default of 0, so
        the first Move up/Move down against an existing row is a same-
        value 0<->0 PATCH swap: a visible no-op. Callers that DO pass an
        explicit ``sort_order`` (e.g. the auto-Ep1 project-creation block,
        which always seeds ``sort_order=1``) keep that value untouched.
        """
        try:
            values = _episode_write_values(data)
            if "sort_order" not in values and values.get("project_id") is not None:
                # Concurrency note: MAX(sort_order)+1 is read inside the INSERT
                # but is NOT collision-proof under concurrent creates — two
                # near-simultaneous inserts for the same project can both read
                # the same MAX and land on an identical sort_order. This is
                # acceptable for the single-writer case that dominates here
                # (one user managing their project's episodes): the resulting
                # tie is benign — the list sorts stably and the first Move
                # up/down swaps the two rows' sort_order, self-healing the
                # duplicate. If this ever needs to be collision-free under
                # multiple concurrent writers, take a per-project advisory lock
                # (``pg_advisory_xact_lock(hashtext(project_id))``) around the
                # read+insert, or add a UNIQUE(project_id, sort_order) and
                # retry on conflict. Not worth that cost today.
                values["sort_order"] = (
                    select(func.coalesce(func.max(Episodes.sort_order) + 1, 1))
                    .where(Episodes.project_id == values["project_id"])
                    .scalar_subquery()
                )
            async with write_scope() as session:
                result = await session.execute(
                    insert(Episodes).values(**values).returning(Episodes)
                )
                row = result.scalars().first()
                out = _row(row) if row else {}
            logger.info(f"Created episode in project {data.get('project_id')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create episode: {e}")
            raise

    async def update(
        self, episode_id: str, data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update an episode (title / sort_order). Returns the updated row or
        None when no such episode exists."""
        try:
            values = {
                k: v
                for k, v in _episode_write_values(data).items()
                if k not in ("id", "project_id", "created_at")
            }
            if not values:
                return await self.get_by_id(episode_id)
            async with write_scope() as session:
                result = await session.execute(
                    update(Episodes)
                    .where(Episodes.id == _bigint(episode_id))
                    .values(**values)
                    .returning(Episodes)
                )
                row = result.scalars().first()
                out = _row(row) if row else None
            logger.info(f"Updated episode {episode_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update episode {episode_id}: {e}")
            raise

    async def delete(self, episode_id: str) -> bool:
        """Delete an episode."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(Episodes).where(Episodes.id == _bigint(episode_id))
                )
            logger.info(f"Deleted episode {episode_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete episode {episode_id}: {e}")
            raise

    async def set_current_node_id(
        self, episode_id: str, node_id: Optional[str]
    ) -> None:
        """Move the per-episode workflow cursor (``episodes.current_node_id``,
        mig 402, B1) — the explicit, dedicated write entry point for it,
        sibling to ``ProjectStageNodesRepository.set_current_node_id`` (which
        still owns the legacy ``projects.current_node_id`` column; this
        method never touches that one). B1 only lands this accessor — B2 is
        what actually rewires ``advance_service`` to call it instead of the
        project-level cursor."""
        async with write_scope() as session:
            await session.execute(
                update(Episodes)
                .where(Episodes.id == _bigint(episode_id))
                .values(
                    current_node_id=_bigint(node_id) if node_id is not None else None
                )
            )


def get_episode_repository() -> "EpisodeRepository":
    """Return the EpisodeRepository (ORM-only)."""
    return EpisodeRepository()
