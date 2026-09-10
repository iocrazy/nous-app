"""Data access for generated_media (Tier-1). Keyset list by (created_at, id) DESC.

ORM-model style (read_scope/write_scope + select on ``GeneratedMedia``),
converged from the raw db_engine call style. Return dict shapes are
byte-identical (native values; ``_normalize`` stringifies the snowflake /
uuid columns exactly as before).
"""

from __future__ import annotations

import base64
import datetime
import json
from contextlib import nullcontext
from typing import Iterable, Optional, Sequence

from loguru import logger
from sqlalchemy import Select
from sqlalchemy import Text as SAText
from sqlalchemy import case, cast
from sqlalchemy import delete as sa_delete
from sqlalchemy import func
from sqlalchemy import insert as sa_insert
from sqlalchemy import or_, select, tuple_
from sqlalchemy import update as sa_update

from app.db.scope import is_enforced, system_request_scope
from app.db.session import read_scope, write_scope
from app.models import (
    Canvases,
    GeneratedMedia,
    Resources,
    ResourceVersions,
    ScriptProjects,
    ScriptScenes,
    ScriptShots,
)
from app.services.library.generated_roles import INTERMEDIATE_ROLES, ROLE_KEY
from app.services.library.media_storage import ObjectStore, resolve_media_source

# How many rows one cleanup pass may look at. Defined HERE because this module
# is what enforces it in SQL, and imported by
# ``GeneratedInboxService.cleanup`` for both the call and its ``truncated``
# flag: if the caller's number and this cap ever drifted apart, ``truncated``
# would be computed against a page size the query cannot produce and would
# silently read False forever — "that was all of them" when it was not.
CLEANUP_SCAN_LIMIT = 2000

# The projection every read returns (matches the legacy _COLS order; the
# object-store-only content_sha256 stays internal, exactly as before).
_GM_COLS = (
    GeneratedMedia.id,
    GeneratedMedia.scope_id,
    GeneratedMedia.creator_id,
    GeneratedMedia.media_kind,
    GeneratedMedia.mime,
    GeneratedMedia.file_path,
    GeneratedMedia.file_size_bytes,
    GeneratedMedia.origin_kind,
    GeneratedMedia.origin_run_id,
    GeneratedMedia.agent_id,
    GeneratedMedia.canvas_id,
    GeneratedMedia.node_id,
    GeneratedMedia.prompt,
    GeneratedMedia.model,
    GeneratedMedia.provider,
    GeneratedMedia.params,
    GeneratedMedia.cost_cents,
    GeneratedMedia.parent_resource_id,
    GeneratedMedia.derivation_kind,
    GeneratedMedia.promoted_resource_id,
    GeneratedMedia.review_state,
    GeneratedMedia.source_asset_id,
    GeneratedMedia.conversation_id,
    GeneratedMedia.created_at,
)

# Snowflake BIGINT columns: must be str() before reaching the frontend to avoid
# JS precision loss (same guard used throughout resources_repository.py).
_BIGINT_COLS = (
    "id",
    "scope_id",
    "canvas_id",
    "parent_resource_id",
    "promoted_resource_id",
    "source_asset_id",
    "conversation_id",
)
_UUID_COLS = ("creator_id", "agent_id")


def _normalize(row: Optional[dict]) -> Optional[dict]:
    """Stringify bigint and UUID fields so JSON serialisation is lossless."""
    if not row:
        return row
    out = dict(row)
    for c in _BIGINT_COLS:
        if out.get(c) is not None:
            out[c] = str(out[c])
    for c in _UUID_COLS:
        if out.get(c) is not None:
            out[c] = str(out[c])
    return out


def _encode_cursor(created_at: str, gen_id: int) -> str:
    return base64.urlsafe_b64encode(json.dumps([created_at, gen_id]).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> Optional[tuple[str, int]]:
    if not cursor:
        return None
    try:
        ts, gid = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return str(ts), int(gid)
    except Exception:  # noqa: BLE001
        return None


def _cursor_ts(ts: str) -> datetime.datetime:
    """Cursor timestamp string → datetime for the asyncpg-strict bind.

    The cursor carries ``str(created_at)`` — ``fromisoformat`` accepts both
    the space-separated ``str(datetime)`` form and the T-separated ISO form.
    """
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


# origin_kind values that represent a shot's rendered output (image or
# video) — the only generated_media rows addressable from a project via
# node_id = str(shot_id).
_SHOT_ORIGIN_KINDS = ("shot_generate", "shot_video")


def _promote_review_state():
    """The review_state expression ``mark_promoted`` writes.

    Promotion may only advance ``unreviewed`` -> ``saved``. Every other state
    re-reads the column, so ``in_assets`` (and ``deleted``) can never be
    downgraded by a promote. Factored out and kept pure so the transition is
    asserted on the compiled SQL -- a flat ``'saved'`` literal here has to
    fail a test, not merely be caught in review.
    """
    return case(
        (GeneratedMedia.review_state == "unreviewed", "saved"),
        else_=GeneratedMedia.review_state,
    )


def _object_refcount_stmt(file_path: str):
    """How many rows across ALL THREE tables still point at ``file_path``.

    Content-addressed keys (``t{scope}/{sha}/…``) are SHARED by dedup, and
    since P1 a ``generated_media`` row's ``file_path`` can be verbatim a live
    ``resources.file_path``: ``insert_registered_resource`` stores the
    resource's own path, and ``promote`` re-derives the same key from the same
    sha when target scope == source scope. Counting only sibling
    ``generated_media`` rows therefore reads 0 while My Uploads, a
    ``resource_versions`` row and any ``asset_files`` attachment are still
    pointing at those exact bytes — deleting an inbox card would 404 the
    user's file with no error anywhere.

    ``asset_files`` is NOT counted: it keys on ``resource_id``, so a live
    attachment is already covered by the ``resources`` row it points at.
    """
    return select(
        (
            select(func.count())
            .select_from(GeneratedMedia)
            .where(GeneratedMedia.file_path == file_path)
            .scalar_subquery()
        )
        + (
            select(func.count())
            .select_from(Resources)
            .where(Resources.file_path == file_path)
            .scalar_subquery()
        )
        + (
            select(func.count())
            .select_from(ResourceVersions)
            .where(ResourceVersions.file_path == file_path)
            .scalar_subquery()
        )
    )


# Advisory-lock namespace for "register THIS resource into the inbox". The
# prefix is what keeps ``hashtextextended`` from colliding with another
# module's per-id locks (script_repository's ``script_provision:``,
# project_stage_nodes_repository's ``project_stage_nodes_instantiate:``) — the
# same namespacing idiom, expressed in ORM rather than ``text()``.
_REGISTRATION_LOCK_NAMESPACE = "generated_media_registration:"


def _registration_lock_stmt(resource_id: int):
    """``pg_advisory_xact_lock`` over the registration of ONE resource.

    ``hashtextextended(text, int8) -> int8`` turns the namespaced id into the
    64-bit key the advisory-lock primitive takes. TRANSACTION-level: it is
    released by the caller's COMMIT/ROLLBACK, never held past it, so a caller
    that crashes cannot wedge the resource.

    Pure/sync so the compiled SQL can be asserted without a database — the
    same reason its sibling ``_stmt`` helpers in this module are.
    """
    return select(
        func.pg_advisory_xact_lock(
            func.hashtextextended(
                _REGISTRATION_LOCK_NAMESPACE + str(int(resource_id)), 0
            )
        )
    )


def _registered_resource_lookup_stmt(resource_id: int):
    """The idempotency SELECT for ``insert_registered_resource``.

    Keyed on ``promoted_resource_id`` alone: a resource has at most one
    inbox row, whether this backfill made it or a promote did. ``order_by``
    is not decoration — a legacy promote may already have left a row, and
    ``LIMIT 1`` without an order is a coin flip between them.
    """
    return (
        select(*_GM_COLS)
        .where(GeneratedMedia.promoted_resource_id == int(resource_id))
        .order_by(GeneratedMedia.id.asc())
        .limit(1)
    )


def _registered_resource_insert_stmt(**values):
    """The INSERT...RETURNING for a row that registers an EXISTING resource.

    Column-level RETURNING (never entity-level — see
    ``generated_media_service._generated_media_insert_stmt``): entity-level
    returning maps a row to one entity-named key instead of one key per
    column.
    """
    return sa_insert(GeneratedMedia).values(**values).returning(*_GM_COLS)


def _visible_role_criterion():
    """Rows whose ``params.role`` is not one of the hidden intermediates.

    ``NOT IN`` alone is WRONG here and the bug would be invisible: a row with
    no ``role`` yields SQL NULL from ``params->>'role'``, ``NULL NOT IN (...)``
    is NULL, and the WHERE clause drops it. Every generation written before
    roles existed would vanish from the inbox — an empty page that reads
    exactly like "nothing generated yet". The explicit ``IS NULL`` arm is what
    makes "unclassified" mean VISIBLE (see ``generated_roles``).
    """
    role = GeneratedMedia.params[ROLE_KEY].astext
    return or_(role.is_(None), role.notin_(list(INTERMEDIATE_ROLES)))


def _inbox_filters(
    *,
    scope_id: int,
    state: Optional[str],
    origin_kinds: Optional[list[str]],
    project_id: Optional[int],
    canvas_id: Optional[int],
    media_kind: Optional[str],
    model: Optional[str],
    since: Optional[datetime.datetime],
    source_asset_id: Optional[int],
    include_intermediate: bool,
) -> list:
    """Criteria for the Generated inbox list. Pure so tests can compile them.

    state=None -> every state except 'deleted'. project_id is resolved through
    canvases.project_id; rows without a canvas_id (chat uploads, agent runs)
    never match a project filter -- the inbox says so in its empty state.
    canvas_id narrows to ONE canvas and needs no such resolution: it is the
    column itself.

    source_asset_id is the asset a run was launched FROM (stamped by
    ``generate_slot``). Every parameter here is required with no default on
    purpose: a filter that can be forgotten silently widens the page to the
    whole scope, which reads exactly like a correct answer.

    include_intermediate=False (what the inbox asks for) drops the masks,
    brush composites and transcoded references that ride under
    ``origin_kind='canvas_upload'`` — see ``generated_roles``.
    """
    crit = [GeneratedMedia.scope_id == int(scope_id)]
    if state:
        crit.append(GeneratedMedia.review_state == state)
    else:
        crit.append(GeneratedMedia.review_state != "deleted")
    if origin_kinds:
        crit.append(GeneratedMedia.origin_kind.in_(list(origin_kinds)))
    if project_id is not None:
        crit.append(
            GeneratedMedia.canvas_id.in_(
                select(Canvases.id).where(Canvases.project_id == int(project_id))
            )
        )
    if canvas_id is not None:
        # A DIRECT column predicate, unlike ``project_id`` above, which has to
        # resolve through ``canvases.project_id``. "This canvas" is a question
        # the row can answer by itself, and routing it through the subquery
        # would answer the whole project's inbox under one canvas's name.
        crit.append(GeneratedMedia.canvas_id == int(canvas_id))
    if media_kind:
        crit.append(GeneratedMedia.media_kind == media_kind)
    if model:
        crit.append(GeneratedMedia.model == model)
    if since is not None:
        crit.append(GeneratedMedia.created_at >= since)
    if source_asset_id is not None:
        crit.append(GeneratedMedia.source_asset_id == int(source_asset_id))
    if not include_intermediate:
        crit.append(_visible_role_criterion())
    return crit


def _promoted_resource_ids_stmt(gen_ids: Sequence[int]) -> Select:
    """``(generation id, promoted resource id)`` for the archived ones whose
    resource still exists.

    ``promoted_resource_id`` has no FK and is not cleared when a resource is
    purged, while ``canvas_resource_refs.resource_id`` REFERENCES resources:
    one dangling id would fail the refs INSERT after its DELETE committed and
    wipe every ref the canvas has. The INNER JOIN keeps only live targets
    (a trashed-but-not-purged row still exists, so it still counts).
    """
    return (
        select(GeneratedMedia.id, GeneratedMedia.promoted_resource_id)
        .join(Resources, Resources.id == GeneratedMedia.promoted_resource_id)
        .where(
            GeneratedMedia.id.in_(list(gen_ids)),
            GeneratedMedia.promoted_resource_id.is_not(None),
        )
    )


class GeneratedMediaRepository:
    async def list_for_project(
        self,
        project_id: str | int,
        *,
        episode_id: Optional[str | int] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict:
        """Renders for a project (optionally narrowed to one episode).

        generated_media has no direct project/episode column — the join
        chain is generated_media.node_id (= str(shot_id) for shot-origin
        rows) -> script_shots.scene_id -> script_scenes.script_id ->
        script_projects.project_id/episode_id. Same cursor-pagination shape
        as ``list_for_scope`` (keyset on (created_at, id) DESC, exclusive
        upper bound via a peek-ahead row).
        """
        limit = max(1, min(int(limit), 100))
        stmt = (
            select(*_GM_COLS)
            .select_from(GeneratedMedia)
            .join(ScriptShots, GeneratedMedia.node_id == cast(ScriptShots.id, SAText))
            .join(ScriptScenes, ScriptScenes.id == ScriptShots.scene_id)
            .join(ScriptProjects, ScriptProjects.id == ScriptScenes.script_id)
            .where(
                ScriptProjects.project_id == int(project_id),
                ScriptProjects.status != "deleted",
                GeneratedMedia.origin_kind.in_(_SHOT_ORIGIN_KINDS),
            )
        )
        if episode_id is not None:
            stmt = stmt.where(ScriptProjects.episode_id == int(episode_id))
        decoded = _decode_cursor(cursor)
        if decoded:
            c_ts, c_id = decoded
            stmt = stmt.where(
                tuple_(GeneratedMedia.created_at, GeneratedMedia.id)
                < tuple_(_cursor_ts(c_ts), c_id)
            )
        stmt = stmt.order_by(
            GeneratedMedia.created_at.desc(), GeneratedMedia.id.desc()
        ).limit(limit + 1)
        async with read_scope() as session:
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(str(last["created_at"]), int(last["id"]))
            rows = rows[:limit]
        return {"items": [_normalize(r) for r in rows], "next_cursor": next_cursor}

    async def list_for_scope(
        self,
        scope_id: int,
        *,
        kind: Optional[str] = None,
        entity_kind: Optional[str] = None,
        entity_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 30,
        cover_source: Optional[str] = None,
    ) -> dict:
        limit = max(1, min(int(limit), 100))
        stmt = select(*_GM_COLS).where(GeneratedMedia.scope_id == scope_id)
        # Cover Studio rounds: stamped by /distribution/covers/generate as
        # params.cover.source_video_id — the studio's history per video.
        if cover_source:
            stmt = stmt.where(
                GeneratedMedia.params["cover"]["source_video_id"].astext == cover_source
            )
        if kind:
            stmt = stmt.where(GeneratedMedia.media_kind == kind)
        # CC5 asset backlink: generations dispatched from an entity branch
        # carry entity_kind/entity_id in params (stamped by the frontend at
        # dispatch); the library asset strips filter on them (mig 360 index).
        if entity_kind:
            stmt = stmt.where(
                GeneratedMedia.params["entity_kind"].astext == entity_kind
            )
        if entity_id:
            stmt = stmt.where(GeneratedMedia.params["entity_id"].astext == entity_id)
        decoded = _decode_cursor(cursor)
        if decoded:
            c_ts, c_id = decoded
            stmt = stmt.where(
                tuple_(GeneratedMedia.created_at, GeneratedMedia.id)
                < tuple_(_cursor_ts(c_ts), c_id)
            )
        stmt = stmt.order_by(
            GeneratedMedia.created_at.desc(), GeneratedMedia.id.desc()
        ).limit(limit + 1)
        async with read_scope() as session:
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(str(last["created_at"]), int(last["id"]))
            rows = rows[:limit]
        return {"items": [_normalize(r) for r in rows], "next_cursor": next_cursor}

    async def get(self, gen_id: int, scope_id: int) -> Optional[dict]:
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_GM_COLS).where(
                            GeneratedMedia.id == gen_id,
                            GeneratedMedia.scope_id == scope_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def get_by_id(self, gen_id: int) -> Optional[dict]:
        """Fetch a row by id without a scope filter (for public-serve endpoints)."""
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(*_GM_COLS).where(GeneratedMedia.id == gen_id)
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def promoted_resource_ids(
        self, gen_ids: Iterable[int | str]
    ) -> dict[int, int]:
        """``{generation id: promoted resource id}`` for the archived ones.

        Unscoped like ``get_by_id``: the ids come from one canvas's own
        nodes_json and the answer only feeds that canvas's refs mirror.
        ``Resources`` is UserScoped and joined here, so under an ambient user
        Scope the join would silently drop another contributor's archived
        output — same rationale as
        ``canvas_refs_repository.list_assets_for_canvas``.
        """
        ids = sorted({int(g) for g in gen_ids})
        if not ids:
            return {}
        scope_cm = (
            system_request_scope(
                reason="canvas refs archived-output lookup: canvas membership "
                "is the authorization boundary, not creator_id — a canvas can "
                "show generations archived by multiple contributors"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            async with read_scope() as session:
                rows = (await session.execute(_promoted_resource_ids_stmt(ids))).all()
        return {int(gen_id): int(resource_id) for gen_id, resource_id in rows}

    async def delete(self, gen_id: int, scope_id: int) -> bool:
        # Capture the location BEFORE deleting so we can clean up an orphaned
        # object-store object afterwards (filesystem cleanup is pre-existing
        # behavior — left as-is; each fs row has a unique uuid path anyway).
        async with read_scope() as session:
            row = (
                (
                    await session.execute(
                        select(
                            GeneratedMedia.file_path,
                            GeneratedMedia.promoted_resource_id,
                        ).where(
                            GeneratedMedia.id == gen_id,
                            GeneratedMedia.scope_id == scope_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        async with write_scope() as session:
            result = await session.execute(
                sa_delete(GeneratedMedia).where(
                    GeneratedMedia.id == gen_id,
                    GeneratedMedia.scope_id == scope_id,
                )
            )
            n = result.rowcount
        if n and row:
            await self._maybe_remove_object(dict(row))
        return bool(n)

    async def _maybe_remove_object(self, row: dict) -> None:
        """Remove the backing object IFF nothing else still references it.

        Two guards, in order:

        1. **A promoted row never removes anything.** ``promoted_resource_id``
           means those bytes were handed to Tier-2; the ``resources`` row owns
           them now and the inbox card is only a pointer. Deleting the card is
           "stop showing me this", never "delete my file".
        2. **The refcount is cross-table** (``_object_refcount_stmt``): a
           content-addressed key is shared by dedup, and since P1 it can be
           shared with ``resources`` / ``resource_versions``, not just with a
           sibling generation.

        A removal failure is swallowed (a leaked object is acceptable; failing
        the delete is not).
        """
        if row.get("promoted_resource_id"):
            return  # the resource owns these bytes now
        file_path = row.get("file_path") or ""
        if not file_path:
            return
        loc = resolve_media_source(file_path)
        if not loc.is_object_store:
            return
        # The refcount must see EVERY owner, not just the caller's own rows:
        # ``Resources`` is UserScoped, so under an ambient user Scope the
        # count would silently drop another user's live row and read 0.
        # Same rationale as ``canvas_refs_repository.list_assets_for_canvas``.
        scope_cm = (
            system_request_scope(
                reason="generated-media object refcount: ownership of the "
                "bytes is global — a resources row belonging to ANOTHER "
                "creator still keeps the object alive"
            )
            if is_enforced("resources")
            else nullcontext()
        )
        async with scope_cm:
            async with read_scope() as session:
                remaining = (
                    await session.execute(_object_refcount_stmt(file_path))
                ).scalar()
        if remaining and int(remaining) > 0:
            return  # still referenced — keep the object
        try:
            await ObjectStore(loc.bucket).remove(loc.key)
        except Exception as exc:
            logger.warning(
                f"[generated_media] object cleanup failed (leak, non-fatal): "
                f"key={loc.key} error={exc!r}"
            )

    async def mark_promoted(self, gen_id: int, resource_id: int) -> Optional[dict]:
        """Set promoted_resource_id (Tier-1 → Tier-2 link). Returns the row."""
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        sa_update(GeneratedMedia)
                        .where(GeneratedMedia.id == gen_id)
                        .values(
                            promoted_resource_id=resource_id,
                            review_state=_promote_review_state(),
                        )
                        .returning(*_GM_COLS)
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def lock_resource_registration(self, resource_id: int) -> None:
        """Serialise inbox registration of ONE resource for this transaction.

        MUST be called inside an ambient ``unit_of_work()`` — ``write_scope()``
        joins it, and a transaction-level advisory lock taken in its own
        auto-committing transaction is released immediately and guards
        nothing. See :func:`_registration_lock_stmt` and the concurrency note
        on :meth:`insert_registered_resource`.
        """
        async with write_scope() as session:
            await session.execute(_registration_lock_stmt(int(resource_id)))

    async def insert_registered_resource(
        self,
        *,
        scope_id: int,
        creator_id: str,
        resource_id: int,
        file_path: str,
        mime: Optional[str],
        media_kind: str,
        conversation_id: Optional[int],
        origin_kind: str = "chat_upload",
        params: Optional[dict] = None,
    ) -> dict:
        """Register an EXISTING resource into the Generated inbox. Returns the row.

        No blob is copied: ``file_path`` is the resource's own stored path and
        ``promoted_resource_id`` points at the resource, so the row is born
        ``saved`` (Tier-2 already holds the bytes) rather than ``unreviewed``.

        Idempotent by ``promoted_resource_id``: a resource that already has an
        inbox row gets that row back instead of a duplicate. The check is a
        SELECT, and a SELECT alone cannot stop two concurrent callers for the
        same resource from each finding nothing and both inserting — under
        READ COMMITTED neither sees the other's uncommitted row.

        ⚠️ **Whether that is acceptable is a property of the CALLER, not of
        this method.** Three writers today, and they do not share a risk
        profile:

        * ``services/library/chat_upload`` — once per brand-new resource, at
          the moment it is created. Nothing else knows the id yet.
        * ``workflows/backfill_generated_inbox`` — a single admin-dispatched
          reconciliation, re-runnable, not concurrent with itself.
        * ``services/library/generated_inbox_service.save_resource_as_asset``
          — **user-triggered, repeatable and concurrently reachable** (a
          double-submitted dialog, a client retry). Two such calls could each
          SELECT nothing and both INSERT under READ COMMITTED, and since the
          lookup is ``id asc limit 1`` the second row would become a permanent
          orphan ``saved`` card. That caller therefore takes
          :func:`_registration_lock_stmt` (via
          :meth:`lock_resource_registration`) inside its transaction BEFORE
          calling this method; the loser blocks until the winner commits and
          then finds the winner's row here.

        Since migration 456 the database also carries a partial unique index,
        ``uq_genmedia_promoted_resource``, so a duplicate row is now
        impossible even if a future caller forgets the lock. The lock is kept
        anyway, because the two guards do different jobs: the index turns a
        lost race into an ``IntegrityError``, while the lock turns it into
        "loser finds the winner's row" — and finding the row is the behaviour
        the Save-as-Asset dialog wants from a double submit.

        ``params`` is the row's internal metadata (it never reaches the wire —
        ``GeneratedItem`` drops it). ``save_resource_as_asset`` uses it to carry
        the name the card should be titled after; the other two callers pass
        nothing and get ``{}``, exactly as before.
        """
        async with read_scope() as session:
            existing = (
                (await session.execute(_registered_resource_lookup_stmt(resource_id)))
                .mappings()
                .first()
            )
        if existing:
            return _normalize(dict(existing))  # type: ignore[return-value]

        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        _registered_resource_insert_stmt(
                            scope_id=int(scope_id),
                            creator_id=str(creator_id),
                            media_kind=media_kind,
                            mime=mime,
                            file_path=file_path,
                            origin_kind=origin_kind,
                            conversation_id=(
                                int(conversation_id)
                                if conversation_id is not None
                                else None
                            ),
                            promoted_resource_id=int(resource_id),
                            review_state="saved",
                            params=dict(params or {}),
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:  # an INSERT...RETURNING that returns nothing
            raise RuntimeError(
                f"[generated_media] registration insert returned no row for "
                f"resource {resource_id}"
            )
        return _normalize(dict(row))  # type: ignore[return-value]

    async def list_inbox(
        self,
        scope_id: int,
        *,
        state: Optional[str] = None,
        origin_kinds: Optional[list[str]] = None,
        project_id: Optional[int] = None,
        canvas_id: Optional[int] = None,
        media_kind: Optional[str] = None,
        model: Optional[str] = None,
        since: Optional[datetime.datetime] = None,
        source_asset_id: Optional[int] = None,
        include_intermediate: bool = False,
        cursor: Optional[str] = None,
        limit: int = 60,
    ) -> dict:
        """Generated inbox page (keyset on (created_at, id) DESC).

        Same cursor shape as ``list_for_scope``; filters come from the pure
        ``_inbox_filters`` so they can be asserted without a database.
        """
        limit = max(1, min(int(limit), 200))
        stmt = select(*_GM_COLS).where(
            *_inbox_filters(
                scope_id=scope_id,
                state=state,
                origin_kinds=origin_kinds,
                project_id=project_id,
                canvas_id=canvas_id,
                media_kind=media_kind,
                model=model,
                since=since,
                source_asset_id=source_asset_id,
                include_intermediate=include_intermediate,
            )
        )
        decoded = _decode_cursor(cursor)
        if decoded:
            c_ts, c_id = decoded
            stmt = stmt.where(
                tuple_(GeneratedMedia.created_at, GeneratedMedia.id)
                < tuple_(_cursor_ts(c_ts), c_id)
            )
        stmt = stmt.order_by(
            GeneratedMedia.created_at.desc(), GeneratedMedia.id.desc()
        ).limit(limit + 1)
        async with read_scope() as session:
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(str(last["created_at"]), int(last["id"]))
            rows = rows[:limit]
        return {"items": [_normalize(r) for r in rows], "next_cursor": next_cursor}

    async def set_review_state(
        self, gen_id: int, scope_id: int, state: str
    ) -> Optional[dict]:
        """Move one row to ``state``. Returns None when nothing matched."""
        async with write_scope() as session:
            row = (
                (
                    await session.execute(
                        sa_update(GeneratedMedia)
                        .where(GeneratedMedia.id == int(gen_id))
                        .where(GeneratedMedia.scope_id == int(scope_id))
                        .values(review_state=state)
                        .returning(*_GM_COLS)
                    )
                )
                .mappings()
                .first()
            )
        return _normalize(dict(row)) if row else None

    async def set_source_asset(
        self, gen_id: int, asset_id: int, *, scope_id: Optional[int] = None
    ) -> Optional[dict]:
        """Stamp which asset a generation was made FOR. None when nothing matched.

        ``scope_id`` is optional but every caller should pass it: without a
        scope predicate this is an UPDATE by primary key, and a future caller
        handed a user-supplied ``gen_id`` could stamp a row in someone else's
        scope. The default stays unscoped so the method's behaviour is decided
        at the call site rather than by whether the argument was remembered —
        and ``None`` (nothing matched) is returned in either case, so a caller
        that passes the wrong scope gets a typed failure rather than silence.
        """
        stmt = sa_update(GeneratedMedia).where(GeneratedMedia.id == int(gen_id))
        if scope_id is not None:
            stmt = stmt.where(GeneratedMedia.scope_id == int(scope_id))
        stmt = stmt.values(source_asset_id=int(asset_id)).returning(*_GM_COLS)
        async with write_scope() as session:
            row = (await session.execute(stmt)).mappings().first()
        return _normalize(dict(row)) if row else None

    async def count_by_state(
        self, scope_id: int, *, include_intermediate: bool = False
    ) -> dict[str, int]:
        """Per-state counts. Always returns all four keys (0 when absent).

        Counts what the inbox LISTS. The unreviewed number is rendered as a
        badge on the sidebar, so a count that included the hidden
        intermediates would send the user to a page that cannot show them the
        rows it just promised — the same "N to review" over an empty list the
        role split exists to end.
        """
        stmt = select(GeneratedMedia.review_state, func.count()).where(
            GeneratedMedia.scope_id == int(scope_id)
        )
        if not include_intermediate:
            stmt = stmt.where(_visible_role_criterion())
        stmt = stmt.group_by(GeneratedMedia.review_state)
        out = {"unreviewed": 0, "saved": 0, "in_assets": 0, "deleted": 0}
        async with read_scope() as session:
            for state, n in (await session.execute(stmt)).all():
                out[state] = int(n)
        return out

    async def list_older_unreviewed(
        self, scope_id: int, older_than: datetime.datetime, limit: int = 500
    ) -> list[dict]:
        """Oldest-first unreviewed rows created before ``older_than``."""
        stmt = (
            select(*_GM_COLS)
            .where(GeneratedMedia.scope_id == int(scope_id))
            .where(GeneratedMedia.review_state == "unreviewed")
            .where(GeneratedMedia.created_at < older_than)
            .order_by(GeneratedMedia.created_at.asc())
            .limit(max(1, min(int(limit), CLEANUP_SCAN_LIMIT)))
        )
        async with read_scope() as session:
            rows = [dict(m) for m in (await session.execute(stmt)).mappings().all()]
        return [_normalize(r) for r in rows]
