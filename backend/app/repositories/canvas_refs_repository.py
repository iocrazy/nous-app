# backend/app/repositories/canvas_refs_repository.py
"""Data access for ``canvas_resource_refs``.

Writes go through ``execute_as_service_role`` (the table is RLS-locked to
service_role; canvas membership was already checked at the route layer).
Reads use ``fetch_all`` (engine role bypasses RLS, same as the temp
sweeper's folder reads) and always JOIN with the caller's membership /
ownership filter so no cross-tenant row can leak.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.db import engine as db_engine


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
        await db_engine.execute_as_service_role(
            "DELETE FROM canvas_resource_refs WHERE canvas_id = :cid",
            {"cid": cid},
        )
        if not refs:
            return
        await db_engine.execute_as_service_role(
            "INSERT INTO canvas_resource_refs "
            "  (canvas_id, resource_id, role, node_id) "
            "SELECT :cid, rid, role, node_id "
            "FROM unnest("
            "  :rids::bigint[], :roles::text[], :node_ids::text[]"
            ") AS t(rid, role, node_id) "
            "ON CONFLICT (canvas_id, resource_id, node_id) DO NOTHING",
            {
                "cid": cid,
                "rids": [int(str(r["resource_id"])) for r in refs],
                "roles": [r["role"] for r in refs],
                "node_ids": [r["node_id"] for r in refs],
            },
        )

    # -- reads --------------------------------------------------------

    async def list_assets_for_canvas(self, canvas_id: str) -> List[Dict[str, Any]]:
        """Unique resources referenced by a canvas, newest first.

        A resource referenced by multiple nodes has multiple refs; we
        collapse to one row per resource (DISTINCT ON r.id) so the grid
        shows each file once. ``role``/``node_id`` reflect the most
        recent ref for that resource.
        """
        rows = await db_engine.fetch_all(
            "SELECT * FROM ("
            "  SELECT DISTINCT ON (r.id) "
            "    r.id::text AS id, r.filename, r.file_type, r.mime_type, "
            "    r.thumbnail_path, r.cover_image_path, r.created_at, "
            "    crr.role, crr.node_id "
            "  FROM canvas_resource_refs crr "
            "  JOIN resources r ON r.id = crr.resource_id "
            "  WHERE crr.canvas_id = :cid AND r.is_trashed = false "
            "  ORDER BY r.id, r.created_at DESC "
            ") sub ORDER BY created_at DESC",
            {"cid": int(str(canvas_id))},
        )
        return rows or []

    async def list_canvases_for_resource(
        self, resource_id: str
    ) -> List[Dict[str, Any]]:
        """Canvases that reference a resource (back-ref for detail page)."""
        rows = await db_engine.fetch_all(
            "SELECT DISTINCT c.id::text AS canvas_id, c.name AS canvas_name, "
            "       c.kind, c.project_id::text AS project_id, crr.role "
            "FROM canvas_resource_refs crr "
            "JOIN canvases c ON c.id = crr.canvas_id "
            "WHERE crr.resource_id = :rid "
            "ORDER BY c.name",
            {"rid": int(str(resource_id))},
        )
        return rows or []

    async def tree_for_projects(self, project_ids: List[str]) -> List[Dict[str, Any]]:
        """Per-canvas count of unique non-trashed referenced resources."""
        if not project_ids:
            return []
        ids = [int(str(p)) for p in project_ids]
        rows = await db_engine.fetch_all(
            "SELECT c.project_id::text AS project_id, c.id::text AS canvas_id, "
            "       c.name AS canvas_name, c.kind, "
            "       COUNT(DISTINCT crr.resource_id) "
            "         FILTER (WHERE r.id IS NOT NULL) AS asset_count "
            "FROM canvases c "
            "LEFT JOIN canvas_resource_refs crr ON crr.canvas_id = c.id "
            "LEFT JOIN resources r ON r.id = crr.resource_id AND r.is_trashed = false "
            "WHERE c.project_id = ANY(:ids) "
            "GROUP BY c.project_id, c.id, c.name, c.kind "
            "ORDER BY c.name",
            {"ids": ids},
        )
        return rows or []
