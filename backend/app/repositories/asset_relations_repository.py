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
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import (
    AssetFiles,
    AssetLinks,
    AssetLoadouts,
    AssetProjectRefs,
    Projects,
    ResourceItems,
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

    async def attach(
        self,
        asset_id: int,
        resource_id: int,
        slot: str,
        *,
        loadout_id: Optional[int] = None,
        note: Optional[str] = None,
        attached_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        stmt = pg_insert(AssetFiles).values(
            asset_id=int(asset_id),
            resource_id=int(resource_id),
            slot=slot,
            loadout_id=int(loadout_id) if loadout_id is not None else None,
            note=note,
            attached_by=attached_by,
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

    async def set_default(self, loadout_id: int, asset_id: int) -> None:
        async with write_scope() as session:
            await session.execute(
                sa_update(AssetLoadouts)
                .where(AssetLoadouts.asset_id == int(asset_id))
                .values(is_default=False)
            )
            await session.execute(
                sa_update(AssetLoadouts)
                .where(AssetLoadouts.id == int(loadout_id))
                .where(AssetLoadouts.asset_id == int(asset_id))
                .values(is_default=True)
            )

    async def strip_from_loadouts(
        self,
        asset_id: int,
        *,
        costume_id: Optional[int] = None,
        prop_id: Optional[int] = None,
    ) -> int:
        """Remove a costume/prop id from every loadout of ``asset_id`` (called
        when the corresponding link is removed). Returns rows touched."""
        touched = 0
        rows = await self.list_loadouts(asset_id)
        async with write_scope() as session:
            for r in rows:
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

    async def project_team_id(self, project_id: int) -> Tuple[bool, Optional[int]]:
        """(exists, team_id). team_id None = personal project."""
        stmt = select(Projects.team_id).where(Projects.id == int(project_id))
        async with read_scope() as session:
            row = (await session.execute(stmt)).first()
        if row is None:
            return False, None
        return True, (int(row[0]) if row[0] is not None else None)

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
