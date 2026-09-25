"""Repository for admin tags + tag_groups management (SQLAlchemy 2.0 ORM).

Post-rollout the repository IS the SQLAlchemy 2.0 implementation — the legacy
supabase-py REST path and its ``USE_ORM_ADMIN_TAGS`` flag were retired once prod
ran 100% ORM. Backs the admin tags + tag_groups console
(``app/api/admin/tags_router.py``); call sites route through
``get_admin_tags_repository()`` (bottom of this module).

MODELS (all verified reflected, all exported from ``app.models``):
  - ``Tags``       (table ``tags``)          — PK ``id`` BIGINT snowflake.
  - ``TagGroups``  (table ``tag_groups``)    — PK ``id`` BIGINT snowflake.
  - ``ResourceTags`` (table ``resource_tags``) — composite PK (resource_id, tag_id),
    both BIGINT. Used only for usage-count + cascade-delete.

★ UUID AUDIT (the admin silent-miss trap) ★
============================================
The admin "tags id is uuid?" question — CHECKED: it is NOT. ``tags.id`` and
``tag_groups.id`` are BIGINT snowflakes (unlike the prior admin tasks/users repos,
the dict-key columns HERE are bigint, not uuid). Per-column evidence:

  - ``tags.id`` (BIGINT) → **native int**. DICT-KEY/COMPARE EVIDENCE: the router
    builds ``tag_ids = [str(t["id"]) for t in rows]`` (list_tags) and
    ``group_counts.get(str(g["id"]))`` (list_groups), and looks usage up with
    ``usage_counts.get(str(t["id"]))``. Every consumer str()s the id at the lookup
    boundary, and ``usage_counts`` returns a dict keyed by ``str(tag_id)``.
    A native int therefore round-trips correctly (``str(123) == "123"``). The
    list_tags endpoint also returns ``item["id"]`` RAW (no Pydantic model — returns
    ``{"items": [...]}``) → a JSON number under both REST and ORM. So id STAYS
    native int (the 5.3 trap — a str would change the JSON shape).
  - ``tag_groups.id`` (BIGINT) → **native int** (same reasoning;
    ``group_counts.get(str(g["id"]))`` + raw return).
  - ``tags.group_id`` (BIGINT) → **native int**. DICT-KEY EVIDENCE: list_groups
    does ``group_counts[str(gid)] += 1`` over ``all_tag_group_ids()`` rows — str()'d
    at use. Native int round-trips.
  - ``tags.user_id`` (UUID) → **str**. This is the only uuid column. It is NOT a
    dict key, but ``select("*")`` returns it and list_tags returns the row dict RAW
    to FastAPI's JSON encoder. REST returned user_id as a JSON STRING; a native
    ``uuid.UUID`` would either fail to encode or change the shape. We str() it (the
    generic ``_parity`` sweep) for byte-exact REST parity. (For admin system tags it
    is typically NULL — create_tag sets user_id=None — but the column exists on the
    table and SELECT * returns it, so the sweep is load-bearing for any pre-existing
    user-scoped row that surfaces.)
  - ``resource_tags.tag_id`` / ``resource_id`` (BIGINT) → native int; only used by
    ``usage_counts`` which str()s them into its result dict. Native int round-trips.

STRATEGY-C VALUE-TYPE PARITY (per-field)
----------------------------------------
  created_at (timestamptz) → **ISO str** (list_tags returns it RAW to JSON; REST
    emitted an ISO string). enabled (bool) / sort_order (int) → native. name /
    type / color / icon / name_zh (text) → native str. type is a plain ``String(20)``
    with a DB CheckConstraint (NOT a SQLAlchemy Enum) → no ``_plain`` unwrap needed.

EMBEDDED JOIN (PostgREST ``select("*, tag_groups(name)")``) — reproduced
------------------------------------------------------------------------
list_tags' legacy query embeds the parent group's name as a nested resource:
each row gets ``{"tag_groups": {"name": <group name>} | None}``, which the router
reads via ``t.get("tag_groups", {}).get("name")`` then ``item.pop("tag_groups")``.
We reproduce it with a LEFT OUTER JOIN to ``tag_groups`` and attach the SAME nested
shape: ``row["tag_groups"] = {"name": gname}`` when a group exists, else ``None``.

WRITES (the silent-rollback P0 lesson) — ALL commit via write_scope()
---------------------------------------------------------------------
  create_group / update_group / create_tag / update_tag → INSERT/UPDATE …
    RETURNING the full row → SELECT *-shaped dict (REST returned ``result.data[0]``).
  delete_group / delete_tag / batch_* / reorder_* → DELETE/UPDATE … RETURNING id;
    bool returns mirror REST's ``bool(result.data)``. delete_tag / batch_delete
    cascade-delete resource_tags FIRST (verbatim legacy order). Per-row reorder /
    batch loops are reproduced row-by-row inside ONE write_scope() (atomic).

PHANTOM-COLUMN SCREEN: create_group/update_group/create_tag/update_tag bind keys
the caller supplies. All caller keys (name, sort_order / name, type, color, icon,
user_id, name_zh, group_id) are REAL mapped columns — no phantom. update payloads
pass THROUGH verbatim (the legacy ``.update(changes)`` contract); a renamed column
would bind via ``_TAG_N2A`` (none of tags' columns are renamed, but the map is
applied defensively).

No date/timestamp range filter exists in this repo → no timestamptz<VARCHAR hazard.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy import insert as sa_insert
from sqlalchemy import or_, select
from sqlalchemy import update as sa_update

from app.db.session import read_scope, write_scope
from app.models import ResourceTags, TagGroups, Tags
from app.repositories._orm_helpers import _name_to_attr

_TAG_N2A: Dict[str, str] = _name_to_attr(Tags)
_GROUP_N2A: Dict[str, str] = _name_to_attr(TagGroups)


def _bigint(value: Any) -> int:
    """Coerce a snowflake id (tags.id / tag_groups.id / group_id) to a native int
    for a BIGINT bind. asyncpg's int8 codec is STRICT — the router passes ids as
    STR (path/query params), but the legacy PostgREST path silently coerced them;
    we must int-coerce at every bigint filter bind to preserve that behaviour."""
    if isinstance(value, int):
        return value
    return int(str(value))


def _maybe_bigint(value: Any) -> Optional[int]:
    """``_bigint`` for ids the CALLER supplied (path params, request bodies):
    a value that is not an integer names no row, so it is ``None`` rather
    than a ``ValueError`` (which surfaced as a 500)."""
    try:
        return _bigint(value)
    except (TypeError, ValueError):
        return None


def _bigints(values: list[Any]) -> Optional[list[int]]:
    """All of ``values`` as ints, or ``None`` if any of them is not one."""
    ids = [_maybe_bigint(v) for v in values]
    return None if any(i is None for i in ids) else ids  # type: ignore[return-value]


class AdminTagGroupNotFound(LookupError):
    """A tag write named a ``group_id`` that is not a tag group."""


async def _all_exist(session: Any, id_col: Any, ids: list[int]) -> bool:
    """Every id in ``ids`` names a row (checked inside the write's own
    transaction, before it writes anything)."""
    found = set(
        (await session.execute(select(id_col).where(id_col.in_(ids)))).scalars().all()
    )
    return found == set(ids)


async def _require_group(session: Any, group_id: Any) -> int:
    gid = _maybe_bigint(group_id)
    if gid is None or not await _all_exist(session, TagGroups.id, [gid]):
        raise AdminTagGroupNotFound(str(group_id))
    return gid


def _parity(value: Any) -> Any:
    """Strategy-C read-boundary coercion: uuid → str, datetime → ISO str. NULL /
    other types pass through unchanged. (No Enum columns on these tables.)"""
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _obj_dict(obj: Any, name_to_attr: Dict[str, str]) -> Dict[str, Any]:
    """SELECT *-shaped dict keyed by DB column NAME, with strategy-C coercions."""
    return {name: _parity(getattr(obj, attr)) for name, attr in name_to_attr.items()}


class AdminTagsRepository:
    """ORM-backed admin tags / tag_groups reads + writes."""

    TAGS_TABLE = "tags"
    GROUPS_TABLE = "tag_groups"
    RESOURCE_TAGS_TABLE = "resource_tags"

    # ─── Tag groups ────────────────────────────────────────────────────

    async def list_groups(self) -> list[dict[str, Any]]:
        stmt = select(TagGroups).order_by(
            TagGroups.sort_order.asc(), TagGroups.created_at.asc()
        )
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [_obj_dict(o, _GROUP_N2A) for o in result.scalars().all()]

    async def max_group_sort_order(self) -> int:
        stmt = (
            select(TagGroups.sort_order).order_by(TagGroups.sort_order.desc()).limit(1)
        )
        async with read_scope() as session:
            value = await session.scalar(stmt)
        return value or 0

    async def create_group(
        self, name: str, sort_order: int
    ) -> Optional[dict[str, Any]]:
        stmt = (
            sa_insert(TagGroups)
            .values(name=name, sort_order=sort_order)
            .returning(TagGroups)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _GROUP_N2A) if obj is not None else None

    async def update_group(
        self, group_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        gid = _maybe_bigint(group_id)
        if gid is None:
            return None
        if not changes:
            return await self._get_group(group_id)
        values = {_GROUP_N2A.get(k, k): v for k, v in changes.items()}
        stmt = (
            sa_update(TagGroups)
            .where(TagGroups.id == gid)
            .values(**values)
            .returning(TagGroups)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _GROUP_N2A) if obj is not None else None

    async def _get_group(self, group_id: str) -> Optional[dict[str, Any]]:
        gid = _maybe_bigint(group_id)
        if gid is None:
            return None
        stmt = select(TagGroups).where(TagGroups.id == gid).limit(1)
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _GROUP_N2A) if obj is not None else None

    async def delete_group(self, group_id: str) -> bool:
        gid = _maybe_bigint(group_id)
        if gid is None:
            return False
        stmt = sa_delete(TagGroups).where(TagGroups.id == gid).returning(TagGroups.id)
        async with write_scope() as session:
            result = await session.execute(stmt)
            return result.first() is not None

    async def reorder_groups(self, group_ids: list[str]) -> bool:
        """``False`` (and nothing written) if any id names no group."""
        if not group_ids:
            return True
        ids = _bigints(group_ids)
        if ids is None:
            return False
        async with write_scope() as session:
            if not await _all_exist(session, TagGroups.id, ids):
                return False
            for idx, gid in enumerate(ids):
                await session.execute(
                    sa_update(TagGroups)
                    .where(TagGroups.id == gid)
                    .values(sort_order=idx)
                )
        return True

    # ─── Tags ──────────────────────────────────────────────────────────

    async def list_tags(
        self,
        *,
        page: int,
        page_size: int,
        search: Optional[str] = None,
        group_id: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        # LEFT OUTER JOIN to tag_groups to reproduce the PostgREST embed
        # ``tag_groups(name)``; select the tag entity + the group name column.
        base = select(Tags, TagGroups.name.label("_group_name")).outerjoin(
            TagGroups, Tags.group_id == TagGroups.id
        )

        if group_id and group_id != "uncategorized" and _maybe_bigint(group_id) is None:
            # Not an id, so no tag is in that group (was a ValueError → 500).
            return [], 0

        if group_id == "uncategorized":
            base = base.where(Tags.group_id.is_(None))
        elif group_id:
            base = base.where(Tags.group_id == _bigint(group_id))

        if search:
            pat = f"%{search}%"
            base = base.where(or_(Tags.name.ilike(pat), Tags.name_zh.ilike(pat)))

        if sort_by and sort_order:
            # Only mapped columns: ``getattr(Tags, "metadata")`` is the
            # MetaData object and ``.desc()`` on it was a 500.
            sort_attr = getattr(Tags, _TAG_N2A.get(sort_by, "sort_order"))
            order_cols = [sort_attr.desc() if sort_order == "desc" else sort_attr.asc()]
        else:
            order_cols = [Tags.sort_order.asc(), Tags.created_at.desc()]

        offset = (page - 1) * page_size
        # COUNT over the SAME filtered tag set (the embed does not change the count;
        # the outer join is to a unique parent so it never multiplies rows).
        count_base = select(Tags.id)
        if group_id == "uncategorized":
            count_base = count_base.where(Tags.group_id.is_(None))
        elif group_id:
            count_base = count_base.where(Tags.group_id == _bigint(group_id))
        if search:
            pat = f"%{search}%"
            count_base = count_base.where(
                or_(Tags.name.ilike(pat), Tags.name_zh.ilike(pat))
            )

        async with read_scope() as session:
            total = await session.scalar(
                select(func.count()).select_from(count_base.subquery())
            )
            result = await session.execute(
                base.order_by(*order_cols).offset(offset).limit(page_size)
            )
            rows: list[dict[str, Any]] = []
            for tag_obj, group_name in result.all():
                row = _obj_dict(tag_obj, _TAG_N2A)
                # Reproduce the embedded resource shape: nested {"name": ...} | None.
                row["tag_groups"] = (
                    {"name": group_name} if group_name is not None else None
                )
                rows.append(row)
        return rows, (total or 0)

    async def all_tag_group_ids(self) -> list[dict[str, Any]]:
        """Used by list_groups to compute tag counts per group."""
        stmt = select(Tags.group_id)
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [{"group_id": _parity(gid)} for (gid,) in result.all()]

    async def usage_counts(self, tag_ids: list[str]) -> dict[str, int]:
        """resource_tags rows grouped by tag_id."""
        if not tag_ids:
            return {}
        ids = [_bigint(t) for t in tag_ids]
        stmt = select(ResourceTags.tag_id).where(ResourceTags.tag_id.in_(ids))
        async with read_scope() as session:
            result = await session.execute(stmt)
            counts: dict[str, int] = {}
            for (tid,) in result.all():
                key = str(tid)
                counts[key] = counts.get(key, 0) + 1
        return counts

    async def create_tag(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Raises :class:`AdminTagGroupNotFound` for an unknown ``group_id``."""
        values = {_TAG_N2A.get(k, k): v for k, v in payload.items()}
        stmt_values = dict(values)
        async with write_scope() as session:
            if values.get("group_id") is not None:
                stmt_values["group_id"] = await _require_group(
                    session, values["group_id"]
                )
            stmt = sa_insert(Tags).values(**stmt_values).returning(Tags)
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _TAG_N2A) if obj is not None else None

    async def update_tag(
        self, tag_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """``None`` if ``tag_id`` names no tag; raises
        :class:`AdminTagGroupNotFound` for an unknown ``group_id``."""
        tid = _maybe_bigint(tag_id)
        if tid is None:
            return None
        if not changes:
            return await self._get_tag(tag_id)
        values = {_TAG_N2A.get(k, k): v for k, v in changes.items()}
        async with write_scope() as session:
            if values.get("group_id") is not None:
                values["group_id"] = await _require_group(session, values["group_id"])
            stmt = (
                sa_update(Tags).where(Tags.id == tid).values(**values).returning(Tags)
            )
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _TAG_N2A) if obj is not None else None

    async def _get_tag(self, tag_id: str) -> Optional[dict[str, Any]]:
        tid = _maybe_bigint(tag_id)
        if tid is None:
            return None
        stmt = select(Tags).where(Tags.id == tid).limit(1)
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _TAG_N2A) if obj is not None else None

    async def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag plus its resource_tags associations (verbatim legacy order:
        associations first, then the tag). Returns bool(tag deleted)."""
        tid = _maybe_bigint(tag_id)
        if tid is None:
            return False
        async with write_scope() as session:
            await session.execute(
                sa_delete(ResourceTags).where(ResourceTags.tag_id == tid)
            )
            result = await session.execute(
                sa_delete(Tags).where(Tags.id == tid).returning(Tags.id)
            )
            return result.first() is not None

    # ─── Batch ─────────────────────────────────────────────────────────
    #
    # Each returns ``False`` — having written nothing — when any tag id (or the
    # target group of a move) names no row. The existence check runs inside the
    # write's own transaction, so a batch is all-or-nothing: it used to update
    # the ids that existed, skip the rest silently and answer "Moved N tags"
    # with N counting the misses; a non-numeric id was a 500 mid-loop.

    async def batch_set_group(
        self, tag_ids: list[str], group_id: Optional[str]
    ) -> bool:
        """Raises :class:`AdminTagGroupNotFound` for an unknown ``group_id``."""
        if not tag_ids:
            return True
        ids = _bigints(tag_ids)
        if ids is None:
            return False
        async with write_scope() as session:
            gid = (
                await _require_group(session, group_id)
                if group_id is not None
                else None
            )
            if not await _all_exist(session, Tags.id, ids):
                return False
            for tid in ids:
                await session.execute(
                    sa_update(Tags).where(Tags.id == tid).values(group_id=gid)
                )
        return True

    async def batch_set_color(self, tag_ids: list[str], color: str) -> bool:
        if not tag_ids:
            return True
        ids = _bigints(tag_ids)
        if ids is None:
            return False
        async with write_scope() as session:
            if not await _all_exist(session, Tags.id, ids):
                return False
            for tid in ids:
                await session.execute(
                    sa_update(Tags).where(Tags.id == tid).values(color=color)
                )
        return True

    async def batch_delete(self, tag_ids: list[str]) -> bool:
        if not tag_ids:
            return True
        ids = _bigints(tag_ids)
        if ids is None:
            return False
        async with write_scope() as session:
            if not await _all_exist(session, Tags.id, ids):
                return False
            for bid in ids:
                await session.execute(
                    sa_delete(ResourceTags).where(ResourceTags.tag_id == bid)
                )
                await session.execute(sa_delete(Tags).where(Tags.id == bid))
        return True

    async def reorder_tags(self, tag_ids: list[str]) -> bool:
        if not tag_ids:
            return True
        ids = _bigints(tag_ids)
        if ids is None:
            return False
        async with write_scope() as session:
            if not await _all_exist(session, Tags.id, ids):
                return False
            for idx, tid in enumerate(ids):
                await session.execute(
                    sa_update(Tags).where(Tags.id == tid).values(sort_order=idx)
                )
        return True


def get_admin_tags_repository() -> "AdminTagsRepository":
    """Return the AdminTagsRepository (SQLAlchemy 2.0 ORM, the only implementation
    post-rollout — the legacy supabase-py REST path and its ``USE_ORM_ADMIN_TAGS``
    flag were retired once prod ran 100% ORM)."""
    return AdminTagsRepository()
