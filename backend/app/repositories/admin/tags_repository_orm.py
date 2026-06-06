"""SQLAlchemy 2.0 ORM implementation of AdminTagsRepository (Phase 2 admin wave).

REST → ORM successor for the admin tags + tag_groups console
(``app/api/admin/tags_router.py``). ``AdminTagsRepositoryOrm`` subclasses
``AdminTagsRepository`` and overrides every data method; the ``*_TABLE`` constants
are inherited. Call sites route through ``get_admin_tags_repository()`` (bottom of
``tags_repository.py``).

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
    boundary, and the legacy ``usage_counts`` returns a dict keyed by ``str(tag_id)``.
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
from app.repositories.admin.tags_repository import AdminTagsRepository

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


class AdminTagsRepositoryOrm(AdminTagsRepository):
    """ORM-backed AdminTagsRepository (admin tags / tag_groups reads + writes)."""

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
        if not changes:
            return await self._get_group(group_id)
        values = {_GROUP_N2A.get(k, k): v for k, v in changes.items()}
        stmt = (
            sa_update(TagGroups)
            .where(TagGroups.id == _bigint(group_id))
            .values(**values)
            .returning(TagGroups)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _GROUP_N2A) if obj is not None else None

    async def _get_group(self, group_id: str) -> Optional[dict[str, Any]]:
        stmt = select(TagGroups).where(TagGroups.id == _bigint(group_id)).limit(1)
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _GROUP_N2A) if obj is not None else None

    async def delete_group(self, group_id: str) -> bool:
        stmt = (
            sa_delete(TagGroups)
            .where(TagGroups.id == _bigint(group_id))
            .returning(TagGroups.id)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            return result.first() is not None

    async def reorder_groups(self, group_ids: list[str]) -> None:
        if not group_ids:
            return
        async with write_scope() as session:
            for idx, gid in enumerate(group_ids):
                await session.execute(
                    sa_update(TagGroups)
                    .where(TagGroups.id == _bigint(gid))
                    .values(sort_order=idx)
                )

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

        if group_id == "uncategorized":
            base = base.where(Tags.group_id.is_(None))
        elif group_id:
            base = base.where(Tags.group_id == _bigint(group_id))

        if search:
            pat = f"%{search}%"
            base = base.where(or_(Tags.name.ilike(pat), Tags.name_zh.ilike(pat)))

        if sort_by and sort_order:
            sort_attr = getattr(Tags, sort_by, Tags.sort_order)
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
        stmt = select(Tags.group_id)
        async with read_scope() as session:
            result = await session.execute(stmt)
            return [{"group_id": _parity(gid)} for (gid,) in result.all()]

    async def usage_counts(self, tag_ids: list[str]) -> dict[str, int]:
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
        values = {_TAG_N2A.get(k, k): v for k, v in payload.items()}
        if values.get("group_id") is not None:
            values["group_id"] = _bigint(values["group_id"])
        stmt = sa_insert(Tags).values(**values).returning(Tags)
        async with write_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _TAG_N2A) if obj is not None else None

    async def update_tag(
        self, tag_id: str, changes: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        if not changes:
            return await self._get_tag(tag_id)
        values = {_TAG_N2A.get(k, k): v for k, v in changes.items()}
        if values.get("group_id") is not None:
            values["group_id"] = _bigint(values["group_id"])
        stmt = (
            sa_update(Tags)
            .where(Tags.id == _bigint(tag_id))
            .values(**values)
            .returning(Tags)
        )
        async with write_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _TAG_N2A) if obj is not None else None

    async def _get_tag(self, tag_id: str) -> Optional[dict[str, Any]]:
        stmt = select(Tags).where(Tags.id == _bigint(tag_id)).limit(1)
        async with read_scope() as session:
            result = await session.execute(stmt)
            obj = result.scalars().first()
        return _obj_dict(obj, _TAG_N2A) if obj is not None else None

    async def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag plus its resource_tags associations (verbatim legacy order:
        associations first, then the tag). Returns bool(tag deleted)."""
        tid = _bigint(tag_id)
        async with write_scope() as session:
            await session.execute(
                sa_delete(ResourceTags).where(ResourceTags.tag_id == tid)
            )
            result = await session.execute(
                sa_delete(Tags).where(Tags.id == tid).returning(Tags.id)
            )
            return result.first() is not None

    # ─── Batch ─────────────────────────────────────────────────────────

    async def batch_set_group(
        self, tag_ids: list[str], group_id: Optional[str]
    ) -> None:
        if not tag_ids:
            return
        gid = _bigint(group_id) if group_id is not None else None
        async with write_scope() as session:
            for tid in tag_ids:
                await session.execute(
                    sa_update(Tags).where(Tags.id == _bigint(tid)).values(group_id=gid)
                )

    async def batch_set_color(self, tag_ids: list[str], color: str) -> None:
        if not tag_ids:
            return
        async with write_scope() as session:
            for tid in tag_ids:
                await session.execute(
                    sa_update(Tags).where(Tags.id == _bigint(tid)).values(color=color)
                )

    async def batch_delete(self, tag_ids: list[str]) -> None:
        if not tag_ids:
            return
        async with write_scope() as session:
            for tid in tag_ids:
                bid = _bigint(tid)
                await session.execute(
                    sa_delete(ResourceTags).where(ResourceTags.tag_id == bid)
                )
                await session.execute(sa_delete(Tags).where(Tags.id == bid))

    async def reorder_tags(self, tag_ids: list[str]) -> None:
        if not tag_ids:
            return
        async with write_scope() as session:
            for idx, tid in enumerate(tag_ids):
                await session.execute(
                    sa_update(Tags)
                    .where(Tags.id == _bigint(tid))
                    .values(sort_order=idx)
                )


__all__ = ["AdminTagsRepositoryOrm"]
