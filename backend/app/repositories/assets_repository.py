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

from app.db.session import in_unit_of_work, read_scope, write_scope
from app.models import AssetFiles, AssetLoadouts, AssetProjectRefs, Assets
from app.models.assets import ASSET_TYPES
from app.services.assets.slots import readiness

_BIGINT_COLS = ("id", "scope_id", "cover_file_id", "duplicated_from")


class DuplicateAssetName(Exception):
    """uq_assets_scope_type_name hit — the router turns this into 409."""

    def __init__(self, existing_id: int):
        self.existing_id = existing_id
        super().__init__(f"asset with same name/type exists: {existing_id}")


def _like_escape(text: str) -> str:
    """Escape the ILIKE metacharacters in USER text so it matches literally.

    Only the caller's substring goes through here — the surrounding ``%``
    wildcards are ours and must stay live. Without it a search for ``a_b``
    also matches ``axb`` and a search for ``%`` matches every row: the filter
    silently WIDENS, which reads as "search is broken" rather than as an
    error. ``\\`` is escaped FIRST; doing it after ``%``/``_`` would
    re-escape the backslashes this function had just added.

    The result is only correct when passed with ``escape="\\"`` — otherwise
    PostgreSQL has no escape character and the backslashes are literal
    pattern characters.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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


LIBRARY_FILTERS = ("in", "out", "all")


def _library_predicate(library: str):
    """The ``assets.in_library`` predicate for one ``library=`` value, or None
    for ``all``.

    Returns None rather than a tautology (``true``) for ``all`` so the caller
    emits NO predicate at all — a compiled-SQL pin can then tell "unfiltered"
    from "filtered to everything", which a ``WHERE true`` would hide.

    An unknown value RAISES, for the same reason ``_order_by`` does: silently
    falling back to ``in`` would answer a different question than the caller
    asked and look exactly like a working filter. The router pins the
    vocabulary with a ``pattern=``, so reaching this raise is a programming
    error, not user input.
    """
    if library == "all":
        return None
    if library == "in":
        return Assets.in_library.is_(True)
    if library == "out":
        return Assets.in_library.is_(False)
    raise ValueError(f"unsupported library filter: {library!r}")


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
        """INSERT from the ``AssetCreate``-shaped field dict.

        The 409 path resolves the clashing row's id by SELECTing AFTER the
        IntegrityError, which is only safe when this method owns its
        transaction: the failed INSERT aborts whatever transaction it ran in,
        and a ``write_scope()`` of our own is thrown away (the follow-up
        ``find_by_name`` then opens a clean session). Inside an ambient
        ``unit_of_work()`` that same SELECT lands on the caller's now-aborted
        transaction and raises ``PendingRollbackError`` — an untyped 500 in
        place of the 409. ``create_raw`` never looks up for exactly this
        reason (integration case 15 pins the difference; a stubbed session
        cannot tell the two apart).

        So under a UoW we raise with ``existing_id=0`` instead. That is not a
        loss of information in practice: a caller opening a UoW around this
        (``AssetsService.create_asset``) pre-checks with ``find_by_name`` and
        answers the 409 from there, and only loses the id on the genuine
        concurrent-insert race — where 409-without-the-id still beats a 500.
        """
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
            if in_unit_of_work():
                raise DuplicateAssetName(existing_id=0)
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
        library: str = "in",
        sort: str = "recent",
        limit: int = 60,
        offset: int = 0,
    ):
        """The SELECT behind :meth:`list`, split out so it can be compiled and
        asserted without a database (tests/services/assets/test_assets_repository_sql.py).

        ``library`` defaults to ``"in"`` HERE as well as at the router, and that
        is deliberate duplication: this is the shelf's query, and a repo default
        of "all" would mean any caller that forgot the argument silently widened
        the library to include project-originated rows. The one caller that
        genuinely wants both states (``GET /projects/{id}/assets``) says so.
        """
        limit = max(1, min(int(limit), 200))
        stmt = (
            select(Assets)
            .where(
                or_(Assets.scope_id == int(scope_id), Assets.is_system_preset.is_(True))
            )
            .where(Assets.deleted_at.is_(None))
        )
        membership = _library_predicate(library)
        if membership is not None:
            stmt = stmt.where(membership)
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
            # ``escape`` on BOTH sides of the or_: passing it to only one leaves
            # that column matching the escape backslashes literally, so the same
            # query would answer differently depending on which column hit.
            like = f"%{_like_escape(q.strip())}%"
            stmt = stmt.where(
                or_(
                    Assets.name.ilike(like, escape="\\"),
                    Assets.description.ilike(like, escape="\\"),
                )
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

    def _count_by_type_stmt(self, scope_id: int):
        """The SELECT behind :meth:`count_by_type`, split out so it can be
        compiled and asserted without a database (mirrors ``_list_stmt``)."""
        return (
            select(Assets.asset_type, func.count())
            .where(Assets.scope_id == int(scope_id))
            # System presets are GLOBAL — ``list`` unions them into every
            # scope, but they are nobody's own assets, so a per-scope tally
            # must not claim them. ``assets_scope_or_preset`` is an OR, so a
            # preset MAY carry a scope_id; the scope predicate alone would
            # therefore not be enough and this is not belt-and-braces.
            .where(Assets.is_system_preset.is_(False))
            # mig 449: the badges count the LIBRARY, and the library is what
            # somebody deliberately added. Counting project-originated rows here
            # would put a number on the sidebar that the shelf below it cannot
            # show — the exact "badge and grid silently disagree" failure the
            # preset exclusion above already exists to prevent.
            .where(Assets.in_library.is_(True))
            .where(Assets.deleted_at.is_(None))
            .group_by(Assets.asset_type)
        )

    async def count_by_type(self, scope_id: int) -> Dict[str, int]:
        """``{asset_type: n}`` for one scope, zero-filled over every type.

        Zero-filled on purpose: a GROUP BY answers only for types that have at
        least one row, and handing the caller a dict missing ``costume``
        instead of ``costume: 0`` makes "none yet" indistinguishable from "this
        type does not exist" at every call site. The fill list comes from
        ``models.assets.ASSET_TYPES`` — the same tuple the DB CHECK and the
        slot tables are pinned against (test_slots.py) — so a seventh type
        cannot be added to the backend while this method keeps answering with
        six keys.

        ⚠️ **Where the unknown-type carry STOPS.** The loop below deliberately
        keeps a row whose ``asset_type`` is outside ``ASSET_TYPES`` rather than
        dropping it silently — but that extra key travels no further than this
        return value. ``AssetCountsResponse`` (``schemas/assets.py``) declares
        six explicit fields, so FastAPI's response validation drops the seventh
        on the way out and no client ever sees it. The carry is therefore a
        DEBUGGING affordance for a direct caller of this repository, not a
        contract with the sidebar: a type that reached the table without
        reaching ``ASSET_TYPES`` shows up in a log or a REPL here, and nowhere
        in the UI. Widening the response model is what would change that.
        """
        out: Dict[str, int] = {t: 0 for t in ASSET_TYPES}
        async with read_scope() as session:
            for asset_type, n in (
                await session.execute(self._count_by_type_stmt(int(scope_id)))
            ).all():
                # An asset_type outside the table would be a row the slot code
                # cannot describe; count it rather than dropping it silently.
                out[str(asset_type)] = int(n)
        return out

    def _resolve_legacy_stmt(self, scope_id: int, legacy_table: str, legacy_id: int):
        """The SELECT behind :meth:`resolve_legacy`.

        Matches on JSONB CONTAINMENT (``attrs @> '{"legacy_ids": [[t, id]]}'``)
        rather than on an unnested comparison: ``legacy_ids`` is a list of
        ``[table, id]`` pairs (a merged asset carries several), and containment
        is the operator that answers "is this pair one of them" without the
        caller having to know how many there are. It also matches BOTH elements
        of the pair together — comparing the id alone would let a
        ``project_characters`` id 7 answer for a ``project_lib_entities`` id 7,
        which is a different entity in a different table.

        Only ``legacy_ids`` is searched. ``attrs.merged_from`` repeats the same
        pairs for a merged asset, but it is a record of the merge, not the
        identity map; a containment probe keyed on the ``legacy_ids`` KEY cannot
        match it, which is the intended behaviour rather than an accident.

        Scope-limited, and system presets are NOT unioned in the way
        :meth:`get` unions them: a preset has no legacy row behind it, so
        widening the read would only add rows that can never match.

        ``ORDER BY id`` is not decoration — nothing in the schema forbids two
        assets carrying the same legacy pair (a hand-edited ``attrs``, or a
        partially-applied migration re-run), and ``LIMIT 1`` without an order is
        a coin flip between them.
        """
        return (
            select(Assets.id)
            .where(Assets.scope_id == int(scope_id))
            .where(Assets.deleted_at.is_(None))
            .where(
                Assets.attrs.contains({"legacy_ids": [[legacy_table, int(legacy_id)]]})
            )
            .order_by(Assets.id.asc())
            .limit(1)
        )

    async def resolve_legacy(
        self, scope_id: int, legacy_table: str, legacy_id: int
    ) -> Optional[int]:
        """The asset a legacy project entity became, or ``None``.

        ``None`` genuinely means "no asset in this scope carries that
        provenance". Two ways a real migrated entity can produce it, both
        expected: the migration ADOPTED an existing hand-made asset (adoption
        never writes ``attrs``, by design — see ``_apply``'s docstring), and an
        entity whose project never migrated has no asset at all.
        """
        async with read_scope() as session:
            row = (
                await session.execute(
                    self._resolve_legacy_stmt(
                        int(scope_id), str(legacy_table), int(legacy_id)
                    )
                )
            ).first()
        return int(row[0]) if row else None

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
