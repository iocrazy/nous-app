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
from sqlalchemy import Text, and_, cast, func, insert, or_, select, update
from sqlalchemy import delete as sa_delete

from app.db.session import read_scope, write_scope
from app.models import (
    Episodes,
    Issues,
    ProjectStageNodes,
    ScriptProjects,
    ScriptScenes,
    ScriptShots,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.issue_repository import needs_input_predicate
from app.services.library.project_stage_issues import ORIGIN_KIND

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
# Episode progress aggregate (PR-10a, spec G12; Phase B5 Task 1: migrated
# to the ORM). The episode -> script -> scene -> shot join chain needs
# multiple independently FILTERed counts over the same leaf table
# (shots_done, renders_count) — expressed here as func.count(...).filter(...)
# per column rather than a raw SQL DISTINCT-count aggregate.
# renders_count uses a single FILTER with an OR
# (image_url IS NOT NULL OR video_url IS NOT NULL) rather than two summed
# FILTERed counts — a shot can have both an image and a video (the
# image-then-video generation flow), and summing two separate FILTERs
# would double-count that shot. A primary read like list_by_project — a
# query failure is NOT swallowed here; it propagates so the router's
# generic except->500 fires, same as every sibling read endpoint.
# ------------------------------------------------------------------ #


def _scene_content_count_col():
    """FILTER predicate for "a real scene": non-OMITTED and content-non-empty
    (content text or content_json array either non-empty) — B4 spec §5
    script-criterion count. Extracted to a single module-level definition so
    ``_progress_stmt`` and ``surface_criteria_for_episode`` can never drift
    apart (they used to each hand-write this FILTER; Task 6 folds
    ``_progress_stmt`` onto this one, Task 2's original owner)."""
    return (
        func.count(func.distinct(ScriptScenes.id))
        .filter(
            ScriptScenes.omitted_at.is_(None),
            or_(
                ScriptScenes.content != "",
                func.jsonb_array_length(ScriptScenes.content_json) > 0,
            ),
        )
        .label("scene_content_count")
    )


def _progress_stmt(project_id: Optional[int]):
    return (
        select(
            Episodes.id.label("episode_id"),
            Episodes.title.label("title"),
            Episodes.sort_order.label("sort_order"),
            Episodes.current_node_id.label("current_node_id"),
            func.count(func.distinct(ScriptProjects.id)).label("script_count"),
            func.count(func.distinct(ScriptScenes.id)).label("scene_count"),
            _scene_content_count_col(),
            func.count(func.distinct(ScriptShots.id)).label("shots_total"),
            func.count(func.distinct(ScriptShots.id))
            .filter(ScriptShots.status == "done")
            .label("shots_done"),
            func.count(func.distinct(ScriptShots.id))
            .filter(
                ScriptShots.image_url.isnot(None) | ScriptShots.video_url.isnot(None)
            )
            .label("renders_count"),
        )
        .select_from(Episodes)
        .outerjoin(
            ScriptProjects,
            and_(
                ScriptProjects.episode_id == Episodes.id,
                ScriptProjects.status != "deleted",
            ),
        )
        .outerjoin(ScriptScenes, ScriptScenes.script_id == ScriptProjects.id)
        .outerjoin(ScriptShots, ScriptShots.scene_id == ScriptScenes.id)
        .where(Episodes.project_id == project_id)
        .group_by(
            Episodes.id, Episodes.title, Episodes.sort_order, Episodes.current_node_id
        )
        .order_by(Episodes.sort_order.asc())
    )


def _workflow_nodes_rollup_stmt(project_id: int):
    """Per-episode node counts for one project — one GROUP BY query for the
    whole project (not per-episode) to avoid N+1. Excludes skipped nodes via
    BOTH skip signals ``project_stage_nodes`` carries: the boolean
    ``skipped`` column and the legacy ``status='skipped'`` string."""
    return (
        select(
            ProjectStageNodes.episode_id.label("episode_id"),
            func.count(ProjectStageNodes.id).label("nodes_total"),
            func.count(ProjectStageNodes.id)
            .filter(ProjectStageNodes.status == "done")
            .label("nodes_done"),
        )
        .where(
            ProjectStageNodes.project_id == project_id,
            ProjectStageNodes.episode_id.isnot(None),
            ProjectStageNodes.skipped.is_(False),
            ProjectStageNodes.status != "skipped",
        )
        .group_by(ProjectStageNodes.episode_id)
    )


def _needs_input_rollup_stmt(project_id: int):
    """Per-episode count of needs_input mirror issues for one project — one
    GROUP BY query, joined via the project_stage origin_id shape
    (``project_stage:{project_id}:{node_id}``, ``build_stage_origin_id``'s
    format) built in SQL with concat/cast since it must match per-row
    against a batch of nodes, not a single known id (the Python-side
    ``build_stage_origin_id`` helper used everywhere else in the codebase
    only fits a one-id-at-a-time call site)."""
    return (
        select(
            ProjectStageNodes.episode_id.label("episode_id"),
            func.count(Issues.id).label("needs_input_count"),
        )
        .select_from(ProjectStageNodes)
        .join(
            Issues,
            and_(
                Issues.origin_kind == ORIGIN_KIND,
                Issues.origin_id
                == func.concat(
                    ORIGIN_KIND + ":",
                    cast(ProjectStageNodes.project_id, Text),
                    ":",
                    cast(ProjectStageNodes.id, Text),
                ),
            ),
        )
        .where(
            ProjectStageNodes.project_id == project_id,
            ProjectStageNodes.episode_id.isnot(None),
            needs_input_predicate(),
        )
        .group_by(ProjectStageNodes.episode_id)
    )


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


def script_criterion_met(script_count: int, scene_content_count: int) -> bool:
    """B4 spec §5 script 档完成判据:该集有剧本且有场次内容。

    与 ``_derive_episode_status`` 的展示阶梯刻意分离 —— 阶梯只判
    ``script_count == 0``(scene_count 是死参数),且把 OMITTED 场次计入;
    完成判据要求至少一个非 OMITTED、内容非空的场次。
    """
    return script_count > 0 and scene_content_count > 0


def storyboard_criterion_met(shots_total: int, shots_done: int) -> bool:
    """B4 spec §5 storyboard 档:镜头全部出卡(boarded 档口径,可复用)。"""
    return shots_total > 0 and shots_done == shots_total


def _progress_row(
    row: Dict[str, Any], workflow_rollup: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """One raw SQL row -> JSON-safe progress dict with derived status.

    ``workflow_rollup`` is this episode's slice of the two batched rollup
    queries ``progress_by_project`` runs alongside ``_progress_stmt``
    (``nodes_total`` / ``nodes_done`` / ``needs_input_count``) — kept OUT of
    ``_progress_stmt`` itself because joining ``project_stage_nodes``/
    ``issues`` into that query's GROUP BY would fan out and corrupt the
    shots_total/shots_done/renders_count aggregates. Defaults to zeros when
    the episode has no eligible nodes / no needs_input mirror issues (absent
    from both rollup dicts, not an error).

    ``surface_state`` is derived here (not fetched) from this same row's
    script_count/scene_content_count/shots_total/shots_done via Task 2's
    pure predicates — it can legitimately disagree with a node's persisted
    ``status`` (e.g. the produced content was deleted after the node
    auto-completed); that divergence is exactly what this field surfaces.
    """
    script_count = int(row["script_count"] or 0)
    scene_count = int(row["scene_count"] or 0)
    scene_content_count = int(row.get("scene_content_count") or 0)
    shots_total = int(row["shots_total"] or 0)
    shots_done = int(row["shots_done"] or 0)
    renders_count = int(row["renders_count"] or 0)
    rollup = workflow_rollup or {}
    current_node_id = row.get("current_node_id")
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
        "workflow": {
            "nodes_total": int(rollup.get("nodes_total") or 0),
            "nodes_done": int(rollup.get("nodes_done") or 0),
            "current_node_id": (
                str(current_node_id) if current_node_id is not None else None
            ),
            "needs_input_count": int(rollup.get("needs_input_count") or 0),
        },
        "surface_state": {
            "script": script_criterion_met(script_count, scene_content_count),
            "storyboard": storyboard_criterion_met(shots_total, shots_done),
        },
    }


class EpisodeRepository:
    """Episodes data access (async, SQLAlchemy 2.0 ORM)."""

    def __init__(self):
        pass

    async def progress_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Per-episode progress (script/scene/shot counts + derived status,
        plus B4 Task 6's workflow node/needs_input rollups and derived
        surface_state) for the workspace shell episodes panel (spec G12).
        LEFT JOINs in ``_progress_stmt`` so an empty episode (no scripts
        yet) still appears with all-zero counts. The two workflow rollups
        are separate GROUP BY queries (not joined into ``_progress_stmt``,
        which would fan out its shot aggregates) — one query each for the
        whole project, avoiding N+1 across episodes."""
        pid = _bigint(project_id)
        async with read_scope() as session:
            result = await session.execute(_progress_stmt(pid))
            rows = result.mappings().all()
            nodes_result = await session.execute(_workflow_nodes_rollup_stmt(pid))
            nodes_by_episode = {
                r["episode_id"]: dict(r) for r in nodes_result.mappings().all()
            }
            needs_input_result = await session.execute(_needs_input_rollup_stmt(pid))
            needs_input_by_episode = {
                r["episode_id"]: r["needs_input_count"]
                for r in needs_input_result.mappings().all()
            }
        return [
            _progress_row(
                r,
                workflow_rollup={
                    **nodes_by_episode.get(r["episode_id"], {}),
                    "needs_input_count": needs_input_by_episode.get(
                        r["episode_id"], 0
                    ),
                },
            )
            for r in rows
        ]

    async def surface_criteria_for_episode(self, episode_id: str) -> Dict[str, bool]:
        """One episode's surface-completion criteria (B4 spec §5).

        scene_content_count = 非 OMITTED 且内容非空(content 文本或 content_json
        数组任一非空)的场次数 —— 这是与 _progress_stmt.scene_count 的两点口径差。
        刻意不吞异常(对齐 progress_by_project 的口径,让写路径 hook 的外层
        try/except 记 warning)。
        """
        stmt = (
            select(
                func.count(func.distinct(ScriptProjects.id)).label("script_count"),
                _scene_content_count_col(),
                func.count(func.distinct(ScriptShots.id)).label("shots_total"),
                func.count(func.distinct(ScriptShots.id))
                .filter(ScriptShots.status == "done")
                .label("shots_done"),
            )
            .select_from(ScriptProjects)
            .outerjoin(ScriptScenes, ScriptScenes.script_id == ScriptProjects.id)
            .outerjoin(ScriptShots, ScriptShots.scene_id == ScriptScenes.id)
            .where(
                ScriptProjects.episode_id == int(episode_id),
                ScriptProjects.status != "deleted",
            )
        )
        async with read_scope() as session:
            row = (await session.execute(stmt)).one()
        return {
            "script": script_criterion_met(
                int(row.script_count or 0), int(row.scene_content_count or 0)
            ),
            "storyboard": storyboard_criterion_met(
                int(row.shots_total or 0), int(row.shots_done or 0)
            ),
        }

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
