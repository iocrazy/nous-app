# app/repositories/storyboard_repository.py

"""Storyboard Repository Layer — data access for the Storyboard Workbench.

ORM 2.0 (post-rollout collapse). The SIX ``Storyboard*Repository`` classes are
the SQLAlchemy 2.0 implementation over their own tables:

  StoryboardProjectRepository   → storyboard_projects
  StoryboardNodeRepository      → storyboard_nodes
  StoryboardEdgeRepository      → storyboard_edges
  StoryboardFrameRepository     → storyboard_frames
  StoryboardCharacterRepository → storyboard_characters
  StoryboardAssetRepository     → storyboard_assets

Every DB method goes through ``read_scope()`` / ``write_scope()`` and builds
SELECT *-shaped dicts via ``_orm_obj_to_dict`` + a precomputed ``_name_to_attr``
map per model. Call sites go through the six ``get_storyboard_*_repository()``
factories (bottom of this file), which now unconditionally return these classes.
This module is 100% ORM: the last supabase-py surface (the ``_get_client``
helper kept for the ``StoryboardService.get_asset`` reach-in) was retired when
``StoryboardAssetRepository.get_by_id`` replaced that ad-hoc REST read.

STRATEGY C — VALUE-TYPE PARITY (per-field, exact REST shape)
============================================================
Supabase REST rendered JSON: bigint → int, uuid → str, timestamptz → ISO str,
date → 'YYYY-MM-DD' str, numeric → str, jsonb → dict. The ORM returns native
types. We coerce ONLY where it matters, to the exact REST shape:

  bigint ids + FKs — EVERY storyboard id/FK is BIGINT snowflake: the six
    tables' ``id``, plus project_id / node_id / source_node_id / target_node_id
    / team_id (storyboard_projects.team_id is BIGINT, NOT uuid — it FKs
    teams.id) / frame_index? (frame_index is plain Integer, not a snowflake but
    still int). → STAY NATIVE int (the 5.3 trap). team_id specifically is
    CONSUMED: StoryboardService.verify_project_access does
    ``client.table("team_members").eq("team_id", project["team_id"])`` and
    upload paths do ``str(project["team_id"])`` for the NAS path — both work
    on a native int (the REST eq accepts an int; str(int) == str(str)).

  uuid — the ONLY uuid column across the six tables is
    ``storyboard_projects.created_by``. → str for REST-shape parity. Consumer
    audit: StoryboardProjectResponse declares ``created_by: str``; no consumer
    does ``UUID(p["created_by"])`` or a uuid ``==`` — it is write-input +
    response-display only. The generic uuid-sweep in ``_parity`` covers it (and
    any future uuid column) at once.

  timestamptz (created_at / updated_at across the tables) → ``.isoformat()``
    ALWAYS (sweep every datetime in the output dict). REST returned ISO; the
    response models take ``datetime`` (parses ISO fine); any ``==`` / ordering
    on a native datetime vs ISO str would diverge. Checked BEFORE ``date``
    (``datetime`` ⊂ ``date``), though there are NO bare ``date`` columns here.

  numeric (position_x / position_y / width / height : Double(53);
    duration_seconds : Double(53)) → LEFT NATIVE float. REST returned a JSON
    number for Double, and the frontend canvas does float math on coords;
    ``_parity`` does not touch float. Parity preserved.

  jsonb (data_json / viewport_json / settings_json / visual_traits /
    annotations_json / metadata_json) → native dict (REST returned dict too).
    ``_parity`` leaves dicts alone.

Model-quirk scan
----------------
  - NO renamed columns: every mapped attribute name == its DB column name on all
    six models. ``_name_to_attr`` resolves to an identity map here but is kept
    for consistency + future-proofing.
  - NO SQLAlchemy ``Enum`` columns: status / node_type / edge_type /
    transition_type / source_type are plain ``String`` columns guarded by DB
    CheckConstraints, so reads return bare ``str`` already.
  - server-default snowflake ids (``generate_snowflake_id()``) on all six PKs →
    inserts omit ``id`` and rely on the default. The bulk_upsert mixed-PK
    handling (below) covers the case where a caller DOES supply an explicit id.
  - junction table ``storyboard_frame_characters`` (Core ``Table``, no
    declarative class): NO repo method, service, router, or workflow touches it
    (grep-verified). Nothing to migrate; documented finding only.

PHANTOM-WRITE handling (parity discipline)
------------------------------------------
The bulk_upsert methods pass row keys straight to
``pg_insert(...).values(**row)`` — they do NOT use ``_known_only`` to silently
drop unknown keys (that would TURN a phantom-column 500 INTO a SUCCESS = a
behavior change, forbidden on a parity migration). An unknown key raises a
SQLAlchemy compile error which the method's ``except`` re-raises — so a broken
caller surfaces exactly as under REST. The single-row create/update paths DO run
through ``_known_only`` (the legacy REST graceful no-op for a phantom column on a
single-row write).

bulk_upsert MIXED-PK handling (the L1 CompileError trap)
--------------------------------------------------------
The three bulk_upsert methods (Node / Edge / Frame) reproduce the legacy
``.upsert(rows, on_conflict="id")`` semantics. A single multi-row VALUES INSERT
cannot mix rows that supply an explicit ``id`` (existing rows to update) with
rows that omit it (new rows relying on the ``generate_snowflake_id()`` server
default) — SQLAlchemy raises CompileError on the heterogeneous PK set. So each
bulk_upsert runs ROW-BY-ROW inside ONE ``write_scope()`` (a single committing
transaction = atomic batch), each row a
``pg_insert(Model).values(**row).on_conflict_do_update(index_elements=[id],
set_=<every supplied non-id column>)``. Explicit ``id`` / injected FK str values
are coerced to int (``_bigint``) for the BIGINT bind (asyncpg int8 codec is
strict).

Single-row create/update bigint coercion (fix/script-sb-create-bigint,
2026-07-04): every single-row create/update ``values()`` dict is also run
through ``_coerce_bigint_cols()`` for its bigint FK/id columns (team_id /
project_id / node_id) before bind. This was NOT true before this fix —
``StoryboardProjectRepository.create`` in particular bound ``team_id`` (a str
from ``require_team_id()``) straight into an INSERT, which asyncpg's strict
int8 codec rejects with a DataError (prod 500 on every "New Storyboard"
click). Only the bulk_upsert paths and WHERE-clause ``_bigint()`` calls had
this discipline; the single-row write ``values()`` paths did not.

Date/timestamp FILTER binding (v3 trap): NONE. Every read filters by equality
(id / project_id / node_id / file_hash) or ``.neq("status","deleted")`` / ilike
/ ordering — there is NO ``WHERE <ts_col> </>/<= X`` range filter, so there is no
timestamptz<VARCHAR hazard.

Writes commit via ``write_scope()`` (the silent-rollback P0 lesson). Reads use
``read_scope()``. Error handling: reads swallow + return None/[]/{} on failure;
writes (create / update / bulk_upsert / delete / reorder / soft_delete /
update_viewport) log + re-raise.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    StoryboardAssets,
    StoryboardCharacters,
    StoryboardEdges,
    StoryboardFrames,
    StoryboardNodes,
    StoryboardProjects,
)
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

# Precomputed DB-column-name → mapped-attribute-name maps (built once). Identity
# maps here (no renamed columns) but kept for consistency + future-proofing.
_PROJECTS_N2A: Dict[str, str] = _name_to_attr(StoryboardProjects)
_NODES_N2A: Dict[str, str] = _name_to_attr(StoryboardNodes)
_EDGES_N2A: Dict[str, str] = _name_to_attr(StoryboardEdges)
_FRAMES_N2A: Dict[str, str] = _name_to_attr(StoryboardFrames)
_CHARACTERS_N2A: Dict[str, str] = _name_to_attr(StoryboardCharacters)
_ASSETS_N2A: Dict[str, str] = _name_to_attr(StoryboardAssets)

# Mapped attribute names per model — for filtering unknown keys out of NON-bulk
# write values() (the graceful no-op contract). NOTE: bulk_upsert deliberately
# does NOT use these (it must raise on phantom keys for REST parity — see the
# module docstring PHANTOM-WRITE note). Bigint ids / FKs are not in any sweep
# list, so they stay native int.
_PROJECTS_ATTRS = {p.key for p in StoryboardProjects.__mapper__.column_attrs}
_NODES_ATTRS = {p.key for p in StoryboardNodes.__mapper__.column_attrs}
_FRAMES_ATTRS = {p.key for p in StoryboardFrames.__mapper__.column_attrs}
_CHARACTERS_ATTRS = {p.key for p in StoryboardCharacters.__mapper__.column_attrs}
_ASSETS_ATTRS = {p.key for p in StoryboardAssets.__mapper__.column_attrs}


def _bigint(value: Any) -> Any:
    """Coerce a snowflake-as-str id/FK to int for a BIGINT bind/where (asyncpg's
    int8 codec rejects str). Passes None / already-int through unchanged."""
    if value is None or isinstance(value, int):
        return value
    return int(value)


def _coerce_bigint_cols(data: Dict[str, Any], cols: tuple) -> Dict[str, Any]:
    """Return a NEW dict (immutable — never mutate the caller's ``data``) with
    each of ``cols`` coerced through ``_bigint`` when present. Every write path
    that binds a caller-supplied dict containing bigint FK/id columns (team_id /
    project_id / node_id) must run it through this first — the service layer
    passes these as ``str`` (e.g. ``require_team_id`` / path params) and
    asyncpg's int8 codec rejects a str bind."""
    out = dict(data)
    for col in cols:
        if col in out:
            out[col] = _bigint(out[col])
    return out


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Apply strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:

    - any ``uuid.UUID`` value → str (REST returned strings; covers
      storyboard_projects.created_by and any future uuid column).
    - any ``datetime`` → ``.isoformat()`` (REST ISO; ``==``/ordering/``str()``
      footgun). Checked BEFORE ``date`` because ``datetime`` ⊂ ``date`` (no bare
      ``date`` column exists on these models, but the order is kept correct).
    - any ``date`` → ``.isoformat()`` → 'YYYY-MM-DD' (REST shape; inert here).
    - bigint ids / FKs and numeric (Double/float) → LEFT NATIVE (the 5.3 trap +
      the iron rule: canvas coords are float-consumed). NULLs pass through.
    """
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _row(obj: Any, name_to_attr: Dict[str, str]) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one ORM row (enum-unwrapped
    via ``_orm_obj_to_dict`` + ``_plain`` [inert here], then uuid/datetime/date
    coerced)."""
    return _parity(_orm_obj_to_dict(obj, name_to_attr))


def _known_only(data: Dict[str, Any], attrs: set[str]) -> Dict[str, Any]:
    """Build a NON-bulk write ``values()`` dict: drop keys that are not mapped
    columns (preserving the legacy REST graceful-no-op for any phantom column on
    a SINGLE-row create/update). Logs dropped keys once at debug. Returns a NEW
    dict. NOTE: NOT used by bulk_upsert (which must raise on phantom keys for
    parity — see the module docstring)."""
    known = {k: v for k, v in data.items() if k in attrs}
    dropped = [k for k in data if k not in attrs]
    if dropped:
        logger.debug(
            f"StoryboardRepository: dropping non-column keys {dropped} "
            f"(phantom-column graceful no-op, REST parity)"
        )
    return known


async def _bulk_upsert_rows(
    model: Any,
    name_to_attr: Dict[str, str],
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Row-by-row ON CONFLICT (id) DO UPDATE upsert inside ONE write_scope().

    Shared by the three bulk_upsert methods. Reproduces the legacy
    ``.upsert(rows, on_conflict="id")`` exactly while sidestepping the mixed-PK
    multi-VALUES CompileError (explicit-id update rows + default-id insert rows
    in the same call). Row keys are passed THROUGH (not _known_only-filtered) so
    a phantom column raises a compile error = REST-parity 500 on a broken
    caller. ``id`` and any value bound to a BIGINT id/FK key is coerced to int
    (asyncpg int8 strictness) — here the caller-injected FK (project_id /
    node_id) is already int from the where-coercion, but explicit ``id`` strings
    are coerced defensively. Committing + atomic."""
    out: List[Dict[str, Any]] = []
    async with write_scope() as session:
        for row in rows:
            row = dict(row)
            if row.get("id") is not None:
                row["id"] = _bigint(row["id"])
            stmt = pg_insert(model).values(**row)
            # ON CONFLICT (id) DO UPDATE every supplied non-id column.
            update_cols = {k: getattr(stmt.excluded, k) for k in row if k != "id"}
            stmt = stmt.on_conflict_do_update(
                index_elements=[model.id], set_=update_cols
            ).returning(model)
            result = await session.execute(stmt)
            obj = result.scalars().first()
            if obj is not None:
                out.append(_row(obj, name_to_attr))
    return out


# --------------------------------------------------------------------------- #
# 1. StoryboardProjectRepository
# --------------------------------------------------------------------------- #


class StoryboardProjectRepository:
    """CRUD + list operations for storyboard_projects."""

    TABLE_NAME = "storyboard_projects"

    # ------------------------------------------------------------------ #
    # Write operations
    # ------------------------------------------------------------------ #

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new storyboard project. Returns the created row dict."""
        try:
            values = _coerce_bigint_cols(
                _known_only(data, _PROJECTS_ATTRS), ("id", "team_id", "project_id")
            )
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(StoryboardProjects)
                    .values(**values)
                    .returning(StoryboardProjects)
                )
                row = result.scalars().first()
                out = _row(row, _PROJECTS_N2A) if row else {}
            logger.info(f"Created storyboard project: {data.get('name')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create storyboard project: {e}")
            raise

    async def update(self, project_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing storyboard project. Returns the updated row dict."""
        try:
            values = _coerce_bigint_cols(
                _known_only(data, _PROJECTS_ATTRS), ("team_id", "project_id")
            )
            if not values:
                current = await self.get_by_id(project_id)
                return current or {}
            async with write_scope() as session:
                result = await session.execute(
                    update(StoryboardProjects)
                    .where(StoryboardProjects.id == _bigint(project_id))
                    .values(**values)
                    .returning(StoryboardProjects)
                )
                row = result.scalars().first()
                out = _row(row, _PROJECTS_N2A) if row else {}
            logger.info(f"Updated storyboard project {project_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update storyboard project {project_id}: {e}")
            raise

    async def update_viewport(
        self, project_id: str, viewport_json: Dict[str, Any]
    ) -> None:
        """Lightweight viewport update — does NOT touch updated_at.

        Intended for high-frequency canvas pan/zoom saves so collaborators do
        not see a spurious "modified" timestamp.
        """
        try:
            async with write_scope() as session:
                await session.execute(
                    update(StoryboardProjects)
                    .where(StoryboardProjects.id == _bigint(project_id))
                    .values(viewport_json=viewport_json)
                )
        except Exception as e:
            logger.error(f"Failed to update viewport for project {project_id}: {e}")
            raise

    async def soft_delete(self, project_id: str) -> None:
        """Soft-delete a project by setting its status to 'deleted'."""
        try:
            async with write_scope() as session:
                await session.execute(
                    update(StoryboardProjects)
                    .where(StoryboardProjects.id == _bigint(project_id))
                    .values(status="deleted")
                )
            logger.info(f"Soft-deleted storyboard project {project_id}")
        except Exception as e:
            logger.error(f"Failed to soft-delete storyboard project {project_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #

    async def get_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single storyboard project by id, or None if not found."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardProjects)
                    .where(StoryboardProjects.id == _bigint(project_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row, _PROJECTS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get storyboard project {project_id}: {e}")
            return None

    async def list_by_team(
        self,
        team_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
        project_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Paginated list of non-deleted storyboard projects for a team.

        Returns ``{items, total, page, limit}`` — mirrors the REST envelope
        shape, the ilike substring search escaping, and the sort_by/sort_order
        contract.
        """
        try:
            offset = (page - 1) * limit
            # team_id is BIGINT; coerce the str arg for the int8 bind.
            base = select(StoryboardProjects).where(
                StoryboardProjects.team_id == _bigint(team_id),
                StoryboardProjects.status != "deleted",
            )
            if project_id is not None:
                base = base.where(StoryboardProjects.project_id == _bigint(project_id))
            if search:
                # Reproduce the REST ilike substring match (LIKE escaping for
                # %/_ matches the legacy escaped pattern).
                escaped = search.replace("%", r"\%").replace("_", r"\_")
                base = base.where(StoryboardProjects.name.ilike(f"%{escaped}%"))

            # Resolve the sort column by name (REST .order(sort_by)); fall back
            # to updated_at if an unknown column is requested (defensive — the
            # service only ever passes a real column).
            sort_col = getattr(StoryboardProjects, sort_by, None)
            if sort_col is None:
                sort_col = StoryboardProjects.updated_at
            ordered = (
                sort_col.desc() if sort_order.lower() == "desc" else sort_col.asc()
            )

            async with read_scope() as session:
                total = await session.scalar(
                    select(func.count()).select_from(base.subquery())
                )
                result = await session.execute(
                    base.order_by(ordered).offset(offset).limit(limit)
                )
                items = [_row(r, _PROJECTS_N2A) for r in result.scalars().all()]
            return {
                "items": items,
                "total": total or 0,
                "page": page,
                "limit": limit,
            }
        except Exception as e:
            logger.error(f"Failed to list storyboard projects for team {team_id}: {e}")
            return {"items": [], "total": 0, "page": page, "limit": limit}


# --------------------------------------------------------------------------- #
# 2. StoryboardNodeRepository
# --------------------------------------------------------------------------- #


class StoryboardNodeRepository:
    """Bulk upsert and CRUD for storyboard_nodes."""

    TABLE_NAME = "storyboard_nodes"

    async def bulk_upsert(
        self, project_id: str, nodes: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Insert or update multiple nodes for a project atomically (ON CONFLICT
        id). ``project_id`` is injected into every row."""
        if not nodes:
            return []
        try:
            rows = [{**node, "project_id": _bigint(project_id)} for node in nodes]
            out = await _bulk_upsert_rows(StoryboardNodes, _NODES_N2A, rows)
            logger.info(f"Bulk-upserted {len(rows)} nodes for project {project_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to bulk-upsert nodes for project {project_id}: {e}")
            raise

    async def update(self, node_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a single node. Returns the updated row dict."""
        try:
            values = _coerce_bigint_cols(
                _known_only(data, _NODES_ATTRS), ("project_id",)
            )
            if not values:
                async with read_scope() as session:
                    row = (
                        (
                            await session.execute(
                                select(StoryboardNodes)
                                .where(StoryboardNodes.id == _bigint(node_id))
                                .limit(1)
                            )
                        )
                        .scalars()
                        .first()
                    )
                    return _row(row, _NODES_N2A) if row else {}
            async with write_scope() as session:
                result = await session.execute(
                    update(StoryboardNodes)
                    .where(StoryboardNodes.id == _bigint(node_id))
                    .values(**values)
                    .returning(StoryboardNodes)
                )
                row = result.scalars().first()
                out = _row(row, _NODES_N2A) if row else {}
            logger.info(f"Updated storyboard node {node_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update storyboard node {node_id}: {e}")
            raise

    async def delete(self, node_id: str) -> None:
        """Hard-delete a single node."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(StoryboardNodes).where(
                        StoryboardNodes.id == _bigint(node_id)
                    )
                )
            logger.info(f"Deleted storyboard node {node_id}")
        except Exception as e:
            logger.error(f"Failed to delete storyboard node {node_id}: {e}")
            raise

    async def delete_by_project(self, project_id: str) -> None:
        """Delete all nodes belonging to a project."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(StoryboardNodes).where(
                        StoryboardNodes.project_id == _bigint(project_id)
                    )
                )
            logger.info(f"Deleted all nodes for project {project_id}")
        except Exception as e:
            logger.error(f"Failed to delete nodes for project {project_id}: {e}")
            raise

    async def get_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Fetch all nodes for a project."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardNodes).where(
                        StoryboardNodes.project_id == _bigint(project_id)
                    )
                )
                return [_row(r, _NODES_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get nodes for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# 3. StoryboardEdgeRepository
# --------------------------------------------------------------------------- #


class StoryboardEdgeRepository:
    """Bulk upsert and CRUD for storyboard_edges."""

    TABLE_NAME = "storyboard_edges"

    async def bulk_upsert(
        self, project_id: str, edges: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Insert or update multiple edges for a project atomically (ON CONFLICT
        id). ``project_id`` is injected into every row."""
        if not edges:
            return []
        try:
            rows = [{**edge, "project_id": _bigint(project_id)} for edge in edges]
            # source_node_id / target_node_id are BIGINT FKs — coerce str → int.
            for row in rows:
                for fk in ("source_node_id", "target_node_id"):
                    if row.get(fk) is not None:
                        row[fk] = _bigint(row[fk])
            out = await _bulk_upsert_rows(StoryboardEdges, _EDGES_N2A, rows)
            logger.info(f"Bulk-upserted {len(rows)} edges for project {project_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to bulk-upsert edges for project {project_id}: {e}")
            raise

    async def delete(self, edge_id: str) -> None:
        """Hard-delete a single edge."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(StoryboardEdges).where(
                        StoryboardEdges.id == _bigint(edge_id)
                    )
                )
            logger.info(f"Deleted storyboard edge {edge_id}")
        except Exception as e:
            logger.error(f"Failed to delete storyboard edge {edge_id}: {e}")
            raise

    async def delete_by_project(self, project_id: str) -> None:
        """Delete all edges belonging to a project."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(StoryboardEdges).where(
                        StoryboardEdges.project_id == _bigint(project_id)
                    )
                )
            logger.info(f"Deleted all edges for project {project_id}")
        except Exception as e:
            logger.error(f"Failed to delete edges for project {project_id}: {e}")
            raise

    async def get_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Fetch all edges for a project."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardEdges).where(
                        StoryboardEdges.project_id == _bigint(project_id)
                    )
                )
                return [_row(r, _EDGES_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get edges for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# 4. StoryboardFrameRepository
# --------------------------------------------------------------------------- #


class StoryboardFrameRepository:
    """Bulk upsert, reorder, and read operations for storyboard_frames."""

    TABLE_NAME = "storyboard_frames"

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a single frame record. Returns the created row dict."""
        try:
            values = _coerce_bigint_cols(
                _known_only(data, _FRAMES_ATTRS), ("id", "node_id", "project_id")
            )
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(StoryboardFrames)
                    .values(**values)
                    .returning(StoryboardFrames)
                )
                row = result.scalars().first()
                out = _row(row, _FRAMES_N2A) if row else {}
            logger.info(f"Created storyboard frame: index={data.get('frame_index')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create storyboard frame: {e}")
            raise

    async def bulk_upsert(
        self, node_id: str, frames: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Insert or update multiple frames for a node atomically (ON CONFLICT
        id). ``node_id`` is injected into every row."""
        if not frames:
            return []
        try:
            rows = [{**frame, "node_id": _bigint(node_id)} for frame in frames]
            # project_id is a required BIGINT FK the callers now supply (str on
            # the workflow path) — coerce for the int8 bind (asyncpg strictness).
            for row in rows:
                if row.get("project_id") is not None:
                    row["project_id"] = _bigint(row["project_id"])
            out = await _bulk_upsert_rows(StoryboardFrames, _FRAMES_N2A, rows)
            logger.info(f"Bulk-upserted {len(rows)} frames for node {node_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to bulk-upsert frames for node {node_id}: {e}")
            raise

    async def update(self, frame_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a single frame. Returns the updated row dict."""
        try:
            values = _coerce_bigint_cols(
                _known_only(data, _FRAMES_ATTRS), ("node_id", "project_id")
            )
            if not values:
                async with read_scope() as session:
                    row = (
                        (
                            await session.execute(
                                select(StoryboardFrames)
                                .where(StoryboardFrames.id == _bigint(frame_id))
                                .limit(1)
                            )
                        )
                        .scalars()
                        .first()
                    )
                    return _row(row, _FRAMES_N2A) if row else {}
            async with write_scope() as session:
                result = await session.execute(
                    update(StoryboardFrames)
                    .where(StoryboardFrames.id == _bigint(frame_id))
                    .values(**values)
                    .returning(StoryboardFrames)
                )
                row = result.scalars().first()
                out = _row(row, _FRAMES_N2A) if row else {}
            logger.info(f"Updated storyboard frame {frame_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update storyboard frame {frame_id}: {e}")
            raise

    async def reorder(self, frame_ids: List[str]) -> None:
        """Reorder frames by assigning sort_order = list position (first → 0)."""
        if not frame_ids:
            return
        try:
            # Parity-equivalent of the legacy RPC's end-state (sort_order = list
            # position). The legacy tried rpc_reorder_storyboard_frames then fell
            # back to per-row UPDATEs; the ORM path runs the per-row UPDATEs
            # directly inside ONE write_scope() (atomic batch, same final state).
            async with write_scope() as session:
                for index, frame_id in enumerate(frame_ids):
                    await session.execute(
                        update(StoryboardFrames)
                        .where(StoryboardFrames.id == _bigint(frame_id))
                        .values(sort_order=index)
                    )
            logger.info(f"Reordered {len(frame_ids)} storyboard frames")
        except Exception as e:
            logger.error(f"Failed to reorder storyboard frames: {e}")
            raise

    async def get_by_node(self, node_id: str) -> List[Dict[str, Any]]:
        """Fetch all frames for a node, ordered by sort_order."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardFrames)
                    .where(StoryboardFrames.node_id == _bigint(node_id))
                    .order_by(StoryboardFrames.sort_order)
                )
                return [_row(r, _FRAMES_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get frames for node {node_id}: {e}")
            return []

    async def get_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Fetch all frames for a project, ordered by sort_order."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardFrames)
                    .where(StoryboardFrames.project_id == _bigint(project_id))
                    .order_by(StoryboardFrames.sort_order)
                )
                return [_row(r, _FRAMES_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get frames for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# 5. StoryboardCharacterRepository
# --------------------------------------------------------------------------- #


class StoryboardCharacterRepository:
    """CRUD for storyboard_characters."""

    TABLE_NAME = "storyboard_characters"

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new character. Returns the created row dict."""
        try:
            values = _coerce_bigint_cols(
                _known_only(data, _CHARACTERS_ATTRS), ("id", "project_id")
            )
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(StoryboardCharacters)
                    .values(**values)
                    .returning(StoryboardCharacters)
                )
                row = result.scalars().first()
                out = _row(row, _CHARACTERS_N2A) if row else {}
            logger.info(f"Created storyboard character: {data.get('name')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create storyboard character: {e}")
            raise

    async def update(self, character_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing character. Returns the updated row dict."""
        try:
            values = _coerce_bigint_cols(
                _known_only(data, _CHARACTERS_ATTRS), ("project_id",)
            )
            if not values:
                current = await self.get_by_id(character_id)
                return current or {}
            async with write_scope() as session:
                result = await session.execute(
                    update(StoryboardCharacters)
                    .where(StoryboardCharacters.id == _bigint(character_id))
                    .values(**values)
                    .returning(StoryboardCharacters)
                )
                row = result.scalars().first()
                out = _row(row, _CHARACTERS_N2A) if row else {}
            logger.info(f"Updated storyboard character {character_id}")
            return out
        except Exception as e:
            logger.error(f"Failed to update storyboard character {character_id}: {e}")
            raise

    async def delete(self, character_id: str) -> None:
        """Hard-delete a character."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(StoryboardCharacters).where(
                        StoryboardCharacters.id == _bigint(character_id)
                    )
                )
            logger.info(f"Deleted storyboard character {character_id}")
        except Exception as e:
            logger.error(f"Failed to delete storyboard character {character_id}: {e}")
            raise

    async def get_by_id(self, character_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single character by id, or None if not found."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardCharacters)
                    .where(StoryboardCharacters.id == _bigint(character_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row, _CHARACTERS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get character {character_id}: {e}")
            return None

    async def list_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Fetch all characters for a project, oldest first."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardCharacters)
                    .where(StoryboardCharacters.project_id == _bigint(project_id))
                    .order_by(StoryboardCharacters.created_at)
                )
                return [_row(r, _CHARACTERS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list characters for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# 6. StoryboardAssetRepository
# --------------------------------------------------------------------------- #


class StoryboardAssetRepository:
    """Asset record management for storyboard_assets."""

    TABLE_NAME = "storyboard_assets"

    async def get_by_id(self, asset_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one asset row by snowflake id. Returns the SELECT *-shaped
        dict, or None when not found / on error (matching the error contract
        of the ``StoryboardService.get_asset`` reach-in this replaces)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardAssets)
                    .where(StoryboardAssets.id == _bigint(asset_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row, _ASSETS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to get asset {asset_id}: {e}")
            return None

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new asset record. Returns the created row dict."""
        try:
            values = _coerce_bigint_cols(
                _known_only(data, _ASSETS_ATTRS), ("id", "project_id")
            )
            async with write_scope() as session:
                result = await session.execute(
                    pg_insert(StoryboardAssets)
                    .values(**values)
                    .returning(StoryboardAssets)
                )
                row = result.scalars().first()
                out = _row(row, _ASSETS_N2A) if row else {}
            logger.info(f"Created storyboard asset: {data.get('file_path')}")
            return out
        except Exception as e:
            logger.error(f"Failed to create storyboard asset: {e}")
            raise

    async def find_by_hash(
        self, project_id: str, file_hash: str
    ) -> Optional[Dict[str, Any]]:
        """Look up an asset by its content hash within a project (dedup)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardAssets)
                    .where(StoryboardAssets.project_id == _bigint(project_id))
                    .where(StoryboardAssets.file_hash == file_hash)
                    .limit(1)
                )
                row = result.scalars().first()
                return _row(row, _ASSETS_N2A) if row else None
        except Exception as e:
            logger.error(f"Failed to find asset by hash in project {project_id}: {e}")
            return None

    async def delete(self, asset_id: str) -> None:
        """Hard-delete an asset record."""
        try:
            async with write_scope() as session:
                await session.execute(
                    delete(StoryboardAssets).where(
                        StoryboardAssets.id == _bigint(asset_id)
                    )
                )
            logger.info(f"Deleted storyboard asset {asset_id}")
        except Exception as e:
            logger.error(f"Failed to delete storyboard asset {asset_id}: {e}")
            raise

    async def list_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """Fetch all assets for a project, newest first."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(StoryboardAssets)
                    .where(StoryboardAssets.project_id == _bigint(project_id))
                    .order_by(StoryboardAssets.created_at.desc())
                )
                return [_row(r, _ASSETS_N2A) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to list assets for project {project_id}: {e}")
            return []


# --------------------------------------------------------------------------- #
# Factories — every storyboard repo is ORM-backed (post-rollout collapse). The
# factories are retained as the stable call-site seam the routers / services /
# workflows import.
# --------------------------------------------------------------------------- #


def get_storyboard_project_repository() -> StoryboardProjectRepository:
    """Return the StoryboardProjectRepository."""
    return StoryboardProjectRepository()


def get_storyboard_node_repository() -> StoryboardNodeRepository:
    """Return the StoryboardNodeRepository."""
    return StoryboardNodeRepository()


def get_storyboard_edge_repository() -> StoryboardEdgeRepository:
    """Return the StoryboardEdgeRepository."""
    return StoryboardEdgeRepository()


def get_storyboard_frame_repository() -> StoryboardFrameRepository:
    """Return the StoryboardFrameRepository."""
    return StoryboardFrameRepository()


def get_storyboard_character_repository() -> StoryboardCharacterRepository:
    """Return the StoryboardCharacterRepository."""
    return StoryboardCharacterRepository()


def get_storyboard_asset_repository() -> StoryboardAssetRepository:
    """Return the StoryboardAssetRepository."""
    return StoryboardAssetRepository()
