# backend/app/repositories/canvas_refs_repository.py
"""Data access for ``canvas_resource_refs``.

ORM session scopes (read_scope/write_scope + ``CanvasResourceRefs``); the
DISTINCT ON / FILTER-aggregate read bodies stay SQL (documented exceptions
per the convergence doctrine). The table is RLS-locked to service_role and
the engine role (postgres) carries BYPASSRLS, so the session write path has
the same effective privileges the old ``execute_as_service_role`` calls had;
canvas membership was already checked at the route layer, and reads always
JOIN the caller's membership/ownership filter so no cross-tenant row leaks.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import Text as SAText
from sqlalchemy import cast
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import Canvases, CanvasResourceRefs


class CanvasRefsRepository:
    # -- writes -------------------------------------------------------

    async def replace_for_canvas(
        self, canvas_id: str, refs: List[Dict[str, str]]
    ) -> None:
        """Replace ALL refs for a canvas with ``refs``.

        Two statements (DELETE then a single multi-row INSERT) — NOT wrapped
        in one explicit transaction. That's acceptable here: refs are derived
        and rebuildable (the backfill script reconstructs them from
        nodes_json), so a partial failure is self-healing rather than
        corrupting. Replace-all keeps the logic trivially correct: the
        extracted set IS the desired state. Idempotent.
        """
        cid = int(str(canvas_id))
        async with write_scope() as session:
            await session.execute(
                sa_delete(CanvasResourceRefs).where(CanvasResourceRefs.canvas_id == cid)
            )
        if not refs:
            return
        stmt = (
            pg_insert(CanvasResourceRefs)
            .values(
                [
                    {
                        "canvas_id": cid,
                        "resource_id": int(str(r["resource_id"])),
                        "role": r["role"],
                        "node_id": r["node_id"],
                    }
                    for r in refs
                ]
            )
            .on_conflict_do_nothing(
                index_elements=["canvas_id", "resource_id", "node_id"]
            )
        )
        async with write_scope() as session:
            await session.execute(stmt)

    # -- reads --------------------------------------------------------

    async def list_assets_for_canvas(self, canvas_id: str) -> List[Dict[str, Any]]:
        """Unique resources referenced by a canvas, newest first.

        A resource referenced by multiple nodes has multiple refs; we
        collapse to one row per resource (DISTINCT ON r.id) so the grid
        shows each file once. ``role``/``node_id`` reflect the most
        recent ref for that resource. SQL body kept: DISTINCT ON + the
        wrapping reorder subselect are the semantics."""
        async with read_scope() as session:
            result = await session.execute(
                text(
                    "SELECT * FROM ("
                    "  SELECT DISTINCT ON (r.id) "
                    "    r.id::text AS id, r.filename, r.file_type, r.mime_type, "
                    "    r.thumbnail_path, r.cover_image_path, r.created_at, "
                    "    crr.role, crr.node_id "
                    "  FROM canvas_resource_refs crr "
                    "  JOIN resources r ON r.id = crr.resource_id "
                    "  WHERE crr.canvas_id = :cid AND r.is_trashed = false "
                    "  ORDER BY r.id, r.created_at DESC "
                    ") sub ORDER BY created_at DESC"
                ),
                {"cid": int(str(canvas_id))},
            )
            return [dict(m) for m in result.mappings().all()]

    async def list_canvases_for_resource(
        self, resource_id: str
    ) -> List[Dict[str, Any]]:
        """Canvases that reference a resource (back-ref for detail page)."""
        async with read_scope() as session:
            result = await session.execute(
                select(
                    cast(Canvases.id, SAText).label("canvas_id"),
                    Canvases.name.label("canvas_name"),
                    Canvases.kind,
                    cast(Canvases.project_id, SAText).label("project_id"),
                    CanvasResourceRefs.role,
                )
                .select_from(CanvasResourceRefs)
                .join(Canvases, Canvases.id == CanvasResourceRefs.canvas_id)
                .where(
                    CanvasResourceRefs.resource_id == int(str(resource_id)),
                    Canvases.deleted_at.is_(None),
                )
                .distinct()
                .order_by(Canvases.name)
            )
            return [dict(m) for m in result.mappings().all()]

    async def tree_for_projects(self, project_ids: List[str]) -> List[Dict[str, Any]]:
        """Per-canvas count of unique non-trashed referenced resources, plus
        the canvas node count (``nodes_json`` is ``NOT NULL DEFAULT '[]'`` so
        the array length is always defined). The router drops canvases that
        are empty on both axes (zero assets AND zero nodes) so orphaned blank
        canvases never surface in the project-assets tree. SQL body kept:
        jsonb_array_length + the FILTER aggregate are the semantics."""
        if not project_ids:
            return []
        ids = [int(str(p)) for p in project_ids]
        async with read_scope() as session:
            result = await session.execute(
                text(
                    "SELECT c.project_id::text AS project_id, "
                    "       c.id::text AS canvas_id, "
                    "       c.name AS canvas_name, c.kind, "
                    "       jsonb_array_length(c.nodes_json) AS node_count, "
                    "       COUNT(DISTINCT crr.resource_id) "
                    "         FILTER (WHERE r.id IS NOT NULL) AS asset_count "
                    "FROM canvases c "
                    "LEFT JOIN canvas_resource_refs crr ON crr.canvas_id = c.id "
                    "LEFT JOIN resources r "
                    "  ON r.id = crr.resource_id AND r.is_trashed = false "
                    "WHERE c.project_id = ANY(:ids) "
                    "  AND c.deleted_at IS NULL "
                    "GROUP BY c.project_id, c.id, c.name, c.kind, c.nodes_json "
                    "ORDER BY c.name"
                ),
                {"ids": ids},
            )
            return [dict(m) for m in result.mappings().all()]
