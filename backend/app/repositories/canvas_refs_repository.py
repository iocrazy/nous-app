# backend/app/repositories/canvas_refs_repository.py
"""Data access for ``canvas_resource_refs``.

ORM session scopes (read_scope/write_scope + ``CanvasResourceRefs``). The
DISTINCT ON (``list_assets_for_canvas``) and FILTER-aggregate
(``tree_for_projects``) reads were raw ``text()`` SQL through Phase C's
final review — both migrated to ORM (Phase C final-review canvas_refs
sweep, 2026-08-05); see each method's own docstring for the scope-wrap
rationale (canvas/project membership, not creator_id, is the authorization
boundary for both). The table is RLS-locked to service_role and the engine
role (postgres) carries BYPASSRLS, so the session write path has the same
effective privileges the old ``execute_as_service_role`` calls had; canvas
membership was already checked at the route layer, and reads always JOIN
the caller's membership/ownership filter so no cross-tenant row leaks.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Dict, List

from sqlalchemy import Text as SAText
from sqlalchemy import and_, cast
from sqlalchemy import delete as sa_delete
from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.scope import is_enforced, system_request_scope
from app.db.session import read_scope, write_scope
from app.models import Canvases, CanvasResourceRefs, Resources


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
        recent ref for that resource. Postgres requires DISTINCT ON's
        columns to be the LEADING ORDER BY columns, so the inner query
        orders by (id, created_at DESC) and an outer wrapping subquery
        re-sorts by created_at DESC alone — same two-level shape the
        original SQL used, now expressed via ``.distinct(Resources.id)``
        + ``.subquery()``.

        Phase C final review (canvas_refs sweep, 2026-08-05): migrated off
        raw ``text()`` to ORM. ``Resources`` is a UserScoped model and this
        is an INNER JOIN (positively injectable) — without an explicit wrap,
        a real ambient user Scope would silently filter to that user's OWN
        resources, which is wrong here: canvas membership (already checked
        at the route layer via ``verify_project_write_access`` in
        ``project_assets_router.py``), not ``creator_id``, is the
        authorization boundary, and a canvas can reference resources from
        multiple contributors. Wrapped in ``system_request_scope`` (gated by
        ``is_enforced``) — same rationale as
        ``resources_repository.validate_scope_image_ids``.
        """
        cid = int(str(canvas_id))
        scope_cm = (
            system_request_scope(
                reason="list-assets-for-canvas: canvas/project membership "
                "is the authorization boundary (checked at the route "
                "layer), not creator_id — a canvas can reference resources "
                "from multiple contributors"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            async with read_scope() as session:
                inner = (
                    select(
                        cast(Resources.id, SAText).label("id"),
                        Resources.filename,
                        Resources.file_type,
                        Resources.mime_type,
                        Resources.thumbnail_path,
                        Resources.cover_image_path,
                        Resources.created_at,
                        CanvasResourceRefs.role,
                        CanvasResourceRefs.node_id,
                    )
                    .distinct(Resources.id)
                    .select_from(CanvasResourceRefs)
                    .join(Resources, Resources.id == CanvasResourceRefs.resource_id)
                    .where(CanvasResourceRefs.canvas_id == cid)
                    .where(Resources.is_trashed.is_(False))
                    .order_by(Resources.id, Resources.created_at.desc())
                ).subquery("sub")
                result = await session.execute(
                    select(inner).order_by(inner.c.created_at.desc())
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
        canvases never surface in the project-assets tree.

        Phase C final review (canvas_refs sweep, 2026-08-05): migrated off
        raw ``text()`` to ORM. ``r.is_trashed = false`` stays inside the
        JOIN's ON clause (not a WHERE filter) — moving it to WHERE would
        silently turn the LEFT JOIN into an INNER JOIN (dropping canvases
        with zero live resources instead of counting them as
        ``asset_count=0``), so it's expressed via ``and_()`` in
        ``.join(..., isouter=True)`` here too.

        ``Resources`` is scoped, and this LEFT JOINs it (to keep a canvas
        row even when it references zero live resources) — the choke point
        treats an OUTER JOIN target as non-filtering and fail-closed RAISEs
        under a real user Scope, same as the admin storage_router LATERAL
        reads. ``project_ids`` is the caller's OWN project list (resolved
        by the router from ``ProjectsRepository.get_user_projects``), but
        the resources counted per canvas can belong to any project
        contributor — canvas/project membership, not creator_id, is the
        authorization boundary. Wrapped in ``system_request_scope`` (gated
        by ``is_enforced``).
        """
        if not project_ids:
            return []
        ids = [int(str(p)) for p in project_ids]
        scope_cm = (
            system_request_scope(
                reason="tree-for-projects: canvas/project membership is "
                "the authorization boundary (project_ids is the caller's "
                "own project list), not creator_id — resources counted per "
                "canvas may belong to any project contributor"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        cast(Canvases.project_id, SAText).label("project_id"),
                        cast(Canvases.id, SAText).label("canvas_id"),
                        Canvases.name.label("canvas_name"),
                        Canvases.kind,
                        func.jsonb_array_length(Canvases.nodes_json).label(
                            "node_count"
                        ),
                        func.count(distinct(CanvasResourceRefs.resource_id))
                        .filter(Resources.id.isnot(None))
                        .label("asset_count"),
                    )
                    .select_from(Canvases)
                    .join(
                        CanvasResourceRefs,
                        CanvasResourceRefs.canvas_id == Canvases.id,
                        isouter=True,
                    )
                    .join(
                        Resources,
                        and_(
                            Resources.id == CanvasResourceRefs.resource_id,
                            Resources.is_trashed.is_(False),
                        ),
                        isouter=True,
                    )
                    .where(Canvases.project_id.in_(ids))
                    .where(Canvases.deleted_at.is_(None))
                    .group_by(
                        Canvases.project_id,
                        Canvases.id,
                        Canvases.name,
                        Canvases.kind,
                        Canvases.nodes_json,
                    )
                    .order_by(Canvases.name)
                )
                return [dict(m) for m in result.mappings().all()]
