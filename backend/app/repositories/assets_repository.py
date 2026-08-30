"""Data access for ``assets`` (mig 445).

ORM-backed (read_scope/write_scope). Every method carries an explicit
``scope_id`` predicate — the model has no scope mixin (ProjectCharacters
stance), so tenancy lives here. Snowflake BIGINTs ride as strings at the API
boundary via ``_serialize``; the derived fields (readiness / counts) are
computed by ``with_derived`` from batch queries so list pages cost O(1) round
trips, not O(n). Read methods return NATIVE rows (int ids) because
``with_derived`` keys on them — the caller serializes last.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from app.db.session import read_scope, write_scope
from app.models import AssetFiles, AssetLoadouts, AssetProjectRefs, Assets
from app.services.assets.slots import readiness

_BIGINT_COLS = ("id", "scope_id", "cover_file_id", "duplicated_from")


class DuplicateAssetName(Exception):
    """uq_assets_scope_type_name hit — the router turns this into 409."""

    def __init__(self, existing_id: int):
        self.existing_id = existing_id
        super().__init__(f"asset with same name/type exists: {existing_id}")


def _row_dict(obj: Assets) -> Dict[str, Any]:
    return {col.name: getattr(obj, col.name) for col in obj.__table__.columns}


def _serialize(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in row.items():
        if key in _BIGINT_COLS and val is not None:
            out[key] = str(val)
        elif isinstance(val, datetime.datetime):
            out[key] = val.isoformat()
        elif key == "created_by" and val is not None:
            out[key] = str(val)
        else:
            out[key] = val
    return out


def with_derived(
    row: Dict[str, Any],
    slot_counts: Dict[int, Dict[str, int]],
    project_ids: Dict[int, List[int]],
    loadout_counts: Dict[int, int],
) -> Dict[str, Any]:
    """Attach readiness / file_counts_by_slot / project_ids / loadout_count.
    ``row['id']`` must be the native int here (call before _serialize)."""
    aid = int(row["id"])
    counts = slot_counts.get(aid, {})
    out = dict(row)
    out["readiness"] = readiness(row["asset_type"], counts, row.get("prompt_positive"))
    out["file_counts_by_slot"] = counts
    out["project_ids"] = [str(p) for p in project_ids.get(aid, [])]
    out["loadout_count"] = loadout_counts.get(aid, 0)
    return out


# ``tags`` is a jsonb OBJECT of groups → arrays ({"role": ["hero"], "era": [...]}),
# so ``@>`` cannot answer "does any group contain this value" without knowing the
# group name. jsonpath can: ``$.*`` walks the group values and ``[*]`` their
# members (lax mode, the default, also matches a group whose value is a bare
# scalar). The path is OUR constant — a literal_column, never caller text — while
# the value rides in as a bound parameter via jsonb_build_object.
_TAG_JSONPATH = literal_column("'$.*[*] ? (@ == $v)'::jsonpath")


def _tag_match(tag: str):
    return func.jsonb_path_exists(
        Assets.tags, _TAG_JSONPATH, func.jsonb_build_object("v", tag)
    )


def _order_by(sort: str):
    """ORDER BY for the two orderings SQL can answer.

    ``readiness`` is DERIVED per row (``with_derived``), so it is not here: the
    service asks for ``recent`` and re-sorts after deriving. An unknown value
    raises rather than falling back — a sort that silently answers a different
    question looks exactly like one that worked.

    Note ``sort_order`` is deliberately NOT a leading key any more (it was, when
    there was a single implicit ordering): a manual-order column ahead of the
    requested sort would make "by name" mean "by name inside manual buckets".
    """
    if sort == "recent":
        return (Assets.updated_at.desc(), Assets.id.desc())
    if sort == "name":
        return (func.lower(Assets.name).asc(), Assets.id.asc())
    raise ValueError(f"unsupported sort: {sort!r}")


class AssetsRepository:
    TABLE = "assets"

    async def create(
        self, scope_id: int, fields: Dict[str, Any], created_by: Optional[str]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                obj = Assets(**fields, scope_id=int(scope_id), created_by=created_by)
                session.add(obj)
                await session.flush()
                await session.refresh(obj)
                return _row_dict(obj)
        except IntegrityError as e:
            if "uq_assets_scope_type_name" not in str(e.orig):
                raise
            existing = await self.find_by_name(
                scope_id, fields["asset_type"], fields["name"]
            )
            raise DuplicateAssetName(existing_id=int(existing["id"]) if existing else 0)

    async def create_raw(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """INSERT from an explicit FULL column dict (``scope_id`` /
        ``source`` / ``duplicated_from`` / ``is_system_preset`` included).

        ``create`` exists for the schema-shaped path, where the caller only
        supplies ``AssetCreate`` fields and the row's provenance is implied.
        ``duplicate`` owns all of it, so it hands over the whole dict.

        The other difference is load-bearing: a ``uq_assets_scope_type_name``
        hit raises ``DuplicateAssetName`` with ``existing_id=0`` — **no
        follow-up SELECT for the real id**. This runs inside the caller's
        ``unit_of_work()``, where the IntegrityError has ALREADY aborted the
        transaction: any further statement on that session is a
        PendingRollbackError (an untyped 500) instead of the typed 409 the
        caller earned. The caller resolves the existing id with
        ``find_by_name`` BEFORE inserting; reaching this raise means a
        concurrent insert won the race between that check and this one, and the
        409 goes out without the id rather than not at all.
        """
        try:
            async with write_scope() as session:
                obj = Assets(**fields)
                session.add(obj)
                await session.flush()
                await session.refresh(obj)
                return _row_dict(obj)
        except IntegrityError as e:
            if "uq_assets_scope_type_name" not in str(e.orig):
                raise
            raise DuplicateAssetName(existing_id=0)

    async def find_by_name(
        self, scope_id: int, asset_type: str, name: str
    ) -> Optional[Dict[str, Any]]:
        stmt = (
            select(Assets)
            .where(Assets.scope_id == int(scope_id))
            .where(Assets.asset_type == asset_type)
            .where(func.lower(Assets.name) == name.lower())
            .where(Assets.deleted_at.is_(None))
            .limit(1)
        )
        async with read_scope() as session:
            obj = (await session.execute(stmt)).scalar_one_or_none()
        return _row_dict(obj) if obj else None

    async def get(self, asset_id: int, scope_id: int) -> Optional[Dict[str, Any]]:
        # System presets (scope_id NULL) are readable from every scope.
        stmt = (
            select(Assets)
            .where(Assets.id == int(asset_id))
            .where(
                or_(Assets.scope_id == int(scope_id), Assets.is_system_preset.is_(True))
            )
            .where(Assets.deleted_at.is_(None))
        )
        async with read_scope() as session:
            obj = (await session.execute(stmt)).scalar_one_or_none()
        return _row_dict(obj) if obj else None

    def _list_stmt(
        self,
        scope_id: int,
        *,
        asset_type: Optional[str] = None,
        project_id: Optional[int] = None,
        q: Optional[str] = None,
        tag: Optional[str] = None,
        sort: str = "recent",
        limit: int = 60,
        offset: int = 0,
    ):
        """The SELECT behind :meth:`list`, split out so it can be compiled and
        asserted without a database (tests/services/assets/test_assets_repository_sql.py).
        """
        limit = max(1, min(int(limit), 200))
        stmt = (
            select(Assets)
            .where(
                or_(Assets.scope_id == int(scope_id), Assets.is_system_preset.is_(True))
            )
            .where(Assets.deleted_at.is_(None))
        )
        if asset_type:
            stmt = stmt.where(Assets.asset_type == asset_type)
        if project_id is not None:
            stmt = stmt.where(
                Assets.id.in_(
                    select(AssetProjectRefs.asset_id).where(
                        AssetProjectRefs.project_id == int(project_id)
                    )
                )
            )
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(Assets.name.ilike(like), Assets.description.ilike(like))
            )
        if tag:
            stmt = stmt.where(_tag_match(tag))
        stmt = stmt.order_by(*_order_by(sort)).limit(limit).offset(max(0, int(offset)))
        return stmt

    async def list(self, scope_id: int, **filters: Any) -> List[Dict[str, Any]]:
        stmt = self._list_stmt(int(scope_id), **filters)
        async with read_scope() as session:
            objs = (await session.execute(stmt)).scalars().all()
        return [_row_dict(o) for o in objs]

    async def update(
        self, asset_id: int, scope_id: int, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            return await self.get(asset_id, scope_id)
        fields = {**fields, "updated_at": datetime.datetime.now(datetime.timezone.utc)}
        try:
            async with write_scope() as session:
                res = await session.execute(
                    sa_update(Assets)
                    .where(Assets.id == int(asset_id))
                    .where(Assets.scope_id == int(scope_id))
                    .where(Assets.deleted_at.is_(None))
                    .values(**fields)
                    .returning(Assets)
                )
                obj = res.scalar_one_or_none()
                return _row_dict(obj) if obj else None
        except IntegrityError as e:
            if "uq_assets_scope_type_name" not in str(e.orig):
                raise
            current = await self.get(asset_id, scope_id)
            existing = (
                await self.find_by_name(
                    scope_id,
                    fields.get("asset_type", current["asset_type"]),
                    fields.get("name", current["name"]),
                )
                if current
                else None
            )
            raise DuplicateAssetName(existing_id=int(existing["id"]) if existing else 0)

    async def soft_delete(self, asset_id: int, scope_id: int) -> bool:
        async with write_scope() as session:
            res = await session.execute(
                sa_update(Assets)
                .where(Assets.id == int(asset_id))
                .where(Assets.scope_id == int(scope_id))
                .where(Assets.deleted_at.is_(None))
                .values(deleted_at=datetime.datetime.now(datetime.timezone.utc))
            )
            return (res.rowcount or 0) > 0

    # ── batch derived lookups (list pages) ────────────────────────────────
    # These key on asset_ids alone: the tenancy check already happened in the
    # get()/list() that produced those ids. Never call them with ids straight
    # off the wire.

    async def slot_counts(self, asset_ids: List[int]) -> Dict[int, Dict[str, int]]:
        if not asset_ids:
            return {}
        stmt = (
            select(AssetFiles.asset_id, AssetFiles.slot, func.count())
            .where(AssetFiles.asset_id.in_([int(a) for a in asset_ids]))
            .group_by(AssetFiles.asset_id, AssetFiles.slot)
        )
        out: Dict[int, Dict[str, int]] = {}
        async with read_scope() as session:
            for aid, slot, n in (await session.execute(stmt)).all():
                out.setdefault(int(aid), {})[slot] = int(n)
        return out

    async def project_ids(self, asset_ids: List[int]) -> Dict[int, List[int]]:
        if not asset_ids:
            return {}
        stmt = select(AssetProjectRefs.asset_id, AssetProjectRefs.project_id).where(
            AssetProjectRefs.asset_id.in_([int(a) for a in asset_ids])
        )
        out: Dict[int, List[int]] = {}
        async with read_scope() as session:
            for aid, pid in (await session.execute(stmt)).all():
                out.setdefault(int(aid), []).append(int(pid))
        return out

    async def loadout_counts(self, asset_ids: List[int]) -> Dict[int, int]:
        if not asset_ids:
            return {}
        stmt = (
            select(AssetLoadouts.asset_id, func.count())
            .where(AssetLoadouts.asset_id.in_([int(a) for a in asset_ids]))
            .group_by(AssetLoadouts.asset_id)
        )
        async with read_scope() as session:
            return {int(a): int(n) for a, n in (await session.execute(stmt)).all()}
