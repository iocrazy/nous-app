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
        """Replace ALL refs for a canvas with ``refs`` in one transaction.

        Replace-all (not diff) keeps the logic trivially correct: the
        extracted set IS the desired state. Idempotent.
        """
        cid = int(str(canvas_id))
        await db_engine.execute_as_service_role(
            "DELETE FROM canvas_resource_refs WHERE canvas_id = :cid",
            {"cid": cid},
        )
        for r in refs:
            await db_engine.execute_as_service_role(
                "INSERT INTO canvas_resource_refs "
                "  (canvas_id, resource_id, role, node_id) "
                "VALUES (:cid, :rid, :role, :node_id) "
                "ON CONFLICT (canvas_id, resource_id, node_id) DO NOTHING",
                {
                    "cid": cid,
                    "rid": int(str(r["resource_id"])),
                    "role": r["role"],
                    "node_id": r["node_id"],
                },
            )

    # -- reads --------------------------------------------------------

    async def list_assets_for_canvas(self, canvas_id: str) -> List[Dict[str, Any]]:
        """Resources referenced by a canvas, with role. Newest first."""
        rows = await db_engine.fetch_all(
            "SELECT r.id::text AS id, r.filename, r.file_type, r.mime_type, "
            "       r.thumbnail_path, r.cover_image_path, r.created_at, "
            "       crr.role, crr.node_id "
            "FROM canvas_resource_refs crr "
            "JOIN resources r ON r.id = crr.resource_id "
            "WHERE crr.canvas_id = :cid AND r.is_trashed = false "
            "ORDER BY r.created_at DESC",
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
        """Per-canvas asset counts for the given projects (tree payload)."""
        if not project_ids:
            return []
        ids = [int(str(p)) for p in project_ids]
        rows = await db_engine.fetch_all(
            "SELECT c.project_id::text AS project_id, c.id::text AS canvas_id, "
            "       c.name AS canvas_name, c.kind, "
            "       COUNT(DISTINCT (crr.resource_id, crr.node_id))"
            "         FILTER (WHERE crr.resource_id IS NOT NULL) AS asset_count "
            "FROM canvases c "
            "LEFT JOIN canvas_resource_refs crr ON crr.canvas_id = c.id "
            "WHERE c.project_id = ANY(:ids) "
            "GROUP BY c.project_id, c.id, c.name, c.kind "
            "ORDER BY c.name",
            {"ids": ids},
        )
        return rows or []
