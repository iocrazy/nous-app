"""Data access for asset_files / asset_links / asset_loadouts / asset_project_refs
(mig 445). Relation rows only — the invariants (slot validity, link types,
loadout ⊆ links) live in AssetsService; this layer is dumb and idempotent.

Read methods return NATIVE rows (int ids, datetimes) exactly like
``assets_repository`` — the caller serializes last, via the module-level
``_serialize_file`` / ``_serialize_link`` / ``_serialize_loadout`` helpers.
Note the loadout arrays (``costume_ids`` / ``prop_ids``) are ARRAY(BigInteger),
so ``_serialize_loadout`` stringifies element-wise, not just the row's own ids.
"""

from __future__ import annotations

import datetime
from contextlib import nullcontext
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.scope import is_enforced, system_request_scope
from app.db.session import read_scope, write_scope
from app.models import (
    AssetFiles,
    AssetLinks,
    AssetLoadouts,
    AssetProjectRefs,
    Assets,
    Projects,
    ResourceItems,
    Resources,
)


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime.datetime) else v


def _serialize_file(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "asset_id": str(row["asset_id"]),
        "resource_id": str(row["resource_id"]),
        "slot": row["slot"],
        "loadout_id": (
            str(row["loadout_id"]) if row.get("loadout_id") is not None else None
        ),
        "sort_order": row.get("sort_order", 0),
        "note": row.get("note"),
        "attached_by": str(row["attached_by"]) if row.get("attached_by") else None,
        "attached_at": _iso(row.get("attached_at")),
    }


def _serialize_link(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "from_asset_id": str(row["from_asset_id"]),
        "to_asset_id": str(row["to_asset_id"]),
        "relation": row["relation"],
        "created_at": _iso(row.get("created_at")),
    }


def _serialize_loadout(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "asset_id": str(row["asset_id"]),
        "name": row["name"],
        "is_default": bool(row["is_default"]),
        "costume_ids": [str(c) for c in (row.get("costume_ids") or [])],
        "prop_ids": [str(p) for p in (row.get("prop_ids") or [])],
        "prompt_extra": row.get("prompt_extra"),
        "sort_order": row.get("sort_order", 0),
        "created_at": _iso(row.get("created_at")),
    }


def _row(obj) -> Dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


class AssetRelationsRepository:
    # ── the parent row's clock ─────────────────────────────────────────────

    async def touch_asset(self, asset_id: int) -> bool:
        """Bump ``assets.updated_at`` — every relation write owes this.

        ``assets`` deliberately ships NO touch trigger (mig 445), and the
        relation tables are separate rows, so attaching a file / linking a
        costume / adding a loadout left the asset's own ``updated_at`` at
        whatever the last header edit set. The visible cost was the shelf:
        ``sort=recent`` orders by that column, so an asset the user had just
        finished filling in did not move.

        No scope predicate on purpose — like the batch derived lookups in
        ``assets_repository``, this keys on an asset_id the caller has ALREADY
        resolved through ``_require_writable``. Never call it with an id
        straight off the wire.
        """
        async with write_scope() as session:
            res = await session.execute(
                sa_update(Assets)
                .where(Assets.id == int(asset_id))
                .where(Assets.deleted_at.is_(None))
                .values(updated_at=func.now())
            )
            return (res.rowcount or 0) > 0

    # ── files ──────────────────────────────────────────────────────────────

    async def resource_in_scope(self, resource_id: int, scope_id: int) -> bool:
        stmt = (
            select(ResourceItems.resource_id)
            .where(ResourceItems.resource_id == int(resource_id))
            .where(ResourceItems.scope_id == int(scope_id))
            .limit(1)
        )
        async with read_scope() as session:
            return (await session.execute(stmt)).first() is not None

    async def resource_media_rows(
        self, resource_ids: List[int]
    ) -> Dict[int, Dict[str, Any]]:
        """The stored media columns of the given resources, keyed by id.

        Only what the reference-materialization step needs (``file_path`` /
        ``mime_type`` / the two derived-image columns). Batched: one statement
        for the whole reference list rather than one per file. A row that does
        not exist is simply ABSENT from the result — that is how the caller
        learns ``resource_not_found`` instead of getting a row of Nones.

        **The SYSTEM wrap is LOAD-BEARING in production, not decorative.**
        ``Resources`` carries ``UserScoped(creator_id)`` and this is the only
        ``Resources`` read on the assets router, which binds no tenant scope
        (no ``ScopedRequestDep``). ``SCOPE_ENFORCE_RESOURCES`` defaults to
        false in code but production sets it true via ``secrets/backend.env``
        (CLAUDE.md 部署陷阱: env overrides config.yml), so without this the
        ``do_orm_execute`` choke point sees a scoped table touched with no
        ambient scope and fail-closed raises ``UnscopedQueryError`` — an
        unhandled 500 on every generate-slot run whose asset has a file
        attached, i.e. every run the feature exists for. Same shape and same
        reason as ``app/main.py``'s media-serve lookup.

        SYSTEM rather than a per-user scope is the deliberate, auditable
        cross-user read: the caller has already passed ``_require_writable``
        on the asset, and the ids come from ``asset_files`` rather than from a
        request body. Injecting ``creator_id == caller`` instead would make a
        file attached from a TEAMMATE's resource vanish inside a team scope
        and be reported as ``resource_not_found`` — a wrong reason about a
        reference that exists.

        Gated on ``is_enforced`` (not unconditional) purely to stay
        byte-for-byte legacy where the flag really is off, e.g. this repo's
        own local/test default.

        Trashed resources are deliberately NOT filtered out: ``is_trashed`` is
        a shelf state, while the bytes and the ``asset_files`` attachment both
        still exist. Hiding them here would silently drop a reference the
        asset page still shows as attached.
        """
        if not resource_ids:
            return {}
        stmt = select(
            Resources.id,
            Resources.file_path,
            Resources.mime_type,
            Resources.thumbnail_path,
            Resources.cover_image_path,
        ).where(Resources.id.in_([int(r) for r in resource_ids]))
        scope_cm = (
            system_request_scope(
                reason="assets-generate-slot: resolve reference media paths"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            async with read_scope() as session:
                rows = (await session.execute(stmt)).mappings().all()
        return {int(r["id"]): dict(r) for r in rows}

    async def attach(
        self,
        asset_id: int,
        resource_id: int,
        slot: str,
        *,
        loadout_id: Optional[int] = None,
        note: Optional[str] = None,
        attached_by: Optional[str] = None,
        sort_order: int = 0,
    ) -> Dict[str, Any]:
        """Attach a resource to a slot (idempotent on the PK).

        ``sort_order`` is a parameter only because ``duplicate`` has to
        reproduce the source's manual ordering; the interactive attach path
        leaves it at the column default. It is deliberately NOT in the
        ON CONFLICT ``set_``: re-attaching an already-attached file must not
        silently reshuffle the slot the user arranged by hand.
        """
        stmt = pg_insert(AssetFiles).values(
            asset_id=int(asset_id),
            resource_id=int(resource_id),
            slot=slot,
            loadout_id=int(loadout_id) if loadout_id is not None else None,
            note=note,
            attached_by=attached_by,
            sort_order=int(sort_order),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                AssetFiles.asset_id,
                AssetFiles.resource_id,
                AssetFiles.slot,
            ],
            set_={"loadout_id": stmt.excluded.loadout_id, "note": stmt.excluded.note},
        ).returning(AssetFiles)
        async with write_scope() as session:
            obj = (await session.execute(stmt)).scalar_one()
            return _row(obj)

    async def detach(self, asset_id: int, resource_id: int, slot: str) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_delete(AssetFiles)
                .where(AssetFiles.asset_id == int(asset_id))
                .where(AssetFiles.resource_id == int(resource_id))
                .where(AssetFiles.slot == slot)
            )
            return (res.rowcount or 0) > 0

    async def list_files(self, asset_id: int) -> List[Dict[str, Any]]:
        stmt = (
            select(AssetFiles)
            .where(AssetFiles.asset_id == int(asset_id))
            .order_by(
                AssetFiles.slot.asc(),
                AssetFiles.sort_order.asc(),
                AssetFiles.attached_at.asc(),
            )
        )
        async with read_scope() as session:
            return [_row(o) for o in (await session.execute(stmt)).scalars().all()]

    # ── links ──────────────────────────────────────────────────────────────

    async def add_link(self, from_id: int, to_id: int, relation: str) -> Dict[str, Any]:
        stmt = (
            pg_insert(AssetLinks)
            .values(
                from_asset_id=int(from_id), to_asset_id=int(to_id), relation=relation
            )
            .on_conflict_do_nothing()
        )
        async with write_scope() as session:
            await session.execute(stmt)
            obj = (
                await session.execute(
                    select(AssetLinks)
                    .where(AssetLinks.from_asset_id == int(from_id))
                    .where(AssetLinks.to_asset_id == int(to_id))
                    .where(AssetLinks.relation == relation)
                )
            ).scalar_one()
            return _row(obj)

    async def remove_link(self, from_id: int, to_id: int, relation: str) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_delete(AssetLinks)
                .where(AssetLinks.from_asset_id == int(from_id))
                .where(AssetLinks.to_asset_id == int(to_id))
                .where(AssetLinks.relation == relation)
            )
            return (res.rowcount or 0) > 0

    async def list_links(
        self, asset_id: int
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        async with read_scope() as session:
            out = (
                (
                    await session.execute(
                        select(AssetLinks).where(
                            AssetLinks.from_asset_id == int(asset_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            inc = (
                (
                    await session.execute(
                        select(AssetLinks).where(
                            AssetLinks.to_asset_id == int(asset_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [_row(o) for o in out], [_row(o) for o in inc]

    async def link_targets(self, from_id: int, relation: str) -> Set[int]:
        stmt = (
            select(AssetLinks.to_asset_id)
            .where(AssetLinks.from_asset_id == int(from_id))
            .where(AssetLinks.relation == relation)
        )
        async with read_scope() as session:
            return {int(t) for (t,) in (await session.execute(stmt)).all()}

    # ── loadouts ───────────────────────────────────────────────────────────

    async def create_loadout(
        self, asset_id: int, fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        async with write_scope() as session:
            obj = AssetLoadouts(asset_id=int(asset_id), **fields)
            session.add(obj)
            await session.flush()
            await session.refresh(obj)
            return _row(obj)

    async def update_loadout(
        self, loadout_id: int, asset_id: int, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            rows = await self.list_loadouts(asset_id)
            return next((r for r in rows if int(r["id"]) == int(loadout_id)), None)
        async with write_scope() as session:
            res = await session.execute(
                sa_update(AssetLoadouts)
                .where(AssetLoadouts.id == int(loadout_id))
                .where(AssetLoadouts.asset_id == int(asset_id))
                .values(**fields)
                .returning(AssetLoadouts)
            )
            obj = res.scalar_one_or_none()
            return _row(obj) if obj else None

    async def delete_loadout(self, loadout_id: int, asset_id: int) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_delete(AssetLoadouts)
                .where(AssetLoadouts.id == int(loadout_id))
                .where(AssetLoadouts.asset_id == int(asset_id))
                .where(AssetLoadouts.is_default.is_(False))
            )
            return (res.rowcount or 0) > 0

    async def list_loadouts(self, asset_id: int) -> List[Dict[str, Any]]:
        stmt = (
            select(AssetLoadouts)
            .where(AssetLoadouts.asset_id == int(asset_id))
            .order_by(
                AssetLoadouts.is_default.desc(),
                AssetLoadouts.sort_order.asc(),
                AssetLoadouts.id.asc(),
            )
        )
        async with read_scope() as session:
            return [_row(o) for o in (await session.execute(stmt)).scalars().all()]

    async def set_default(self, loadout_id: int, asset_id: int) -> bool:
        """Make ``loadout_id`` the asset's default, in one transaction.

        Returns ``False`` when the loadout is not owned by ``asset_id`` (or
        does not exist) — **nothing changed** in that case, so the asset keeps
        whatever default it had. ``True`` = it is now the only default.

        Ownership is settled by a ``SELECT ... FOR UPDATE`` *before* any write,
        so the "not owned" path cannot leave the asset with no default at all.
        The siblings are cleared before the target is set: ``uq_loadout_default``
        (mig 445 line 75) is a partial unique *index*, which PostgreSQL checks
        row-by-row and cannot defer — setting the target first would collide
        with the existing default on the common path.
        """
        async with write_scope() as session:
            owned = (
                await session.execute(
                    select(AssetLoadouts.id)
                    .where(AssetLoadouts.id == int(loadout_id))
                    .where(AssetLoadouts.asset_id == int(asset_id))
                    .with_for_update()
                )
            ).first()
            if owned is None:
                return False
            await session.execute(
                sa_update(AssetLoadouts)
                .where(AssetLoadouts.asset_id == int(asset_id))
                .where(AssetLoadouts.id != int(loadout_id))
                .values(is_default=False)
            )
            await session.execute(
                sa_update(AssetLoadouts)
                .where(AssetLoadouts.id == int(loadout_id))
                .where(AssetLoadouts.asset_id == int(asset_id))
                .values(is_default=True)
            )
            return True

    async def strip_from_loadouts(
        self,
        asset_id: int,
        *,
        costume_id: Optional[int] = None,
        prop_id: Optional[int] = None,
    ) -> int:
        """Remove a costume/prop id from every loadout of ``asset_id`` (called
        when the corresponding link is removed). Returns rows touched.

        The read runs on the *same* session as the writes, under
        ``FOR UPDATE`` row locks: each UPDATE rewrites the whole array from the
        snapshot, so reading in a separate transaction would silently overwrite
        any concurrent loadout edit landing in between (lost update).
        """
        touched = 0
        async with write_scope() as session:
            objs = (
                (
                    await session.execute(
                        select(AssetLoadouts)
                        .where(AssetLoadouts.asset_id == int(asset_id))
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for obj in objs:
                r = _row(obj)
                new_c = [
                    c
                    for c in r["costume_ids"]
                    if costume_id is None or int(c) != int(costume_id)
                ]
                new_p = [
                    p
                    for p in r["prop_ids"]
                    if prop_id is None or int(p) != int(prop_id)
                ]
                if new_c != list(r["costume_ids"]) or new_p != list(r["prop_ids"]):
                    await session.execute(
                        sa_update(AssetLoadouts)
                        .where(AssetLoadouts.id == int(r["id"]))
                        .values(costume_ids=new_c, prop_ids=new_p)
                    )
                    touched += 1
        return touched

    # ── project refs ───────────────────────────────────────────────────────

    async def project_team_id(
        self, project_id: int
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """``(exists, team_id, owner_id)``. ``team_id`` None = personal project.

        The owner rides along because a personal project's asset scope IS the
        owner's personal team — the caller cannot substitute its own without
        writing a row the read path would never return.
        """
        stmt = select(Projects.team_id, Projects.owner_id).where(
            Projects.id == int(project_id)
        )
        async with read_scope() as session:
            row = (await session.execute(stmt)).first()
        if row is None:
            return False, None, None
        team_id, owner_id = row
        return (
            True,
            (int(team_id) if team_id is not None else None),
            (str(owner_id) if owner_id is not None else None),
        )

    async def link_project(
        self, asset_id: int, project_id: int, linked_by: Optional[str]
    ) -> bool:
        stmt = (
            pg_insert(AssetProjectRefs)
            .values(
                asset_id=int(asset_id), project_id=int(project_id), linked_by=linked_by
            )
            .on_conflict_do_nothing()
        )
        async with write_scope() as session:
            res = await session.execute(stmt)
            return (res.rowcount or 0) > 0

    async def unlink_project(self, asset_id: int, project_id: int) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_delete(AssetProjectRefs)
                .where(AssetProjectRefs.asset_id == int(asset_id))
                .where(AssetProjectRefs.project_id == int(project_id))
            )
            return (res.rowcount or 0) > 0

    async def list_project_ids(self, asset_id: int) -> List[int]:
        stmt = select(AssetProjectRefs.project_id).where(
            AssetProjectRefs.asset_id == int(asset_id)
        )
        async with read_scope() as session:
            return [int(p) for (p,) in (await session.execute(stmt)).all()]
