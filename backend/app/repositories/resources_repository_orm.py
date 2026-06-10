"""SQLAlchemy 2.0 ORM implementation of ResourcesRepository (Task 5.2).

The successor to ``resources_repository_asyncpg.py``. Same Strangler-Fig
multiple-inheritance pattern (overrides the 40 data-access methods that
touch ``resources`` / ``resource_items`` / ``resource_versions`` /
``folders``; inherits the remaining legacy-only methods — the
resource_tags trio + smart-folder trio — from the supabase-py base via
Python MRO), but the internals run on the ORM session scopes from
``app.db.session`` instead of ``db_engine.fetch_one``/``execute`` on a
bare ``connect()``.

THE P0 FIX
==========
The asyncpg path ran every write via ``self.fetch_one("…RETURNING")`` →
``db_engine.fetch_one()`` on a NON-committing ``engine.connect()``. On
connection close the INSERT/UPDATE/DELETE **silently rolled back** — the
returned RETURNING row looked written but the next read saw the OLD value
(silent data loss). This implementation routes every write through
``write_scope()`` (which does ``session.begin()`` and COMMITS), and runs
each multi-statement cascade inside ONE ``write_scope()`` so the cascade
is atomic (all-or-nothing) AND actually persists. The P0-persistence and
cascade-atomicity regression tests in
``tests/integration/test_resources_repository_orm.py`` pin this.

Fidelity contract (the swap must be invisible to all call sites):
  - dict at the boundary — never leak ORM objects. Same exact dict shapes
    as the asyncpg/legacy impls (column projections, nested ``resource``
    from ``row_to_json``, ``parsed_media`` overlay, cascade count dicts).
  - ``_bigint()`` / ``_bigint_list()`` coercion on str-snowflake ids
    before binding to BIGINT columns (asyncpg int8 codec is strict).
  - ``_VERSION_BIGINT_COLS`` coercion preserved in ``create_version``.
  - datetimes bound as tz-aware ``datetime`` objects, never isoformat.
  - enum read-parity: ``resources`` has 3 ``Enum(AiTaskStatus)`` columns
    (transcript_status / summary_status / visual_analysis_status). The ORM
    returns enum MEMBERS; the prior impls returned bare ``str``. Every read
    of a resources row is funnelled through ``_resources_row_to_dict`` which
    unwraps enums to ``.value`` (see ``_plain``).
  - VALUE-TYPE read-parity (strategy-C — AUTHZ-CRITICAL): the ORM read
    helpers (``_orm_obj_to_dict`` / RETURNING ``.mappings()``) return NATIVE
    Python types — ``uuid.UUID`` for uuid columns (creator_id / created_by /
    added_by / uploaded_by) and ``datetime`` for timestamptz columns
    (created_at / updated_at / trashed_at / transcode_at). The legacy REST
    (PostgREST) repo returns these as ``str``. Consumers compare WITHOUT
    coercion — e.g. ``resource.get("creator_id") != auth.user_id`` (ai_router)
    and ``resource["creator_id"] != user_id`` (resources_service), where the
    user id is a ``str`` — so a native ``UUID`` makes ``!=`` ALWAYS true and
    the owner is wrongly DENIED (silent authz break). Every dict the repo
    returns is funnelled through ``_rest_parity`` (uuid → str, datetime → ISO
    str), recursing into nested ``resource`` / ``parsed_media`` sub-dicts, so
    the ORM dict is a true drop-in for the REST baseline. BIGINT ids stay int
    (int on both sides).
"""

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, insert, select, text, update

from app.db.repository_base import AsyncpgRepository
from app.db.scope import is_enforced, scoped_sql, system_request_scope
from app.db.session import read_scope, write_scope
from app.models import Folders, ResourceItems, Resources, ResourceVersions
from app.repositories._orm_helpers import _name_to_attr, _orm_obj_to_dict, _plain
from app.repositories.resources_repository import (
    _AI_STATUS_COMPLETED,
    _AI_STATUS_FIELDS,
    EXPIRED_TRASH_BATCH,
    UNTRANSCODED_BATCH,
    ResourcesRepository,
)

_RESOURCES_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Resources)
_RESOURCE_ITEMS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(ResourceItems)
_RESOURCE_VERSIONS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(ResourceVersions)
_FOLDERS_NAME_TO_ATTR: Dict[str, str] = _name_to_attr(Folders)


def _to_rest_value(value: Any) -> Any:
    """Coerce ONE native ORM read value to its PostgREST/REST wire form.

    Strategy-C value-type parity (AUTHZ-CRITICAL): the ORM read helpers hand
    back native ``uuid.UUID`` / ``datetime`` objects where the legacy REST
    (PostgREST) repo returned ``str``. Generic ``isinstance`` sweep (NOT a
    column-name allowlist) so EVERY uuid/datetime field is covered without
    enumeration, recursing into nested dicts and lists of dicts (e.g. the
    embedded ``resource`` / ``parsed_media`` sub-dicts). BIGINT ids are ``int``
    on both sides → pass through untouched. Enums are already unwrapped to
    ``.value`` upstream by ``_plain`` (strings pass through here, so enum
    read-parity is preserved). ``None`` and every other scalar pass through."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        # tz-aware UTC datetime → ``...+00:00`` matches PostgREST. (PostgREST
        # trims trailing zeros in microseconds; that residual cosmetic diff is
        # accepted + pre-existing across the rollout — we do NOT replicate it.)
        return value.isoformat()
    if isinstance(value, dict):
        return _rest_parity(value)
    if isinstance(value, list):
        return [_to_rest_value(v) for v in value]
    return value


def _rest_parity(d: Dict[str, Any]) -> Dict[str, Any]:
    """Return a NEW dict (immutable — never mutate the input) with strategy-C
    value-type coercion applied to every value: uuid → str, datetime → ISO
    str, recursing into nested dicts / lists of dicts. See ``_to_rest_value``.

    Applied at EVERY dict-returning boundary of this repo so the ORM dict is a
    true drop-in for the REST baseline (fixes the 6 ``creator_id`` authz
    comparisons in ai_router / resources_service that break on a native UUID)."""
    return {k: _to_rest_value(v) for k, v in d.items()}


def _resources_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``resources`` ORM row (enum + value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _RESOURCES_NAME_TO_ATTR))


def _resource_item_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``resource_items`` ORM row (value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _RESOURCE_ITEMS_NAME_TO_ATTR))


def _version_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``resource_versions`` ORM row (value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _RESOURCE_VERSIONS_NAME_TO_ATTR))


def _folder_row_to_dict(obj: Any) -> Dict[str, Any]:
    """SELECT *-shaped dict for a ``folders`` ORM row (value safe)."""
    return _rest_parity(_orm_obj_to_dict(obj, _FOLDERS_NAME_TO_ATTR))


def _mappings_dict(row: Any) -> Dict[str, Any]:
    """Plain dict from a RETURNING ``.mappings()`` row, enum- and value-safe.

    RETURNING rows still pass through the column type result processors, so
    an ``Enum`` column comes back as an enum MEMBER here too — funnel through
    ``_plain`` for the same bare-str parity as reads — and uuid/datetime
    columns come back native, so funnel through ``_rest_parity`` for the same
    REST wire shape (uuid → str, datetime → ISO str)."""
    return _rest_parity({k: _plain(v) for k, v in dict(row).items()})


class ResourcesRepositoryOrm(AsyncpgRepository, ResourcesRepository):
    """ORM-backed ResourcesRepository.

    Overrides the 40 data-access methods on the four tables; the
    resource_tags trio (add/remove/get) and smart-folder trio
    (get/create/execute) inherit the legacy supabase-py path via MRO.
    ``_bigint`` / ``_bigint_list`` are inherited from AsyncpgRepository."""

    TABLE = "resources"

    # ── Resources CRUD ──────────────────────────────────────────────

    async def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """INSERT a resource as an ORM instance (the flip-safe write path).

        A2.5: replaced the Core ``insert(Resources).values().returning()`` —
        which the scope choke point FORBIDS under a user scope (``_forbid_
        scoped_bulk_dml``: a Core INSERT never reaches ``before_insert`` so it
        can't be owner-stamped) — with a ``session.add(instance)`` flush. The
        instance flush DOES flow through ``before_insert`` (``_stamp_user_on_
        insert``): under a USER scope it stamps/asserts ``creator_id ==
        scope.user_id``; under SYSTEM it leaves the owner as-given; with the
        flag OFF it is a plain legacy insert (no stamp / no raise). The post-
        flush ``refresh`` reloads the server-default columns (snowflake ``id``,
        ``created_at``, the 3 ai-status enums, …) so the returned dict matches
        the old RETURNING-row shape exactly — it is wrapped in
        ``system_request_scope`` because ``session.refresh`` (and lazy-load of
        the post-flush-expired columns by ``_resources_row_to_dict``) issues a
        ``from_statement`` PK reload that the choke point treats as
        non-injectable (deny-by-default RAISE under a USER scope). Reading back
        the row we JUST wrote — whose ownership ``before_insert`` already
        verified equals the active scope — to materialize server defaults is a
        safe, owner-agnostic internal read.

        ENFORCEMENT-GATED (inert guarantee): the ``system_request_scope`` wrap is
        applied ONLY when ``resources`` is enforced (``is_enforced``). Flag-off the
        refresh can't raise (no injection) so no wrap is needed — and skipping it
        avoids emitting a spurious ``orm-refresh-own-write`` audit log + DB row on
        every create, keeping the flag-off path byte-for-byte legacy."""
        try:
            async with write_scope() as session:
                obj = Resources(
                    **{_RESOURCES_NAME_TO_ATTR.get(k, k): v for k, v in data.items()}
                )
                session.add(obj)
                await session.flush()  # before_insert fires (stamp/assert)
                refresh_cm = (
                    system_request_scope(reason="orm-refresh-own-write")
                    if is_enforced("resources")
                    else nullcontext()
                )
                async with refresh_cm:
                    await session.refresh(obj)  # load server-default columns
                    created = _resources_row_to_dict(obj)
            logger.info(f"Created resource: {data.get('filename')}")
            return created
        except Exception as e:
            logger.error(f"Failed to create resource: {e}")
            raise

    async def get_resource_by_id(self, resource_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.id == self._bigint(resource_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource {resource_id}: {e}")
            return None

    async def get_resource_by_media_id(self, media_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.media_id == self._bigint(media_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource by media_id {media_id}: {e}")
            return None

    async def get_resource_by_platform_id(
        self, platform_id: str
    ) -> Optional[Dict[str, Any]]:
        """Two-step lookup parsed_media.platform_id → resources.media_id.

        Kept as two queries (rather than a JOIN) to match legacy shape —
        caller may already have the parsed_media row cached and collapsing
        into a JOIN would diverge the cache key."""
        try:
            async with read_scope() as session:
                # parsed_media.platform_id → id (text lookup). Use raw text()
                # so we don't import ParsedMedia here just for one scalar.
                media_id = await session.scalar(
                    text(
                        "SELECT id FROM parsed_media "
                        "WHERE platform_id = :pid LIMIT 1"
                    ),
                    {"pid": platform_id},
                )
            if not media_id:
                return None
            # parsed_media.id and resources.media_id are both BIGINT
            # (Snowflake, migration 051); pass the int through directly.
            return await self.get_resource_by_media_id(media_id)
        except Exception as e:
            logger.error(f"Failed to get resource by platform_id {platform_id}: {e}")
            return None

    async def get_resource_by_media_id_and_creator(
        self, media_id: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Resources)
                    .where(Resources.media_id == self._bigint(media_id))
                    .where(Resources.creator_id == creator_id)
                    .limit(1)
                )
                row = result.scalars().first()
                return _resources_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Failed to get resource for media={media_id}, "
                f"creator={creator_id}: {e}"
            )
            return None

    async def get_completed_resource_by_url_and_creator(
        self, url: str, creator_id: str
    ) -> Optional[Dict[str, Any]]:
        """L2 dedup probe. JOIN with parsed_media on media_id, filter on
        original_url and the appropriate completion status (image vs video).

        Returns the same nested shape as legacy: ``{id, media_id,
        parsed_media: {id, platform_id, original_url, ...}}`` so call sites
        read the inner dict the same way.

        A3: the tenant predicate is BUILT by ``scoped_sql`` (the ambient-scope
        raw-SQL choke-point backstop) and AND-spliced into the WHERE, NOT the
        passed-in ``:creator_id`` bind. The ``creator_id`` arg stays in the
        signature for interface stability but is SUPERSEDED by the ambient scope
        (on every real call path the acting user IS ``creator_id``, so the bound
        value is identical — verified by the flag-off parity test). Under SYSTEM
        the predicate opens to all owners; under no scope (or an empty-identity
        user scope) ``scoped_sql`` fail-closes."""
        try:
            # scoped_sql owns the predicate shape (CAST(:scope_user_id AS uuid)
            # IS NULL OR r.creator_id = ...): the caller can only AND it in, never
            # park a bare token in a non-filtering position. The f-string splice
            # is safe — ``pred`` is helper-built and "r.creator_id" is a hardcoded
            # literal; the user value travels only via the bound :scope_user_id
            # param. Built INSIDE the try so a fail-closed raise (no scope / empty
            # identity) degrades to None here rather than propagating — preserving
            # the legacy "probe failure is non-fatal" contract of this L2 dedup.
            pred, params = scoped_sql("r.creator_id", {"url": url})
            sql = (
                "SELECT r.id AS r_id, r.media_id AS r_media_id, "
                "       p.id AS p_id, p.platform_id, p.original_url, "
                "       p.video_download_status, p.image_download_status, "
                "       p.media_type "
                "FROM resources r "
                "INNER JOIN parsed_media p ON r.media_id = p.id "
                f"WHERE {pred} "
                "  AND p.original_url = :url "
                "LIMIT 1"
            )
            async with read_scope() as session:
                result = await session.execute(text(sql), params)
                row = result.mappings().first()
            if not row:
                return None
            row = dict(row)

            mt = row.get("media_type")
            is_image = str(mt) in ("2", "68", "image", "images")
            status = (
                row.get("image_download_status")
                if is_image
                else row.get("video_download_status")
            )
            # Statuses come back as the PG enum's text value (str) via text();
            # funnel through _plain in case the driver hands back a member.
            if _plain(status) != "completed":
                return None

            # ids/strings/statuses only (no uuid/datetime here) — _rest_parity is
            # a no-op for the current shape, applied for uniformity + safety if
            # the projection ever grows a uuid/timestamptz column.
            return _rest_parity(
                {
                    "id": row["r_id"],
                    "media_id": row["r_media_id"],
                    "parsed_media": {
                        "id": row["p_id"],
                        "platform_id": row["platform_id"],
                        "original_url": row["original_url"],
                        "video_download_status": _plain(row["video_download_status"]),
                        "image_download_status": _plain(row["image_download_status"]),
                        "media_type": row["media_type"],
                    },
                }
            )
        except Exception as e:
            logger.debug(
                f"[ResourcesRepo] L2 dedup probe failed url={url[:40]} "
                f"creator={creator_id}: {e}"
            )
            return None

    async def get_owned_platform_ids(
        self, platform_ids: List[str], creator_id: str
    ) -> set:
        """Of the given vids (parsed_media.platform_id), return the subset this
        user has already downloaded (a resources row with file_path set). ONE
        batched query for the whole list — no N+1. Empty input short-circuits
        to ``set()`` without a query.

        A3: the tenant predicate is BUILT by ``scoped_sql`` and AND-spliced in,
        NOT the passed-in ``:creator_id`` bind. The ``creator_id`` arg is kept for
        interface stability but SUPERSEDED by the ambient scope (acting user ==
        creator_id on every real path; identical bound value — see flag-off
        parity test). SYSTEM opens to all owners; no scope (or empty-identity user
        scope) fail-closes."""
        if not platform_ids:
            return set()
        try:
            # scoped_sql owns the predicate shape (see get_completed_resource_by_
            # url_and_creator). Safe f-string splice: helper-built pred + literal
            # column. Built INSIDE the try so a fail-closed raise (no scope /
            # empty identity) degrades to set() rather than propagating.
            pred, params = scoped_sql("r.creator_id", {"pids": list(platform_ids)})
            sql = (
                "SELECT DISTINCT p.platform_id "
                "FROM resources r "
                "INNER JOIN parsed_media p ON r.media_id = p.id "
                f"WHERE {pred} "
                "  AND p.platform_id = ANY(:pids) "
                "  AND r.file_path IS NOT NULL"
            )
            async with read_scope() as session:
                result = await session.execute(text(sql), params)
                return {r["platform_id"] for r in result.mappings().all()}
        except Exception as e:
            logger.error(
                f"Failed to resolve owned platform_ids for creator "
                f"{creator_id}: {e}"
            )
            return set()

    async def update_resource(
        self, resource_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """UPDATE a resource by id via load-then-modify, COMMITTING via
        write_scope (the P0 fix).

        A2.5: replaced the Core ``update(Resources).where().values().
        returning()`` — FORBIDDEN under a user scope (``_forbid_scoped_bulk_
        dml``: a bulk UPDATE tree can't be safely tenant-filtered) — with the
        sanctioned load-then-modify pattern. ``session.get`` flows through the
        SELECT injection: under a USER scope the choke point injects
        ``creator_id == scope.user_id``, so a row owned by another user loads
        as None (correct fail-closed isolation) and we return ``{}`` —
        preserving the legacy "update of a nonexistent id → {}" contract. The
        dirty-instance flush is governed by the unit-of-work, no Core DML.

        The post-flush ``refresh`` (re-materializing server-managed columns
        like ``updated_at`` for the returned dict) is wrapped in
        ``system_request_scope`` for the same reason as ``create_resource``: a
        ``from_statement`` PK reload is non-injectable → deny-by-default RAISE
        under a USER scope. The row's ownership was already proven by the
        injected ``session.get`` above, so reading it back is owner-agnostic-
        safe.

        ENFORCEMENT-GATED (inert guarantee): the ``system_request_scope`` wrap is
        applied ONLY when ``resources`` is enforced (``is_enforced``). Flag-off the
        refresh can't raise (no injection) so no wrap is needed — and skipping it
        avoids a spurious ``orm-refresh-own-write`` audit log + DB row on every
        update, keeping the flag-off path byte-for-byte legacy."""
        try:
            async with write_scope() as session:
                obj = await session.get(Resources, self._bigint(resource_id))
                if obj is None:
                    return {}
                for k, v in data.items():
                    setattr(obj, _RESOURCES_NAME_TO_ATTR.get(k, k), v)
                await session.flush()
                refresh_cm = (
                    system_request_scope(reason="orm-refresh-own-write")
                    if is_enforced("resources")
                    else nullcontext()
                )
                async with refresh_cm:
                    await session.refresh(obj)
                    updated = _resources_row_to_dict(obj)
            logger.info(f"Updated resource {resource_id}")
            return updated
        except Exception as e:
            logger.error(f"Failed to update resource {resource_id}: {e}")
            raise

    async def delete_resource(self, resource_id: str) -> bool:
        """DELETE a resource by id via load-then-delete, COMMITTING via
        write_scope.

        A2.5: replaced the Core ``delete(Resources).where()`` — FORBIDDEN
        under a user scope (``_forbid_scoped_bulk_dml``) — with a
        load-then-``session.delete(instance)``. ``session.get`` flows through
        the SELECT injection: under a USER scope it injects ``creator_id ==
        scope.user_id``, so a CROSS-USER id loads None → no-op → still returns
        ``True``. Returning True on not-found / not-owned keeps the boolean
        interface stable (idempotent delete) AND is the correct isolation
        behaviour: a user cannot observe (or delete) another user's row, and a
        cross-user delete is silently a no-op rather than an error."""
        try:
            async with write_scope() as session:
                obj = await session.get(Resources, self._bigint(resource_id))
                if obj is not None:
                    await session.delete(obj)
                    # Not load-bearing: write_scope commits (and thus flushes)
                    # at block exit. Kept only to surface any FK/constraint
                    # error inside this try (so it is logged + re-raised here)
                    # rather than at the outer commit. Contrast create/update,
                    # where flush IS required (before_insert / dirty-UPDATE
                    # must hit the DB before the subsequent refresh).
                    await session.flush()
            logger.info(f"Deleted resource {resource_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource {resource_id}: {e}")
            raise

    async def count_resources_by_media_id(self, media_id: str) -> int:
        """Count how many resources (across ALL users) reference a parsed_media.

        A2.5 — DATA-LOSS FIX + well-formed projection. This is a CROSS-USER GC
        reference count: callers (``resources_service.permanent_delete`` /
        ``cleanup_expired_trash``) use ``remaining == 0`` to decide whether to
        delete the SHARED physical files + parsed_media record. Under
        enforcement, if this count were user-scoped, user A deleting their
        resource would NOT see user B's resource for the same media_id → it
        would delete files B still needs. So we make it ALWAYS GLOBAL by
        wrapping the query in ``system_request_scope`` (owner-agnostic
        regardless of the caller's ambient scope — it is semantically "does ANY
        user still reference this media"). Nesting SYSTEM inside the already-
        SYSTEM ``cleanup_expired_trash`` path is a no-op set/reset; inside the
        USER ``permanent_delete`` path it temporarily sets SYSTEM for this read
        then resets to USER.

        Also projects a scoped column — ``func.count(Resources.id)`` instead of
        the old ``func.count()`` + ``select_from`` — so the statement is
        well-formed (a bare ``count(*)`` over a scoped table with no projected
        scoped column is deny-by-default RAISE; here SYSTEM scope means no
        injection either way, but the projected form is the canonical shape).

        A4 — RE-RAISE ON ERROR (data-loss hardening): this method must NEVER
        fabricate a ``0``. A transient DB error returning 0 is the DANGEROUS
        value — the GC callers (``permanent_delete`` / ``cleanup_expired_trash``)
        read ``remaining == 0`` to decide whether to delete the SHARED physical
        files + parsed_media record, so a fake 0 would delete files other users
        still reference. We log and RE-RAISE; the callers catch and SKIP the
        physical-file/media GC on uncertainty (never GC on a count failure).
        NOTE: no scheduled sweeper reclaims those skipped files — the only orphan
        sweeper (``scheduled_cleanup._sweep_orphan_upload_dirs``) walks ONLY the
        ``teams/{scope}/uploads/{resource_id}/`` tree by resource_id, NOT the
        ``global/resources/.../{media_id}/`` download tree that
        ``_delete_physical_files`` handles, nor the parsed_media row — so they
        leak until a later successful permanent_delete or manual cleanup.
        Accepted: leaking files on a rare transient count error beats deleting
        files another user still references.

        ENFORCEMENT-GATED (inert guarantee): the ``system_request_scope`` wrap that
        forces this count GLOBAL is applied ONLY when ``resources`` is enforced
        (``is_enforced``). Flag-off the choke point does not inject the tenant
        filter into this query anyway (it is already a cross-user count regardless
        of ambient scope), so the wrap is a no-op for correctness — skipping it
        avoids a spurious ``media-refcount-gc`` audit log + DB row on every GC
        refcount, keeping the flag-off path byte-for-byte legacy. Flag-on the wrap
        applies, keeping the count global under a USER scope (the data-loss
        regression guard)."""
        try:
            count_cm = (
                system_request_scope(reason="media-refcount-gc")
                if is_enforced("resources")
                else nullcontext()
            )
            async with count_cm:
                async with read_scope() as session:
                    count = await session.scalar(
                        select(func.count(Resources.id)).where(
                            Resources.media_id == self._bigint(media_id)
                        )
                    )
            return int(count or 0)
        except Exception as e:
            # NEVER return a fabricated 0 — a count failure must abort the
            # caller's shared-file GC, not silently green-light it.
            logger.error(f"Failed to count resources for media {media_id}: {e}")
            raise

    # ── Hash-based duplicate lookup ─────────────────────────────────

    async def find_by_hash(self, file_hash: str, creator_id: str) -> list[dict]:
        """Find non-trashed resources with the same file hash for a given
        creator. Column projection matches legacy exactly."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id,
                        Resources.filename,
                        Resources.file_type,
                        Resources.mime_type,
                        Resources.file_size_bytes,
                        Resources.thumbnail_path,
                        Resources.cover_image_path,
                        Resources.created_at,
                    )
                    .where(Resources.file_hash == file_hash)
                    .where(Resources.creator_id == creator_id)
                    .where(Resources.is_trashed.is_(False))
                )
                return [_rest_parity(dict(r)) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to find resources by hash: {e}")
            return []

    # ── Resource Items ──────────────────────────────────────────────

    async def find_resource_item(
        self,
        resource_id: str,
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None = None,
    ) -> dict | None:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                stmt = (
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                )
                if folder_id:
                    stmt = stmt.where(
                        ResourceItems.folder_id == self._bigint(folder_id)
                    )
                else:
                    stmt = stmt.where(ResourceItems.folder_id.is_(None))
                result = await session.execute(stmt.limit(1))
                row = result.scalars().first()
                return _resource_item_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to find resource_item: {e}")
            return None

    async def create_resource_item(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(ResourceItems)
                    .values(**data)
                    .returning(*ResourceItems.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(
                f"Created resource_item for resource {data.get('resource_id')} "
                f"in scope {data.get('scope_id')}"
            )
            return created
        except Exception as e:
            logger.error(f"Failed to create resource_item: {e}")
            raise

    async def _resource_ids_for_platforms(self, platforms: List[str]) -> List[str]:
        """Resource ids whose linked parsed_media.source_platform is in
        ``platforms``. Two-step lookup retained for parity. Returns list[str]
        (the inherited get_resource_items legacy path feeds it to PostgREST)."""
        cleaned = [p.strip() for p in platforms if p and p.strip()]
        if not cleaned:
            return []
        try:
            async with read_scope() as session:
                media_rows = await session.execute(
                    text(
                        "SELECT id FROM parsed_media "
                        "WHERE source_platform = ANY(:platforms)"
                    ),
                    {"platforms": cleaned},
                )
                media_ids_int = [r["id"] for r in media_rows.mappings().all()]
                if not media_ids_int:
                    return []
                resource_rows = await session.execute(
                    text(
                        "SELECT id FROM resources " "WHERE media_id = ANY(:media_ids)"
                    ),
                    {"media_ids": media_ids_int},
                )
                return [str(r["id"]) for r in resource_rows.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to resolve resource ids for platforms: {e}")
            return []

    async def _resource_ids_with_all_tags(self, tag_ids: List[str]) -> List[str]:
        """Resource ids that carry EVERY tag in ``tag_ids`` (AND).

        Single-query GROUP BY HAVING count = N. ``tag_id`` is a bigint column
        (migration 077+); coerce the str ids and bind as a bigint array."""
        if not tag_ids:
            return []
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT resource_id FROM resource_tags "
                        "WHERE tag_id = ANY(:tag_ids) "
                        "GROUP BY resource_id "
                        "HAVING count(DISTINCT tag_id) = :n"
                    ),
                    {
                        "tag_ids": self._bigint_list(tag_ids),
                        "n": len(set(tag_ids)),
                    },
                )
                return [str(r["resource_id"]) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to intersect resource tag ids: {e}")
            return []

    async def get_resource_item(
        self, resource_id: str, scope_type: Optional[str], scope_id: str
    ) -> Optional[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resource_item_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource_item: {e}")
            return None

    async def get_resource_item_in_folder(
        self,
        resource_id: str,
        scope_type: Optional[str],
        scope_id: str,
        folder_id: str | None,
    ) -> Optional[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                stmt = (
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .where(ResourceItems.scope_id == self._bigint(scope_id))
                )
                if folder_id:
                    stmt = stmt.where(
                        ResourceItems.folder_id == self._bigint(folder_id)
                    )
                else:
                    stmt = stmt.where(ResourceItems.folder_id.is_(None))
                result = await session.execute(stmt.limit(1))
                row = result.scalars().first()
                return _resource_item_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get resource_item in folder: {e}")
            return None

    async def get_first_resource_item(
        self, resource_id: str
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _resource_item_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get first resource_item: {e}")
            return None

    async def update_resource_item(
        self, item_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ResourceItems)
                    .where(ResourceItems.id == self._bigint(item_id))
                    .values(**data)
                    .returning(*ResourceItems.__table__.columns)
                )
                row = result.mappings().first()
                return _mappings_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update resource_item {item_id}: {e}")
            raise

    async def delete_resource_item(self, item_id: str) -> bool:
        """Delete a resource_item by ID. The DB trigger auto-trashes the
        parent resource if this was the last reference."""
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ResourceItems).where(
                        ResourceItems.id == self._bigint(item_id)
                    )
                )
            logger.info(f"Deleted resource_item {item_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete resource_item {item_id}: {e}")
            raise

    async def count_resource_items(self, resource_id: str) -> int:
        try:
            async with read_scope() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(ResourceItems)
                    .where(ResourceItems.resource_id == self._bigint(resource_id))
                )
            return int(count or 0)
        except Exception as e:
            logger.error(f"Failed to count items for resource {resource_id}: {e}")
            return 0

    # ── Listing with filters (the 22-arg behemoth) ──────────────────

    @staticmethod
    def _build_mime_sql(types: Optional[List[str]]) -> Optional[str]:
        """Translate type categories into a SQL ``OR`` expression. Returns
        None when no filter is needed. Values are inlined as SQL string
        literals (a fixed set of well-known LIKE patterns — no injection
        surface)."""
        if not types:
            return None
        categories = {t.strip() for t in types if t and t.strip()}
        if not categories:
            return None

        document_clause = (
            "(r.mime_type = 'application/pdf' "
            "OR r.mime_type LIKE 'application/msword%' "
            "OR r.mime_type LIKE 'application/vnd.%' "
            "OR r.mime_type LIKE 'text/%')"
        )
        known_clause = (
            "(r.mime_type LIKE 'video/%' "
            "OR r.mime_type LIKE 'image/%' "
            "OR r.mime_type LIKE 'audio/%' "
            "OR r.mime_type = 'application/pdf' "
            "OR r.mime_type LIKE 'application/msword%' "
            "OR r.mime_type LIKE 'application/vnd.%' "
            "OR r.mime_type LIKE 'text/%')"
        )

        clauses: List[str] = []
        for category in categories:
            if category == "video":
                clauses.append("r.mime_type LIKE 'video/%'")
            elif category == "image":
                clauses.append("r.mime_type LIKE 'image/%'")
            elif category == "audio":
                clauses.append("r.mime_type LIKE 'audio/%'")
            elif category == "document":
                clauses.append(document_clause)
            elif category == "other":
                clauses.append(f"NOT {known_clause}")
            # Silently ignore unknown categories (matches legacy behaviour).

        if not clauses:
            return None
        return "(" + " OR ".join(clauses) + ")"

    async def get_resource_items(
        self,
        scope_id: str,
        scope_type: Optional[str] = None,
        folder_id: Optional[str] = None,
        include_trashed: bool = False,
        tag_ids: Optional[List[str]] = None,
        min_rating: Optional[int] = None,
        types: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        ai_transcribed: Optional[bool] = None,
        ai_summarized: Optional[bool] = None,
        ai_analyzed: Optional[bool] = None,
        created_after: Optional[date] = None,
        created_before: Optional[date] = None,
        duration_min: Optional[int] = None,
        duration_max: Optional[int] = None,
        aspect_ratios: Optional[List[str]] = None,
        min_likes: Optional[int] = None,
        min_comments: Optional[int] = None,
        min_favorites: Optional[int] = None,
        min_shares: Optional[int] = None,
        social_combine: str = "and",
        has_comments: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """List resource_items joined to their resources.

        Same dynamic-WHERE SQL the asyncpg impl built; the embedded
        ``resource`` is produced by ``row_to_json(r.*)`` and decoded to a
        dict. Preserves all per-parameter semantics including the no-ops
        (``aspect_ratios`` + social metrics) the frontend chip applies
        client-side. Bound named params (:name) — no asyncpg ``$N``."""
        try:
            # AND-semantic tag filter — pre-resolve via helper.
            matched_resource_ids: Optional[List[str]] = None
            if tag_ids:
                matched_resource_ids = await self._resource_ids_with_all_tags(tag_ids)
                if not matched_resource_ids:
                    return []

            # Platform pre-resolution + intersection.
            if platforms:
                platform_resource_ids = await self._resource_ids_for_platforms(
                    platforms
                )
                if not platform_resource_ids:
                    return []
                if matched_resource_ids is None:
                    matched_resource_ids = platform_resource_ids
                else:
                    platform_set = set(platform_resource_ids)
                    matched_resource_ids = [
                        rid for rid in matched_resource_ids if rid in platform_set
                    ]
                    if not matched_resource_ids:
                        return []

            # Build dynamic WHERE with named bind params.
            where: List[str] = ["i.scope_id = :scope_id"]
            params: Dict[str, Any] = {"scope_id": self._bigint(scope_id)}

            if folder_id:
                where.append("i.folder_id = :folder_id")
                params["folder_id"] = self._bigint(folder_id)
            else:
                where.append("i.folder_id IS NULL")

            if not include_trashed:
                where.append("r.is_trashed = false")

            if matched_resource_ids is not None:
                where.append("i.resource_id = ANY(:matched_ids)")
                params["matched_ids"] = self._bigint_list(matched_resource_ids)

            if min_rating is not None:
                where.append("r.rating >= :min_rating")
                params["min_rating"] = int(min_rating)

            mime_sql = self._build_mime_sql(types)
            if mime_sql:
                where.append(mime_sql)

            # AI status filters: each requires == "completed".
            for key, flag, column in (
                ("ai_transcribed", ai_transcribed, _AI_STATUS_FIELDS["transcribed"]),
                ("ai_summarized", ai_summarized, _AI_STATUS_FIELDS["summarized"]),
                ("ai_analyzed", ai_analyzed, _AI_STATUS_FIELDS["analyzed"]),
            ):
                if flag is True:
                    where.append(f'r."{column}" = :{key}')
                    params[key] = _AI_STATUS_COMPLETED

            if created_after is not None:
                where.append("r.created_at >= :created_after")
                params["created_after"] = datetime.combine(
                    created_after, datetime.min.time(), tzinfo=timezone.utc
                )
            if created_before is not None:
                where.append("r.created_at <= :created_before")
                params["created_before"] = datetime.combine(
                    created_before, datetime.max.time(), tzinfo=timezone.utc
                )

            if duration_min is not None:
                where.append("r.duration_seconds >= :duration_min")
                params["duration_min"] = int(duration_min)
            if duration_max is not None:
                where.append("r.duration_seconds <= :duration_max")
                params["duration_max"] = int(duration_max)

            # Tail no-ops kept for parity (filtered client-side).
            _ = (
                aspect_ratios,
                min_likes,
                min_comments,
                min_favorites,
                min_shares,
                social_combine,
                has_comments,
            )

            # NOTE: resource_items has NO ``updated_at`` column in the current
            # schema (PR-E era). The legacy ``select("*, resource:...")``
            # returned only the columns that exist, so callers never depend on
            # an item-level ``updated_at``. We select the real item columns +
            # the embedded ``resource`` (row_to_json, decoded to a dict) and
            # alias i.created_at so it survives the resource overlay.
            sql = (
                "SELECT i.id, i.resource_id, i.scope_id, "
                "       i.folder_id, i.added_by, i.library_id, "
                "       i.created_at AS i_created_at, "
                "       row_to_json(r.*) AS resource "
                "FROM resource_items i "
                "INNER JOIN resources r ON i.resource_id = r.id "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY i.created_at DESC"
            )

            async with read_scope() as session:
                result = await session.execute(text(sql), params)
                rows = [dict(r) for r in result.mappings().all()]
            for row in rows:
                resource = row.get("resource")
                if isinstance(resource, str):
                    row["resource"] = json.loads(resource)
                row["created_at"] = row.pop("i_created_at")
            return [_rest_parity(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get resource items: {e}")
            return []

    # ── Trash listing ───────────────────────────────────────────────

    async def get_expired_trashed_resources(
        self, older_than_days: int = 30, limit: int = EXPIRED_TRASH_BATCH
    ) -> List[Dict[str, Any]]:
        """Trashed resources older than N days, for permanent cleanup.

        Bounded + ordered to match the REST twin: at most ``limit`` rows,
        oldest-trashed first. Was an unbounded SELECT (whole expired set into
        RAM at scale); the daily sweeper re-runs to drain a larger backlog.
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        Resources.id,
                        Resources.file_path,
                        Resources.cover_image_path,
                    )
                    .where(Resources.is_trashed.is_(True))
                    .where(Resources.trashed_at < cutoff)
                    .order_by(Resources.trashed_at.asc())
                    .limit(limit)
                )
                return [_rest_parity(dict(r)) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get expired trashed resources: {e}")
            return []

    async def get_trashed_resources(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        """Trashed resources in a scope. JOIN with resources, re-shape flat
        row → ``{item_columns..., resource: {...}}`` to match the
        embedded-PostgREST shape callers depend on. ``row_to_json(r.*)`` is
        decoded explicitly (the driver returns it as a str)."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "SELECT i.id, i.resource_id, i.scope_id, "
                        "       i.folder_id, i.added_by, i.library_id, "
                        "       i.created_at AS i_created_at, "
                        "       row_to_json(r.*) AS resource "
                        "FROM resource_items i "
                        "INNER JOIN resources r ON i.resource_id = r.id "
                        "WHERE i.scope_id = :scope_id "
                        "  AND r.is_trashed = true "
                        "ORDER BY i.created_at DESC"
                    ),
                    {"scope_id": self._bigint(scope_id)},
                )
                rows = [dict(r) for r in result.mappings().all()]
            for row in rows:
                resource = row.get("resource")
                if isinstance(resource, str):
                    row["resource"] = json.loads(resource)
                row["created_at"] = row.pop("i_created_at")
            return [_rest_parity(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get trashed resources: {e}")
            return []

    # ── Resource Versions ───────────────────────────────────────────

    # resource_versions BIGINT columns that snowflake ids travel into as
    # strings. asyncpg refuses to cast str → int8 so coerce at the boundary.
    _VERSION_BIGINT_COLS = ("resource_id", "file_size_bytes")

    async def create_version(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            coerced = {
                k: (self._bigint(v) if k in self._VERSION_BIGINT_COLS else v)
                for k, v in data.items()
            }
            async with write_scope() as session:
                result = await session.execute(
                    insert(ResourceVersions)
                    .values(**coerced)
                    .returning(*ResourceVersions.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(
                f"Created version {coerced.get('version_number')} "
                f"for resource {coerced.get('resource_id')}"
            )
            return created
        except Exception as e:
            logger.error(f"Failed to create version: {e}")
            raise

    async def get_versions(self, resource_id: str) -> List[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.resource_id == self._bigint(resource_id))
                    .order_by(ResourceVersions.version_number.desc())
                )
                return [_version_row_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get versions for resource {resource_id}: {e}")
            return []

    async def get_version_by_id(self, version_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.id == self._bigint(version_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _version_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get version {version_id}: {e}")
            return None

    async def get_version_by_number(
        self, resource_id: str, version_number: int
    ) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(ResourceVersions)
                    .where(ResourceVersions.resource_id == self._bigint(resource_id))
                    .where(ResourceVersions.version_number == int(version_number))
                    .limit(1)
                )
                row = result.scalars().first()
                return _version_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Failed to get version {version_number} for {resource_id}: {e}"
            )
            return None

    async def delete_version(self, version_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(ResourceVersions).where(
                        ResourceVersions.id == self._bigint(version_id)
                    )
                )
            logger.info(f"Deleted version {version_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete version {version_id}: {e}")
            raise

    async def update_version(
        self, version_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(ResourceVersions)
                    .where(ResourceVersions.id == self._bigint(version_id))
                    .values(**data)
                    .returning(*ResourceVersions.__table__.columns)
                )
                row = result.mappings().first()
                return _mappings_dict(row) if row else {}
        except Exception as e:
            logger.error(f"Failed to update version {version_id}: {e}")
            raise

    async def get_untranscoded_video_versions(
        self, limit: int = UNTRANSCODED_BATCH
    ) -> List[Dict[str, Any]]:
        """Video versions never transcoded — NULL ``transcode_status`` AND
        non-NULL ``file_path``. Projection matches legacy.

        Bounded + ordered to match the REST twin: at most ``limit`` rows by id.
        The caller marks each ``pending`` so it leaves this set, making the
        batch re-runnable to drain past one call.
        """
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(
                        ResourceVersions.id,
                        ResourceVersions.resource_id,
                        ResourceVersions.mime_type,
                        ResourceVersions.file_path,
                    )
                    .where(ResourceVersions.mime_type.like("video/%"))
                    .where(ResourceVersions.transcode_status.is_(None))
                    .where(ResourceVersions.file_path.isnot(None))
                    .order_by(ResourceVersions.id.asc())
                    .limit(limit)
                )
                return [_rest_parity(dict(r)) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get untranscoded video versions: {e}")
            return []

    async def get_next_version_number(self, resource_id: str) -> int:
        """Next monotonic version_number for a resource. Returns 1 when the
        resource has no versions yet."""
        try:
            async with read_scope() as session:
                current = await session.scalar(
                    select(
                        func.coalesce(func.max(ResourceVersions.version_number), 0)
                    ).where(ResourceVersions.resource_id == self._bigint(resource_id))
                )
            return int(current or 0) + 1
        except Exception as e:
            logger.error(f"Failed to get next version for resource {resource_id}: {e}")
            return 1

    # ── Folders ─────────────────────────────────────────────────────

    async def create_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    insert(Folders).values(**data).returning(*Folders.__table__.columns)
                )
                row = result.mappings().first()
                created = _mappings_dict(row) if row else {}
            logger.info(f"Created folder: {data.get('name')}")
            return created
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def get_trashed_folders(
        self, scope_type: Optional[str], scope_id: str
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Folders)
                    .where(Folders.scope_id == self._bigint(scope_id))
                    .where(Folders.is_trashed.is_(True))
                    .order_by(Folders.trashed_at.desc())
                )
                return [_folder_row_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get trashed folders: {e}")
            return []

    async def get_folders(
        self,
        scope_type: Optional[str],
        scope_id: str,
        include_trashed: bool = False,
    ) -> List[Dict[str, Any]]:
        # PR-E Phase 1: scope_type accepted but unused (scope_id is unique).
        try:
            async with read_scope() as session:
                stmt = select(Folders).where(Folders.scope_id == self._bigint(scope_id))
                if not include_trashed:
                    stmt = stmt.where(Folders.is_trashed.is_(False))
                stmt = stmt.order_by(Folders.sort_order.asc())
                result = await session.execute(stmt)
                return [_folder_row_to_dict(r) for r in result.scalars().all()]
        except Exception as e:
            logger.error(f"Failed to get folders: {e}")
            return []

    async def get_folder_by_id(self, folder_id: str) -> Optional[Dict[str, Any]]:
        try:
            async with read_scope() as session:
                result = await session.execute(
                    select(Folders)
                    .where(Folders.id == self._bigint(folder_id))
                    .limit(1)
                )
                row = result.scalars().first()
                return _folder_row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get folder {folder_id}: {e}")
            return None

    async def update_folder(
        self, folder_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            async with write_scope() as session:
                result = await session.execute(
                    update(Folders)
                    .where(Folders.id == self._bigint(folder_id))
                    .values(**data)
                    .returning(*Folders.__table__.columns)
                )
                row = result.mappings().first()
                updated = _mappings_dict(row) if row else {}
            logger.info(f"Updated folder {folder_id}")
            return updated
        except Exception as e:
            logger.error(f"Failed to update folder {folder_id}: {e}")
            raise

    async def delete_folder(self, folder_id: str) -> bool:
        try:
            async with write_scope() as session:
                await session.execute(
                    sa_delete(Folders).where(Folders.id == self._bigint(folder_id))
                )
            logger.info(f"Deleted folder {folder_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete folder {folder_id}: {e}")
            raise

    async def get_descendant_folder_ids(self, folder_id: str) -> List[str]:
        """Recursive descent into non-trashed children. Single CTE.

        Returns list[str] for legacy contract (some unmigrated paths pass it
        back to PostgREST); internal callers like ``count_folder_contents``
        re-coerce via ``_bigint_list``."""
        try:
            async with read_scope() as session:
                result = await session.execute(
                    text(
                        "WITH RECURSIVE descendants AS ("
                        "  SELECT id FROM folders "
                        "    WHERE parent_id = :fid AND is_trashed = false "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN descendants d ON f.parent_id = d.id "
                        "    WHERE f.is_trashed = false"
                        ") SELECT id FROM descendants"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                return [str(r["id"]) for r in result.mappings().all()]
        except Exception as e:
            logger.error(f"Failed to get descendant folders for {folder_id}: {e}")
            return []

    async def count_folder_contents(self, folder_ids: List[str]) -> Dict[str, int]:
        """Resource + sub-folder count for a list of folder ids. Sub-folder
        count mirrors the legacy quirk: ``len(ids) - 1`` (the list is
        ``[root] + descendants``; the -1 strips the root)."""
        try:
            async with read_scope() as session:
                resource_count = await session.scalar(
                    text(
                        "SELECT count(*) FROM resource_items "
                        "WHERE folder_id = ANY(:fids)"
                    ),
                    {"fids": self._bigint_list(folder_ids)},
                )
            subfolder_count = len(folder_ids) - 1 if len(folder_ids) > 1 else 0
            return {
                "resource_count": int(resource_count or 0),
                "subfolder_count": subfolder_count,
            }
        except Exception as e:
            logger.error(f"Failed to count folder contents: {e}")
            return {"resource_count": 0, "subfolder_count": 0}

    async def restore_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Restore a folder + all trashed descendants + their resources, in
        ONE transaction (write_scope commits once). The multi-statement
        cascade is atomic — any raise rolls back all of it."""
        try:
            async with write_scope() as session:
                folder_ids_rows = await session.execute(
                    text(
                        "WITH RECURSIVE subtree AS ("
                        "  SELECT id FROM folders "
                        "    WHERE id = :fid AND is_trashed = true "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN subtree s ON f.parent_id = s.id "
                        "    WHERE f.is_trashed = true"
                        ") SELECT id FROM subtree"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                all_ids = [r["id"] for r in folder_ids_rows.mappings().all()]
                if not all_ids:
                    return {"restored_folders": 0, "restored_resources": 0}

                restored_resources_rows = await session.execute(
                    text(
                        "UPDATE resources SET is_trashed = false, "
                        "       trashed_at = NULL "
                        "WHERE is_trashed = true "
                        "  AND id IN ("
                        "    SELECT resource_id FROM resource_items "
                        "    WHERE folder_id = ANY(:ids)"
                        "  ) RETURNING id"
                    ),
                    {"ids": all_ids},
                )
                restored_resources = len(restored_resources_rows.mappings().all())

                restored_folders_rows = await session.execute(
                    text(
                        "UPDATE folders SET is_trashed = false, "
                        "       trashed_at = NULL "
                        "WHERE id = ANY(:ids) RETURNING id"
                    ),
                    {"ids": all_ids},
                )
                restored_folders = len(restored_folders_rows.mappings().all())

            logger.info(
                f"Cascade-restored folder {folder_id}: "
                f"{restored_folders} folders, "
                f"{restored_resources} resources"
            )
            return {
                "restored_folders": restored_folders,
                "restored_resources": restored_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-restore folder {folder_id}: {e}")
            raise

    async def trash_folder_cascade(self, folder_id: str) -> Dict[str, int]:
        """Trash a folder + all non-trashed descendants + their resources, in
        ONE transaction (write_scope commits once; atomic). The resource
        UPDATE snapshots the original location (last_folder_id / last_library_id
        / last_scope_id) per legacy contract — needed for restore.

        PR-E 4c dropped ``resource_items.scope_type``; the legacy supabase-py
        impl no longer reads/snapshots it (restore uses last_scope_id + folder/
        library). We match that — the retired asyncpg impl still referenced
        ``i.scope_type`` and would 42703 against the current schema."""
        try:
            async with write_scope() as session:
                folder_ids_rows = await session.execute(
                    text(
                        "WITH RECURSIVE subtree AS ("
                        "  SELECT id FROM folders WHERE id = :fid "
                        "  UNION ALL "
                        "  SELECT f.id FROM folders f "
                        "    INNER JOIN subtree s ON f.parent_id = s.id "
                        "    WHERE f.is_trashed = false"
                        ") SELECT id FROM subtree"
                    ),
                    {"fid": self._bigint(folder_id)},
                )
                all_ids = [r["id"] for r in folder_ids_rows.mappings().all()]
                if not all_ids:
                    return {"trashed_folders": 0, "trashed_resources": 0}

                trashed_resources_rows = await session.execute(
                    text(
                        "UPDATE resources r SET "
                        "  is_trashed = true, "
                        "  trashed_at = now(), "
                        "  last_folder_id = i.folder_id, "
                        "  last_library_id = i.library_id, "
                        "  last_scope_id = i.scope_id "
                        "FROM resource_items i "
                        "WHERE r.id = i.resource_id "
                        "  AND i.folder_id = ANY(:ids) "
                        "  AND r.is_trashed = false RETURNING r.id"
                    ),
                    {"ids": all_ids},
                )
                trashed_resources = len(trashed_resources_rows.mappings().all())

                trashed_folders_rows = await session.execute(
                    text(
                        "UPDATE folders SET "
                        "  is_trashed = true, trashed_at = now() "
                        "WHERE id = ANY(:ids) RETURNING id"
                    ),
                    {"ids": all_ids},
                )
                trashed_folders = len(trashed_folders_rows.mappings().all())

            logger.info(
                f"Cascade-trashed folder {folder_id}: "
                f"{trashed_folders} folders, "
                f"{trashed_resources} resources"
            )
            return {
                "trashed_folders": trashed_folders,
                "trashed_resources": trashed_resources,
            }
        except Exception as e:
            logger.error(f"Failed to cascade-trash folder {folder_id}: {e}")
            raise


__all__ = ["ResourcesRepositoryOrm"]
