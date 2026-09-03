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
    ) -> int:
        """Replace ALL asset refs for a canvas with ``refs``.

        Returns how many refs had their ``loadout_id`` dropped as not belonging
        to their asset (see ``_null_unowned_loadouts``) — reported, never
        silent.

        DELETE-then-INSERT, both in ONE ``write_scope()`` session, i.e. ONE
        transaction. The resource-refs sibling deliberately uses two, arguing
        that a partial failure is "self-healing rather than corrupting". For
        THIS table that argument is false, and was measured on a real server:
        one node carrying a well-formed snowflake for an asset that does not
        exist makes the INSERT raise, and with the DELETE already committed the
        canvas is left with NO refs at all — including the refs of every other
        asset on it. It stays that way, because each later save re-runs the same
        DELETE and fails the same INSERT, and the backfill fails identically. The
        visible result is ``used_in`` answering "used nowhere" for an asset the
        user can see on the canvas: a wrong answer with a 200 on it, which is the
        very failure the ``DO UPDATE`` clause below exists to prevent.

        One transaction makes the rollback do what the sentence claimed: a
        failed INSERT restores the previous refs, so the mirror is stale rather
        than empty, and the next good save fixes it. That vector is rare today
        but grows with this plan — P4 Task 4 makes asset nodes copyable between
        canvases and Task 6 migrates legacy cards, both of which can plant an
        ``asset_id`` the target scope no longer has.

        Replace-all keeps the logic trivially correct (the extracted set IS the
        desired state) and idempotent.

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
        rows = [
            {
                "canvas_id": cid,
                "asset_id": int(r["asset_id"]),
                "node_id": str(r["node_id"]),
                "loadout_id": (
                    int(r["loadout_id"]) if r.get("loadout_id") is not None else None
                ),
            }
            for r in refs
        ]
        async with write_scope() as session:
            await session.execute(
                sa_delete(CanvasAssetRefs).where(CanvasAssetRefs.canvas_id == cid)
            )
            if not rows:
                return 0
            disowned = await self._null_unowned_loadouts(session, rows)
            stmt = pg_insert(CanvasAssetRefs).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=_CONFLICT_KEY,
                set_={"loadout_id": stmt.excluded.loadout_id},
            )
            await session.execute(stmt)
        return disowned

    @staticmethod
    async def _null_unowned_loadouts(session, rows: List[Dict[str, Any]]) -> int:
        """Blank any ``loadout_id`` that does not belong to its row's asset, and
        return how many were blanked. Mutates ``rows`` in place.

        The FK only requires the loadout row to EXIST — nothing in the schema
        ties it to ``asset_id``. Without this, a node pairing asset A with a
        loadout of asset B is stored verbatim and ``used_in.loadout_ids`` then
        names a costume that asset does not own: the mirror is described as the
        source of truth for which loadout is in use, so it must not assert a
        pairing the asset library would reject.

        NULL (plus a count the caller logs) rather than dropping the whole ref:
        the node really does reference the asset, and "this asset is on this
        canvas" is the more important half of the fact. Runs in the caller's
        write transaction, so it reads the same snapshot the INSERT writes.
        """
        wanted = {
            (r["loadout_id"], r["asset_id"])
            for r in rows
            if r["loadout_id"] is not None
        }
        if not wanted:
            return 0
        result = await session.execute(
            select(AssetLoadouts.id, AssetLoadouts.asset_id).where(
                AssetLoadouts.id.in_({lid for lid, _ in wanted})
            )
        )
        owned = {(int(lid), int(aid)) for lid, aid in result.all()}
        disowned = 0
        for r in rows:
            lid = r["loadout_id"]
            if lid is not None and (lid, r["asset_id"]) not in owned:
                r["loadout_id"] = None
                disowned += 1
        return disowned

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
