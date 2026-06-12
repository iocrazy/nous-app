# app/repositories/tags_repository_orm.py

"""SQLAlchemy 2.0 ORM implementation of TagsRepository (Phase 2, M batch).

REST → ORM successor for the tag system: the ``tags`` table CRUD + the
``resource_tags`` junction (M:N resource↔tag) + the cross-table reads
(``tag_groups`` embed, ``resources`` media-id resolution, RPC-backed counts).
Strangler-Fig single-inheritance: ``TagsRepositoryOrm`` subclasses
``TagsRepository`` and overrides every DB method. Call sites route through
``get_tags_repository()`` (bottom of ``tags_repository.py``).

TABLES TOUCHED
==============
  tags           (Tags)         — id BIGINT, user_id UUID, scope_id/group_id BIGINT
  resource_tags  (ResourceTags) — junction, composite PK (resource_id, tag_id) BIGINT
  tag_groups     (TagGroups)    — embedded for group_name (id BIGINT, name)
  resources      (Resources)    — media_id → id resolution (BIGINT)

★ ID-TYPE FINDING (CONTRARY TO THE TASK HINT) ★
================================================
The task brief warned ``tags.id`` "may be UUID". It is NOT — per the model and
``supabase/migrations``, ``tags.id`` is **BIGINT** (Snowflake, server-default
``generate_snowflake_id()``), and ``resource_tags.tag_id`` / ``resource_id`` are
BIGINT too. So the 5.3 trap (bigint → NEVER str()) applies to every tag id, and
there is NO uuid tag-id consumer hazard. The ONLY uuid column in this whole
surface is ``tags.user_id`` (+ ``resource_tags.tagged_by``, never selected by the
legacy).

STRATEGY C — per-field value-type parity (per-column consumer audit)
====================================================================
REST rendered: bigint → JSON number (int), uuid → str, timestamptz → ISO str,
double → number (float), text → str. The ORM returns native types; ``_parity``
restores the REST shape:

  tags.id / scope_id / group_id (BIGINT) → **native int** (5.3 trap). CONSUMER
    AUDIT — every router/service consumer wraps the id in ``str(...)`` at the
    boundary BEFORE use (e.g. media_fetch_helpers ``tag_ids.append(str(tag["id"]))``,
    tags_router maps through the ``SnowflakeId`` response type whose
    BeforeValidator does ``str(v)``, classification_service passes
    ``tag_id=primary_tag["id"]`` straight into ``add_tag_to_resource`` which binds
    it to a BIGINT column). ``str(int)`` and ``str(str)`` are identical, and a
    native int binds correctly to the bigint columns — so native int is correct
    AND matches REST's JSON-number shape. There is NO ``==``/dict-key tag-id
    identity compare that would silently fail (the dedup in get_all_tags str()s
    the id into the count_map key internally). NEVER str() these.
  tags.user_id (UUID) → **str** for shape parity. CONSUMER AUDIT — the legacy
    SELECT * returned user_id as a str; TagResponse.user_id is typed
    ``Optional[str]`` (pydantic v2 rejects a native uuid.UUID for a str field).
    str() REQUIRED.
  tags.created_at (timestamptz) → **ISO str** ALWAYS (TagResponse.created_at is
    datetime; pydantic parses the ISO str — parity with REST).
  tags.enabled (bool) / color / icon / name / name_zh / type (text) → native.
  resource_tags.confidence (double) → native float (matches REST number).
  resource_tags.tagged_by (uuid) → str (shape parity; never consumed
    type-sensitively — only surfaced in get_resource_tags' SELECT *).

PHANTOM-COLUMN PRE-FLIGHT (per write path)
==========================================
  create_tag       : writes name / type / user_id / color / icon / (name_zh) —
                     all mapped Tags columns. No phantom.
  update_tag /
  update_tag_admin : patch keys come from **kwargs (router passes name / color /
                     icon / name_zh / enabled / group_id / sort_order — all mapped
                     Tags columns). We filter to mapped attrs (``_TAG_ATTRS``)
                     defensively; a stray key becomes a silent no-op, matching the
                     legacy whose PostgREST update would 400 (router catches). No
                     HARD STOP.
  add_tag_to_resource /
  bulk_add_tags_to_resource : write resource_id / tag_id / source (+ confidence) —
                     all mapped ResourceTags columns. No phantom.

JUNCTION / BULK WRITES (the resource_tags upsert)
=================================================
``resource_tags`` PK is composite ``(resource_id, tag_id)`` (both explicit
bigints — NO server-default id), so there is NO mixed-PK bulk_upsert CompileError
hazard (that trap only fires when some rows carry an explicit id and others rely
on a server-default). The legacy ``.upsert(...)`` (PostgREST) defaults to ON
CONFLICT on the PK DO UPDATE (merge). We reproduce it EXACTLY with
``pg_insert(ResourceTags).values(rows).on_conflict_do_update(index_elements=
[resource_id, tag_id], set_=<non-pk cols>)`` in ONE ``write_scope()`` — a single
multi-VALUES statement, atomic, returning the affected rows. add_tag_to_resource
upserts one row + returns it; bulk upserts N rows + returns them all.

MODEL-QUIRK SCAN
================
  No renamed columns (every Tags / ResourceTags column has name == key, so
  ``_orm_obj_to_dict`` is a straight getattr). No SQLAlchemy Enum columns (``type``
  is a CHECK-constrained ``String(20)``, NOT Enum → no ``_plain`` unwrap).
  resource_tags has a composite PK (handled by on_conflict above). tags.id /
  tag_groups.id carry a ``generate_snowflake_id()`` server default — irrelevant to
  reads; create_tag does NOT supply an id (lets the default fire), matching legacy.

RPC PARITY
==========
``get_all_tags`` calls ``get_tag_counts_by_ids`` under REST; we reproduce its
DB-side GROUP BY directly via an engine-native ``func.count()`` query (no RPC
needed — same aggregate, byte-identical result), with the legacy client-side
fallback shape kept.

``get_tag_counts`` was a ★ DOUBLE-BROKEN endpoint — BOTH halves are now FIXED ★:
  1. Its primary RPC ``get_user_tag_counts`` was broken in the schema — the mig
     066 function body JOINed ``media_tags`` (dropped in migration 077, merged
     into ``resource_tags``) + ``parsed_media.user_id`` (dropped in migration
     083), so every call raised ``relation "media_tags" does not exist``. FIXED
     by **migration 256** (``256_fix_get_user_tag_counts.sql``): the RPC is
     redefined resource-centric — JOIN ``resource_tags`` → ``resources`` and
     filter ``resources.creator_id = p_user_id``, same signature, id return type
     widened UUID → BIGINT to match the snowflake ``tags.id`` (post mig 051). NO
     code change here — repos call the RPC by name and pick up the new body once
     256 is applied.
  2. Its fallback ``_get_tag_counts_fallback`` formerly selected the non-existent
     ``resources.user_id`` column → PG 42703. FIXED in the prior commit to filter
     on ``resources.creator_id`` (see that method).
So the primary RPC path now returns the user's real tag counts; the fallback is
only hit if the RPC is genuinely absent/empty, and it too returns correct counts.
The router's ``get_tag_statistics`` try/except still guards both paths.

No date columns and NO date/timestamptz RANGE filters in this repo (every WHERE is
equality / ILIKE / IN), so there is no timestamptz<VARCHAR binding hazard.

Reads return [] / None on the documented paths (mirroring the legacy try/except).
Writes commit via ``write_scope()``; reads use ``read_scope()``.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, or_, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.session import read_scope, write_scope
from app.models import Resources, ResourceTags, TagGroups, Tags
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict
from app.repositories.tags_repository import TagsRepository

_TAG_N2A: Dict[str, str] = _name_to_attr(Tags)
_TAG_ATTRS = {p.key for p in Tags.__mapper__.column_attrs}
_RT_N2A: Dict[str, str] = _name_to_attr(ResourceTags)


def _parity(out: Dict[str, Any]) -> Dict[str, Any]:
    """Strategy-C value-type parity IN PLACE on a SELECT *-shaped dict:
    uuid → str (REST shape — REQUIRED for tags.user_id's str response field;
    shape-only for resource_tags.tagged_by), datetime → ISO str. Bigint ids / FKs
    (tags.id/scope_id/group_id, resource_tags.resource_id/tag_id) stay NATIVE int
    (the 5.3 trap — every consumer str()s them at the boundary). Double (confidence)
    and text stay native. NULLs pass through."""
    for key, value in out.items():
        if isinstance(value, _uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, _dt.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, _dt.date):
            out[key] = value.isoformat()
    return out


def _tag_row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full Tags row."""
    return _parity(_orm_obj_to_dict(obj, _TAG_N2A))


def _rt_row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full ResourceTags row."""
    return _parity(_orm_obj_to_dict(obj, _RT_N2A))


class TagsRepositoryOrm(TagsRepository):
    """ORM-backed TagsRepository. Overrides every DB method on tags +
    resource_tags."""

    async def get_all_tags(
        self, user_id: Optional[str] = None, enabled_only: bool = False
    ) -> List[dict]:
        """Get all tags (system + time + user's own) with media_count + group_name.

        Reproduces the legacy: single tags query (or_ filter) + a single GROUP BY
        count over resource_tags, then flatten group_name + attach media_count.
        """
        async with read_scope() as session:
            stmt = select(Tags)
            if user_id:
                stmt = stmt.where(
                    or_(
                        Tags.type == "system",
                        Tags.type == "time",
                        (Tags.type == "user") & (Tags.user_id == user_id),
                    )
                )
            else:
                stmt = stmt.where(or_(Tags.type == "system", Tags.type == "time"))
            if enabled_only:
                stmt = stmt.where(Tags.enabled.is_(True))

            tag_objs = (await session.execute(stmt)).scalars().all()
            tags = [_tag_row(t) for t in tag_objs]

            # group_name flatten — resolve in one IN() query (the legacy used a
            # PostgREST embedded ``tag_groups(name)``; same single round-trip).
            group_ids = {t["group_id"] for t in tags if t.get("group_id") is not None}
            group_name_map: Dict[int, Optional[str]] = {}
            if group_ids:
                grp_rows = (
                    await session.execute(
                        select(TagGroups.id, TagGroups.name).where(
                            TagGroups.id.in_(group_ids)
                        )
                    )
                ).all()
                group_name_map = {gid: name for gid, name in grp_rows}

            # media_count — one GROUP BY over resource_tags (DB-side, matches the
            # legacy RPC ``get_tag_counts_by_ids`` aggregation exactly).
            tag_ids = [t["id"] for t in tags]
            count_map: Dict[int, int] = {}
            if tag_ids:
                count_rows = (
                    await session.execute(
                        select(ResourceTags.tag_id, func.count())
                        .where(ResourceTags.tag_id.in_(tag_ids))
                        .group_by(ResourceTags.tag_id)
                    )
                ).all()
                count_map = {tid: cnt for tid, cnt in count_rows}

        for tag in tags:
            tag["group_name"] = group_name_map.get(tag.get("group_id"))
            if "enabled" not in tag:
                tag["enabled"] = True
            tag["media_count"] = count_map.get(tag["id"], 0)
        return tags

    async def get_tag_by_id(self, tag_id: str) -> Optional[dict]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(Tags).where(Tags.id == int(tag_id)).limit(1)
                    )
                )
                .scalars()
                .first()
            )
            return _tag_row(row) if row else None

    async def get_tag_by_name(
        self, name: str, user_id: Optional[str] = None
    ) -> Optional[dict]:
        """Get a tag by name (case-insensitive English) or name_zh (exact).

        Mirrors the legacy precedence: system → time → (user). For system/time we
        match English ILIKE OR exact ZH; for user we match English ILIKE scoped to
        the user. ``ilike("name", value)`` with no ``%`` wildcards is an exact
        case-insensitive match (the SQLAlchemy escaping of literal %/_ mirrors the
        legacy's manual PostgREST escaping intent — no user input is treated as a
        pattern)."""
        try:
            async with read_scope() as session:
                for tag_type in ("system", "time"):
                    row = (
                        (
                            await session.execute(
                                select(Tags)
                                .where(
                                    or_(
                                        Tags.name.ilike(name, escape="\\"),
                                        Tags.name_zh == name,
                                    )
                                )
                                .where(Tags.type == tag_type)
                                .limit(1)
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if row:
                        return _tag_row(row)

                if user_id:
                    row = (
                        (
                            await session.execute(
                                select(Tags)
                                .where(Tags.name.ilike(name, escape="\\"))
                                .where(Tags.user_id == user_id)
                                .limit(1)
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if row:
                        return _tag_row(row)
        except Exception as e:
            logger.error(f"Error in get_tag_by_name: {e}")
        return None

    async def get_or_create_group(self, name: str) -> Optional[dict]:
        """Find a tag_groups row by exact name, creating it when missing.

        Used by the AI classification write-through (one group per
        dimension). Race-safe: ON CONFLICT(name) DO NOTHING + re-select.
        """
        try:
            async with write_scope() as session:
                row = (
                    (
                        await session.execute(
                            select(TagGroups).where(TagGroups.name == name).limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                if row is None:
                    row = (
                        (
                            await session.execute(
                                pg_insert(TagGroups)
                                .values(name=name)
                                .on_conflict_do_nothing(index_elements=["name"])
                                .returning(TagGroups)
                            )
                        )
                        .scalars()
                        .first()
                    )
                if row is None:  # lost the insert race — re-select
                    row = (
                        (
                            await session.execute(
                                select(TagGroups).where(TagGroups.name == name).limit(1)
                            )
                        )
                        .scalars()
                        .first()
                    )
                if row is None:
                    return None
                return {
                    "id": row.id,
                    "name": row.name,
                    "sort_order": row.sort_order,
                }
        except Exception as e:
            logger.error(f"Error in get_or_create_group({name}): {e}")
            return None

    async def create_tag(
        self,
        name: str,
        user_id: str,
        color: str = "#6366f1",
        icon: Optional[str] = None,
        name_zh: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> dict:
        """Create a new user tag (id fires the server-default snowflake)."""
        values: Dict[str, Any] = {
            "name": name,
            "type": "user",
            "user_id": user_id,
            "color": color,
            "icon": icon,
        }
        if name_zh:
            values["name_zh"] = name_zh
        if group_id:
            values["group_id"] = int(group_id)

        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        pg_insert(Tags).values(**values).returning(Tags)
                    )
                )
                .scalars()
                .first()
            )
            out = _tag_row(row)
        logger.info(f"Created tag: {name} (zh: {name_zh}) for user: {user_id}")
        return out

    async def update_tag(self, tag_id: str, user_id: str, **kwargs) -> Optional[dict]:
        """Update a user tag (scoped to user_id). Mirrors the legacy: drop None
        values, no-op (return current) when nothing to write."""
        update_data = {
            k: v for k, v in kwargs.items() if v is not None and k in _TAG_ATTRS
        }
        if not update_data:
            return await self.get_tag_by_id(tag_id)

        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        sa_update(Tags)
                        .where(Tags.id == int(tag_id))
                        .where(Tags.user_id == user_id)
                        .values(**update_data)
                        .returning(Tags)
                    )
                )
                .scalars()
                .first()
            )
            return _tag_row(row) if row else None

    async def update_tag_admin(self, tag_id: str, **kwargs) -> Optional[dict]:
        """Update any tag (no user_id check). Used for toggling enabled on
        system tags."""
        update_data = {
            k: v for k, v in kwargs.items() if v is not None and k in _TAG_ATTRS
        }
        if not update_data:
            return await self.get_tag_by_id(tag_id)

        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        sa_update(Tags)
                        .where(Tags.id == int(tag_id))
                        .values(**update_data)
                        .returning(Tags)
                    )
                )
                .scalars()
                .first()
            )
            return _tag_row(row) if row else None

    async def delete_tag(self, tag_id: str, user_id: str) -> bool:
        """Delete a user tag (scoped to user_id + type='user'). Returns True if
        a row was deleted."""
        async with write_scope() as session:
            result = await session.execute(
                Tags.__table__.delete()
                .where(Tags.id == int(tag_id))
                .where(Tags.user_id == user_id)
                .where(Tags.type == "user")
            )
            return (result.rowcount or 0) > 0

    async def add_tag_to_resource(
        self,
        resource_id: str,
        tag_id: str,
        confidence: Optional[float] = None,
        source: str = "manual",
    ) -> dict:
        """Add a tag to a resource (upsert on the composite PK). Reproduces the
        PostgREST ON CONFLICT (resource_id, tag_id) DO UPDATE."""
        values: Dict[str, Any] = {
            "resource_id": int(resource_id),
            "tag_id": int(tag_id),
            "source": source,
        }
        if confidence is not None:
            values["confidence"] = confidence

        set_cols = {
            k: v for k, v in values.items() if k not in ("resource_id", "tag_id")
        }
        async with write_scope() as session:
            stmt = (
                pg_insert(ResourceTags)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[ResourceTags.resource_id, ResourceTags.tag_id],
                    set_=set_cols,
                )
                .returning(ResourceTags)
            )
            row = (await session.execute(stmt)).scalars().first()
            out = _rt_row(row)
        logger.info(f"Added tag {tag_id} to resource {resource_id}")
        return out

    async def remove_tag_from_resource(self, resource_id: str, tag_id: str) -> bool:
        async with write_scope() as session:
            result = await session.execute(
                ResourceTags.__table__.delete()
                .where(ResourceTags.resource_id == int(resource_id))
                .where(ResourceTags.tag_id == int(tag_id))
            )
            return (result.rowcount or 0) > 0

    async def resolve_media_id_to_resource_id(self, media_id: str) -> Optional[str]:
        """Resolve a parsed_media id to its resource id. Returns the id as a STR
        (legacy contract: ``str(result.data[0]["id"])``)."""
        async with read_scope() as session:
            rid = await session.scalar(
                select(Resources.id).where(Resources.media_id == int(media_id)).limit(1)
            )
            return str(rid) if rid is not None else None

    async def get_resource_tags(self, resource_id: str) -> List[dict]:
        """Get all resource_tags rows for a resource, each with its embedded
        ``tags`` row (reproduces the PostgREST ``*, tags(*)`` embed)."""
        async with read_scope() as session:
            rt_objs = (
                (
                    await session.execute(
                        select(ResourceTags).where(
                            ResourceTags.resource_id == int(resource_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            rt_rows = [_rt_row(rt) for rt in rt_objs]

            tag_ids = [rt["tag_id"] for rt in rt_rows]
            tag_map: Dict[int, Dict[str, Any]] = {}
            if tag_ids:
                tag_objs = (
                    (await session.execute(select(Tags).where(Tags.id.in_(tag_ids))))
                    .scalars()
                    .all()
                )
                tag_map = {t.id: _tag_row(t) for t in tag_objs}

        for rt in rt_rows:
            rt["tags"] = tag_map.get(rt["tag_id"])
        return rt_rows

    async def get_resources_by_tag(
        self, tag_id: str, user_id: str, limit: int = 50, offset: int = 0
    ) -> List[dict]:
        """Get all resources with a specific tag (reproduces the PostgREST
        ``resource_id, resources(*)`` embed; returns the embedded resource rows)."""
        from app.repositories._orm_helpers import _name_to_attr as _n2a

        _res_n2a = _n2a(Resources)
        async with read_scope() as session:
            rt_rows = (
                (
                    await session.execute(
                        select(ResourceTags.resource_id)
                        .where(ResourceTags.tag_id == int(tag_id))
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not rt_rows:
                return []
            res_objs = (
                (
                    await session.execute(
                        select(Resources).where(Resources.id.in_(list(rt_rows)))
                    )
                )
                .scalars()
                .all()
            )
            # Preserve the resource_tags ordering (PostgREST returned embeds in the
            # junction-row order).
            by_id = {r.id: r for r in res_objs}
            ordered = [by_id[rid] for rid in rt_rows if rid in by_id]
            return [_parity(_orm_obj_to_dict(r, _res_n2a)) for r in ordered]

    async def bulk_add_tags_to_resource(
        self, resource_id: str, tag_ids: List[str], source: str = "manual"
    ) -> List[dict]:
        """Add multiple tags to a resource (one multi-VALUES upsert in a single
        write_scope). Composite-PK rows (no server-default id) → no mixed-PK
        bulk_upsert CompileError. Reproduces the PostgREST upsert (ON CONFLICT DO
        UPDATE) exactly."""
        if not tag_ids:
            return []
        rows = [
            {
                "resource_id": int(resource_id),
                "tag_id": int(tag_id),
                "source": source,
            }
            for tag_id in tag_ids
        ]
        async with write_scope() as session:
            stmt = (
                pg_insert(ResourceTags)
                .values(rows)
                .on_conflict_do_update(
                    index_elements=[ResourceTags.resource_id, ResourceTags.tag_id],
                    set_={"source": text("excluded.source")},
                )
                .returning(ResourceTags)
            )
            result_rows = (await session.execute(stmt)).scalars().all()
            out = [_rt_row(rt) for rt in result_rows]
        logger.info(f"Added {len(tag_ids)} tags to resource {resource_id}")
        return out

    async def get_tag_counts(self, user_id: str, limit: int = 10) -> List[dict]:
        """Get tag usage counts for a user's resources, most-used first.

        Reproduces the legacy: try the ``get_user_tag_counts`` RPC; on absence /
        empty, fall back to the engine-native manual count.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT * FROM get_user_tag_counts("
                        "p_user_id => CAST(:uid AS uuid), p_limit => :lim)"
                    ),
                    {"uid": str(user_id), "lim": limit},
                )
                rows = [_parity(dict(r)) for r in result.mappings().all()]
            if rows:
                return rows
        except Exception as e:
            logger.warning(f"RPC get_user_tag_counts unavailable, using fallback: {e}")
        return await self._get_tag_counts_fallback(user_id, limit)

    async def _get_tag_counts_fallback(
        self, user_id: str, limit: int = 10
    ) -> List[dict]:
        """Fallback tag counts without the RPC.

        The ``resources`` owner column is ``creator_id`` (a uuid), NOT ``user_id``
        — verified against the live schema + the Resources model. This formerly
        referenced the nonexistent ``user_id`` column and raised PG 42703; it now
        correctly filters on ``creator_id``, so the fallback returns the user's tag
        counts.

        NOTE: the primary path's ``get_user_tag_counts`` RPC (the half of
        ``get_tag_counts`` above this fallback) is addressed separately in
        migration 256 — do NOT touch the RPC call here."""
        async with read_scope() as session:
            result = await session.execute(
                text("SELECT id FROM resources WHERE creator_id = CAST(:uid AS uuid)"),
                {"uid": str(user_id)},
            )
            resource_ids = [r[0] for r in result.all()]
            if not resource_ids:
                return []

            rows = (
                await session.execute(
                    select(
                        Tags.id,
                        Tags.name,
                        Tags.color,
                        Tags.icon,
                        Tags.type,
                        func.count().label("count"),
                    )
                    .select_from(ResourceTags)
                    .join(Tags, Tags.id == ResourceTags.tag_id)
                    .where(ResourceTags.resource_id.in_(resource_ids))
                    .group_by(Tags.id, Tags.name, Tags.color, Tags.icon, Tags.type)
                    .order_by(func.count().desc())
                    .limit(limit)
                )
            ).all()

        return [
            {
                "id": tid,  # bigint → native int (5.3 trap); SnowflakeId str()s it
                "name": name,
                "color": color or "#6366f1",
                "icon": icon,
                "type": type_ or "system",
                "count": count,
            }
            for tid, name, color, icon, type_, count in rows
        ]


__all__ = ["TagsRepositoryOrm"]
