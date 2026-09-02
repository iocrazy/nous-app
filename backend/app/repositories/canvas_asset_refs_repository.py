# backend/app/repositories/canvas_asset_refs_repository.py
"""Data access for ``canvas_asset_refs`` (asset library P4).

The asset-library twin of ``canvas_refs_repository.py``. ORM only
(read_scope/write_scope + ``CanvasAssetRefs``). The table is RLS-locked to
service_role and the engine role (postgres) carries BYPASSRLS, and none of the
tables joined here (``canvases`` / ``projects`` / ``teams`` / ``assets`` /
``asset_loadouts``) carries a scope mixin — so no ``system_request_scope`` wrap
is needed, unlike the resource-refs sibling which joins the ``UserScoped``
``Resources``.

Authorization is the CALLER's job on both reads:
* ``list_for_canvas`` — the route runs the canvas read gate first.
* ``list_canvases_for_asset`` — takes the caller's ``scope_id`` and filters to
  canvases whose project resolves to it, so the RLS-locked table cannot leak a
  canvas from another team through an asset that happens to be shared.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import Text as SAText
from sqlalchemy import cast
from sqlalchemy import delete as sa_delete
from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    AssetLoadouts,
    Assets,
    CanvasAssetRefs,
    Canvases,
    Projects,
    Teams,
)

# The PK, spelled once. ``loadout_id`` is deliberately NOT in it (mig 445):
# one node carries one loadout, so a node re-saved with a different loadout is
# the SAME row with a new value — see ``replace_for_canvas``.
_CONFLICT_KEY = ["canvas_id", "asset_id", "node_id"]


class CanvasAssetRefsRepository:
    # -- writes -------------------------------------------------------

    async def replace_for_canvas(
        self, canvas_id: str, refs: List[Dict[str, Any]]
    ) -> None:
        """Replace ALL asset refs for a canvas with ``refs``.

        Two statements (DELETE then one multi-row INSERT), NOT wrapped in a
        single transaction — same rationale as the resource-refs sibling: refs
        are derived and rebuildable, so a partial failure is self-healing
        rather than corrupting, and replace-all keeps the logic trivially
        correct (the extracted set IS the desired state). Idempotent.

        ``on_conflict_do_update(set_={"loadout_id": ...})``, NOT
        ``do_nothing``: ``loadout_id`` is outside the primary key, so a node
        re-saved with a different loadout conflicts on the SAME key. Under
        ``do_nothing`` the pre-existing row would survive with the STALE
        loadout and the save would report success — the canvas would say
        "Night Raid" while the refs table still said "Court Dress". The
        DELETE above makes the conflict unreachable in the normal path; this
        clause is what keeps the statement correct on its own, so a future
        edit that makes the DELETE conditional cannot resurrect the bug
        silently.
        """
        cid = int(str(canvas_id))
        async with write_scope() as session:
            await session.execute(
                sa_delete(CanvasAssetRefs).where(CanvasAssetRefs.canvas_id == cid)
            )
        if not refs:
            return
        stmt = pg_insert(CanvasAssetRefs).values(
            [
                {
                    "canvas_id": cid,
                    "asset_id": int(r["asset_id"]),
                    "node_id": str(r["node_id"]),
                    "loadout_id": (
                        int(r["loadout_id"])
                        if r.get("loadout_id") is not None
                        else None
                    ),
                }
                for r in refs
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=_CONFLICT_KEY,
            set_={"loadout_id": stmt.excluded.loadout_id},
        )
        async with write_scope() as session:
            await session.execute(stmt)

    # -- reads --------------------------------------------------------

    async def list_for_canvas(self, canvas_id: str) -> List[Dict[str, Any]]:
        """Asset refs held by one canvas, with the asset (and loadout) named.

        INNER JOIN on ``assets`` with ``deleted_at IS NULL``: a soft-deleted
        asset's refs stay in the table (the FK only cascades on a HARD delete)
        and naming a deleted asset in the canvas panel would be noise. The
        loadout is a LEFT JOIN — ``loadout_id`` is nullable, and its FK is
        ``ON DELETE SET NULL``, so "no loadout" is a normal state, not a
        missing row.
        """
        cid = int(str(canvas_id))
        async with read_scope() as session:
            result = await session.execute(
                select(
                    cast(CanvasAssetRefs.asset_id, SAText).label("asset_id"),
                    CanvasAssetRefs.node_id,
                    cast(CanvasAssetRefs.loadout_id, SAText).label("loadout_id"),
                    Assets.name.label("asset_name"),
                    Assets.asset_type,
                    AssetLoadouts.name.label("loadout_name"),
                )
                .select_from(CanvasAssetRefs)
                .join(Assets, Assets.id == CanvasAssetRefs.asset_id)
                .join(
                    AssetLoadouts,
                    AssetLoadouts.id == CanvasAssetRefs.loadout_id,
                    isouter=True,
                )
                .where(CanvasAssetRefs.canvas_id == cid)
                .where(Assets.deleted_at.is_(None))
                .order_by(Assets.name, CanvasAssetRefs.node_id)
            )
            return [dict(m) for m in result.mappings().all()]

    async def list_canvases_for_asset(
        self, asset_id: str, scope_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Canvases referencing an asset, one row per canvas.

        ``node_ids`` / ``loadout_ids`` are aggregated so a canvas that uses the
        asset on three nodes appears ONCE with three node ids, rather than
        three times — the caller renders a canvas list, not a node list.

        ``scope_id`` limits the answer to canvases whose project resolves to
        that scope, using the SAME rule ``assets_router._project_scope_id``
        applies one project at a time: the project's ``team_id``, or the
        OWNER's personal team when it is NULL. Expressed as a LEFT JOIN on
        ``teams`` (kind='personal') plus a COALESCE so it stays one query —
        resolving it per row in Python would be N+1 and, worse, would tempt a
        caller to skip it. Without the filter a system-preset asset (readable
        from every scope) would list every team's canvases to anyone who could
        see it.
        """
        aid = int(str(asset_id))
        personal = (
            select(Teams.id, Teams.owner_id)
            .where(Teams.kind == "personal")
            .subquery("personal_team")
        )
        stmt = (
            select(
                cast(Canvases.id, SAText).label("canvas_id"),
                Canvases.name.label("canvas_name"),
                Canvases.kind,
                cast(Canvases.project_id, SAText).label("project_id"),
                # ``distinct()`` (the SQLAlchemy construct) rather than
                # ``func.distinct()``: the latter compiles to ``distinct(x)``,
                # which Postgres DOES accept inside an aggregate (it parses as
                # the DISTINCT keyword plus a parenthesised expression —
                # verified on pg17), so this is a spelling choice matching the
                # resource-refs sibling, not a bug fix. What IS load-bearing is
                # the DISTINCT itself: two nodes sharing one loadout would
                # otherwise repeat that id in ``loadout_ids``.
                func.array_agg(distinct(CanvasAssetRefs.node_id)).label("node_ids"),
                func.array_agg(
                    distinct(cast(CanvasAssetRefs.loadout_id, SAText))
                ).label("loadout_ids"),
            )
            .select_from(CanvasAssetRefs)
            .join(Canvases, Canvases.id == CanvasAssetRefs.canvas_id)
            .join(Projects, Projects.id == Canvases.project_id)
            .join(
                personal,
                personal.c.owner_id == Projects.owner_id,
                isouter=True,
            )
            .where(CanvasAssetRefs.asset_id == aid)
            .where(Canvases.deleted_at.is_(None))
            .group_by(Canvases.id, Canvases.name, Canvases.kind, Canvases.project_id)
            .order_by(Canvases.name)
        )
        if scope_id is not None:
            stmt = stmt.where(
                func.coalesce(Projects.team_id, personal.c.id) == int(str(scope_id))
            )
        async with read_scope() as session:
            result = await session.execute(stmt)
            rows = [dict(m) for m in result.mappings().all()]
        for row in rows:
            row["node_ids"] = sorted(n for n in (row.get("node_ids") or []) if n)
            # array_agg(DISTINCT ...) keeps a NULL element when some node has no
            # loadout; dropping it here means "loadout_ids" lists the loadouts
            # actually in use rather than carrying a null the client must filter.
            row["loadout_ids"] = sorted(
                lo for lo in (row.get("loadout_ids") or []) if lo
            )
        return rows
