"""Repository for Tags data access (异步) — ORM-only (post-rollout collapse).

ORM 2.0 post-rollout cleanup: ``TagsRepository`` is now the SQLAlchemy 2.0 ORM
implementation directly (the legacy supabase-py REST branch and the former
``TagsRepositoryOrm`` subclass have been folded in and deleted). Call sites go
through ``get_tags_repository()`` (bottom of this file), which unconditionally
returns ``TagsRepository()`` — no flag, no engine-missing REST fallback.

The tag system covers the ``tags`` table CRUD + the ``resource_tags`` junction
(M:N resource↔tag) + the cross-table reads (``tag_groups`` embed, ``resources``
media-id resolution, RPC-backed counts).

★ ID-TYPE FINDING ★
===================
``tags.id`` is **BIGINT** (Snowflake, server-default ``generate_snowflake_id()``),
and ``resource_tags.tag_id`` / ``resource_id`` are BIGINT too. So the 5.3 trap
(bigint → NEVER str()) applies to every tag id; every consumer str()s it at the
boundary (media_fetch_helpers ``str(tag["id"])``, tags_router's ``SnowflakeId``
response type, classification_service binds it straight to a BIGINT column). The
ONLY uuid column in this surface is ``tags.user_id`` (+ ``resource_tags.tagged_by``,
never selected by the legacy).

STRATEGY C — per-field value-type parity
========================================
REST rendered: bigint → JSON number (int), uuid → str, timestamptz → ISO str,
double → number (float), text → str. The ORM returns native types; ``_parity``
restores the REST shape:

  tags.id / scope_id / group_id (BIGINT) → **native int** (5.3 trap; NEVER str()).
  tags.user_id (UUID) → **str** (TagResponse.user_id is a str field; pydantic v2
    rejects a native uuid.UUID for a str field).
  tags.created_at (timestamptz) → **ISO str** ALWAYS.
  tags.enabled (bool) / color / icon / name / name_zh / type (text) → native.
  resource_tags.confidence (double) → native float; tagged_by (uuid) → str.

JUNCTION / BULK WRITES (the resource_tags upsert)
=================================================
``resource_tags`` PK is composite ``(resource_id, tag_id)`` (both explicit
bigints — NO server-default id), so there is NO mixed-PK bulk_upsert CompileError
hazard. add/bulk_add reproduce the PostgREST ``.upsert(...)`` (ON CONFLICT on the
PK DO UPDATE) with ``pg_insert(...).on_conflict_do_update(...)`` in ONE
``write_scope()`` — a single multi-VALUES statement, atomic, returning the rows.

RPC PARITY
==========
``get_all_tags`` reproduces the ``get_tag_counts_by_ids`` RPC's DB-side GROUP BY
directly via an engine-native ``func.count()`` query (byte-identical aggregate),
with the legacy client-side fallback shape kept.

``get_tag_counts`` tries the ``get_user_tag_counts`` RPC (repaired by migration
256 — was JOINing the dropped ``media_tags`` / ``parsed_media.user_id``) and falls
back to an engine-native manual count. ``_get_tag_counts_fallback`` filters on the
real ``resources.creator_id`` (a prior commit fixed the nonexistent
``resources.user_id`` → PG 42703 bug).

``merge_tags`` has no ORM successor — it stays on the supabase-py RPC path
(``merge_tags`` SECURITY DEFINER proc) via ``_get_client()``. This is a conscious
keep (RPC precedent), not dead REST to delete.

Reads return [] / None on the documented paths; writes commit via ``write_scope()``.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.scope import Scope, request_scope
from app.db.session import read_scope, write_scope
from app.models import Resources, ResourceTags, TagGroups, Tags
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict

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


# Columns where an explicit None is a real instruction ("clear it"), not the
# caller's way of saying "leave this alone".
_NULLABLE_TAG_ATTRS = {"group_id"}
# BIGINT columns. JSON carries these as strings (Snowflake ids overflow JS
# numbers), and asyncpg refuses a str for a BIGINT bind param —
# ``DataError: 'str' object cannot be interpreted as an integer`` → 500.
# ``create_tag`` and the admin repo already coerced; ``update_tag`` did not,
# which killed every tag→group assignment in prod until 2026-08-26.
_BIGINT_TAG_ATTRS = {"group_id"}


def _tag_update_values(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Column values for an UPDATE, from loose keyword args."""
    values: Dict[str, Any] = {}
    for key, value in kwargs.items():
        if key not in _TAG_ATTRS:
            continue
        if value is None and key not in _NULLABLE_TAG_ATTRS:
            continue
        if value is not None and key in _BIGINT_TAG_ATTRS:
            value = int(value)
        values[key] = value
    return values


def _tag_row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full Tags row."""
    return _parity(_orm_obj_to_dict(obj, _TAG_N2A))


def _rt_row(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped, strategy-C-parity dict for one full ResourceTags row."""
    return _parity(_orm_obj_to_dict(obj, _RT_N2A))


def pick_note_tag_match(word: str, rows: list[dict]) -> Optional[int]:
    """Spec §5 ranking: own user tag > system/time; name hit > name_zh hit;
    oldest created_at wins. ``rows`` are candidate tag dicts already filtered
    to lower(name)/lower(name_zh) == word and visibility scope."""
    w = word.lower()

    def _key(r: dict):
        return (
            0 if r.get("type") == "user" else 1,
            0 if (r.get("name") or "").lower() == w else 1,
            str(r.get("created_at") or ""),
        )

    hits = [
        r
        for r in rows
        if (r.get("name") or "").lower() == w or (r.get("name_zh") or "").lower() == w
    ]
    if not hits:
        return None
    return int(sorted(hits, key=_key)[0]["id"])


class TagsRepository:
    """ORM-backed repository for tags CRUD + the resource_tags junction (异步)."""

    def __init__(self):
        pass

    async def resolve_note_tags(self, user_id: str, names: list[str]) -> list[int]:
        """Resolve parsed note-tag words to tag ids, creating origin='note'
        shadow tags for unseen words (spec §2/§5). Idempotent under races via
        unique_tag_per_scope + re-select."""
        words: list[str] = []
        for n in names:
            w = n.strip().lower()
            if w and w not in words:
                words.append(w)
        if not words:
            return []
        resolved: dict[str, int] = {}
        # Race window (accepted by design): the SELECT snapshot below is taken
        # before the per-word INSERTs, so two concurrent callers can each miss a
        # same-named system/time tag and both create a user shadow tag — leaving a
        # benign cross-type duplicate (one 'note' user tag alongside the system
        # one). The unique_tag_per_scope index still prevents same-scope dupes via
        # the ON CONFLICT re-select; the cross-type overlap is tolerated.
        async with write_scope() as session:
            stmt = select(
                Tags.id, Tags.name, Tags.name_zh, Tags.type, Tags.created_at
            ).where(
                or_(
                    func.lower(Tags.name).in_(words),
                    func.lower(Tags.name_zh).in_(words),
                ),
                or_(
                    and_(Tags.type == "user", Tags.user_id == user_id),
                    Tags.type.in_(("system", "time")),
                ),
            )
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
            for w in words:
                hit = pick_note_tag_match(w, rows)
                if hit is not None:
                    resolved[w] = hit
            for w in words:
                if w in resolved:
                    continue
                ins = (
                    pg_insert(Tags)
                    .values(name=w, type="user", user_id=user_id, origin="note")
                    .on_conflict_do_nothing(index_elements=["name", "type", "user_id"])
                    .returning(Tags.id)
                )
                new_id = (await session.execute(ins)).scalar()
                if new_id is None:  # lost the race — re-select
                    new_id = (
                        await session.execute(
                            select(Tags.id).where(
                                Tags.name == w,
                                Tags.type == "user",
                                Tags.user_id == user_id,
                            )
                        )
                    ).scalar()
                if new_id is None:
                    raise RuntimeError(f"resolve_note_tags: failed to ensure tag '{w}'")
                resolved[w] = int(new_id)
        return [resolved[w] for w in words]

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

    async def get_system_tag_ids_by_names(self, names: List[str]) -> Dict[str, int]:
        """英文名 → id，只看 ``type = 'system'`` 行，永不创建。

        意图字段（transcribe / summarize / analyze）映射到 Pipeline 组的
        Transcript / Summary / Analyze；mig 220 的约定是只匹配系统类型，
        同名用户标签不算数。"""
        if not names:
            return {}
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(Tags.name, Tags.id).where(
                        Tags.type == "system", Tags.name.in_(list(names))
                    )
                )
            ).all()
        return {str(name): int(tag_id) for name, tag_id in rows}

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
        prompt_trigger: bool = False,
    ) -> dict:
        """Create a new user tag (id fires the server-default snowflake)."""
        values: Dict[str, Any] = {
            "name": name,
            "type": "user",
            "user_id": user_id,
            "color": color,
            "icon": icon,
            "prompt_trigger": prompt_trigger,
        }
        if name_zh:
            values["name_zh"] = name_zh
        if group_id:
            values["group_id"] = int(group_id)

        async with write_scope() as session:
            # Race-safe get-or-create: two concurrent same-name creates (e.g. a
            # double-fired "Mark to publish" lazily creating its well-known tag)
            # both passed the router's exists-check, and the loser blew up with
            # an unhandled unique_tag_per_scope IntegrityError → 500 (prod
            # 2026-07-19). ON CONFLICT DO NOTHING + re-select makes the loser
            # return the winner's row — concurrent same-intent creation is
            # success, not an error.
            row = (
                (
                    await session.execute(
                        pg_insert(Tags)
                        .values(**values)
                        .on_conflict_do_nothing(constraint="unique_tag_per_scope")
                        .returning(Tags)
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                row = (
                    (
                        await session.execute(
                            select(Tags)
                            .where(Tags.name == name)
                            .where(Tags.type == "user")
                            .where(Tags.user_id == user_id)
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
            if row is None:  # conflict yet no row visible — surface loudly
                raise RuntimeError(
                    f"create_tag: conflict on {name!r} but existing row not found"
                )
            out = _tag_row(row)
        logger.info(f"Created tag: {name} (zh: {name_zh}) for user: {user_id}")
        return out

    async def update_tag(self, tag_id: str, user_id: str, **kwargs) -> Optional[dict]:
        """Update a user tag (scoped to user_id). Drops keys whose value is
        None — EXCEPT the nullable ones, where an explicit None is the whole
        point (``group_id=None`` = move to Uncategorized). Callers must
        therefore pass a nullable key only when the client actually sent it;
        ``update_tag`` cannot tell "omitted" from "null" on its own.

        No-op (return current) when nothing is left to write."""
        update_data = _tag_update_values(kwargs)
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

    async def merge_tags(
        self, target_id: str, source_ids: list[str], user_id: str
    ) -> int:
        """Merge source tags into target via the atomic merge_tags SQL proc.

        Re-points every resource_tags row from the sources onto the target
        (deduped) and deletes the source tags, all in one transaction.
        Returns the target's resulting resource count.

        Same SECURITY DEFINER function (migration 267) the old supabase-py
        ``client.rpc`` path called, now a ``text()`` SELECT on the committing
        ``write_scope()`` session — the function writes, so it must not run
        on a bare read connection. Ownership stays enforced inside the proc
        via ``p_user``; failures raise (unchanged contract).
        """
        async with write_scope() as session:
            result = await session.execute(
                text(
                    "SELECT merge_tags(:p_target, "
                    "CAST(:p_sources AS text[]), CAST(:p_user AS uuid))"
                ),
                {
                    "p_target": str(target_id),
                    "p_sources": [str(s) for s in source_ids],
                    "p_user": user_id,
                },
            )
            value = result.scalar()
        return int(value or 0)

    async def update_tag_admin(self, tag_id: str, **kwargs) -> Optional[dict]:
        """Update any tag (no user_id check). Used for toggling enabled on
        system tags. Same value rules as ``update_tag``."""
        update_data = _tag_update_values(kwargs)
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

    async def get_tags_by_ids(self, ids: list[int], user_id: str) -> List[dict]:
        """Full tag rows for the given ids, scoped to the caller-visible pool
        (spec §5: ``type IN ('system','time') OR (type='user' AND user_id =
        caller)``), in ONE in-list query.

        Backs the topics feed's pool-tag filter, which resolves picked tag ids
        to their name/name_zh word set. Without this scoping, a caller could
        pass another user's private ``type='user'`` tag id and have its
        name/name_zh silently become live filter words (cross-user existence
        oracle + private-word-steered filtering) — mirrors the same predicate
        ``resolve_note_tags`` uses above. Empty ids short-circuits. ``tags.id``
        is BIGINT — the returned ``id`` stays a native int (5.3 trap)."""
        if not ids:
            return []
        async with read_scope() as session:
            objs = (
                (
                    await session.execute(
                        select(Tags).where(
                            Tags.id.in_([int(t) for t in ids]),
                            or_(
                                Tags.type.in_(("system", "time")),
                                and_(Tags.type == "user", Tags.user_id == user_id),
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [_tag_row(o) for o in objs]

    async def get_name_zh_map(self, tag_ids: list[int]) -> Dict[int, str]:
        """id → name_zh for the given tag ids, in ONE in-list query.

        ``get_tag_counts`` (both the get_user_tag_counts RPC and the manual
        fallback) omit ``name_zh``; the statistics endpoint backfills it here so
        Chinese hotspot words still match. Tags with no Chinese alias are absent.
        """
        if not tag_ids:
            return {}
        async with read_scope() as session:
            rows = (
                await session.execute(
                    select(Tags.id, Tags.name_zh).where(
                        Tags.id.in_([int(t) for t in tag_ids])
                    )
                )
            ).all()
        return {int(tid): zh for tid, zh in rows if zh is not None}

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
        migration 256 — do NOT touch the RPC call here.

        Phase C task 3: migrated off the raw ``text()`` SELECT to a real ORM
        ``select(Resources.id)``. The caller (``tags_router.get_tag_statistics``)
        opens no ambient scope of its own (no ``ScopedRequestDep``), so this
        method opens a real per-user ``request_scope(Scope(user_id=user_id))``
        around just this query — a real user boundary (not
        ``system_request_scope``), since ``user_id`` is exactly the
        ``creator_id`` this already filters on; this only satisfies the
        do_orm_execute choke point once ``SCOPE_ENFORCE_RESOURCES`` is on, it
        does not change which rows are returned (the explicit filter below is
        unconditionally the same predicate)."""
        async with request_scope(Scope(user_id=str(user_id))):
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources.id).where(Resources.creator_id == str(user_id))
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


def get_tags_repository() -> "TagsRepository":
    """Return the TagsRepository (SQLAlchemy 2.0 ORM, the only implementation
    post-collapse). Unconditional — no flag, no engine-missing REST fallback."""
    return TagsRepository()
