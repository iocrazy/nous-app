"""storage_migration DBOS workflow — legacy filesystem → Supabase Storage.

Task 4.1 of the storage-unification epic (PR-4). Moves existing files that
still live on the local filesystem into the ``library`` object-store bucket,
row by row, for one module at a time (``uploads`` / ``project_files`` /
``downloads``).

Design
------
Row-wise idempotent, NOT step-checkpointed. ``_migrate_row`` skips any row
already pointing at ``sb://`` (via ``resolve_media_source`` — never a raw
``startswith`` string check, so a malformed/legacy scheme value degrades
safely instead of silently re-migrating). ``store_local_file`` is itself
content-addressed and dedup-safe (skip-PUT when the object already exists),
so replaying the same batch (same or a fresh ``workflow_id``) after a crash
just re-skips already-migrated rows and resumes on the rest — no
``@DBOS.step`` checkpointing is needed for that property. Nothing in this
module ever calls ``DBOS.start_workflow`` (CLAUDE.md 路线 C / established
lesson: never dispatch another workflow from inside retryable I/O).

THE safety contract (non-negotiable ordering) — see ``_migrate_row``:
  1. verify the object really landed in storage AND is the right size —
     BEFORE any DB mutation.
  2. ``dry_run`` stops right there (PUT may have happened — dedup-safe — but
     zero DB UPDATE, zero delete).
  3. DB UPDATE (the row now points at ``sb://``) — BEFORE the local file is
     touched.
  4. local file deleted ONLY if ``delete_source`` and the UPDATE above
     already committed.
A raised exception at any point before step 3 leaves the row completely
unmutated — the caller's per-row try/except counts it "failed" and the
batch continues; only the final failed>0 check aborts the workflow (raise,
never a returned failed dict — CLAUDE.md 路线 C rule 4).

``downloads`` (source_type='web') rows can point at a DIRECTORY instead of a
file — an album (``douyin/{pm_id}/`` full of ``{aweme}_0.jpg`` + cover.jpg).
``_migrate_album_row`` follows the same 4-step ordering, but swaps the
per-object sha256/size check for a file-COUNT check (local rglob vs. what
``put_dir`` left under the prefix) since a directory has no single content
hash to verify against. The row's ``file_path`` becomes the PREFIX itself
(trailing slash — ``MediaLocation.is_prefix``), not a path to one object.

``hls`` (``resource_versions.hls_path``) is also a directory migration, but
unlike ``downloads`` it does not call ``library_store().put_dir`` directly —
it delegates to ``HlsPublisher.publish``, which already encodes the one
non-negotiable HLS invariant (master.m3u8 uploaded LAST, after every
segment/tier playlist has landed — see hls_publisher.py docstring). Re-doing
that upload ordering here instead of reusing it would risk exposing a
half-written master to a player. ``_migrate_hls_row`` still keeps the same
4-step safety ordering, verifying via a post-publish file-count check
(local rglob vs. ``list_prefix`` under the version's hls prefix) same as
the album path.

Module registry
----------------
Each module supplies: a SELECT that coarse-filters ``file_path NOT LIKE
'sb://%'`` (row-level resolve_media_source is still the real authority —
the SQL filter only keeps a huge legacy table from being scanned row by
row for nothing), a pure ``extract`` (row → scope/mime/filename), and an
``update_row`` that persists the new ``sb://`` path (+ syncs the parent
row's ``file_path`` when the migrated version is the current one).

``storyboard`` is intentionally NOT registered — the module has been
410-dead since migration 348 (``storyboard_assets`` / ``video_assets``
renamed to ``zzz_deprecated_*``). There is nothing left under those names
to migrate; wiring it up would just be dead SQL against renamed tables.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from dbos import DBOS
from loguru import logger
from sqlalchemy import and_, or_, select, true, update

from app.core.config import settings
from app.db.scope import is_enforced, system_request_scope
from app.db.session import read_scope, write_scope
from app.models import (
    FileVersions,
    ParsedMedia,
    ProjectFiles,
    ResourceItems,
    Resources,
    ResourceVersions,
)
from app.services.library import media_storage
from app.services.media.transcode.hls_publisher import HlsPublisher

# System-initiated batch job — no single owning end user. Matches the
# existing SYSTEM_RUN_USER_ID convention (app/api/admin/ai_usage_router.py,
# app/services/topics/topic_scorer.py). task_tracking.user_id is UUID NOT
# NULL, so this can't be blank.
SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

# Rows between task_tracking progress writes (update_progress is itself
# throttled to 1 write/sec, this just avoids computing % on every row).
_PROGRESS_EVERY = 20


# ── Row extraction value object ─────────────────────────────────────────


@dataclass(frozen=True)
class RowExtract:
    """What ``_migrate_row`` needs to content-address + name one file."""

    scope_id: int
    mime: str
    filename: Optional[str]
    # Album directory (downloads module only) — ``mime``/``filename`` are
    # meaningless in this case (multiple files, each with its own) and go
    # unused; default False keeps every existing extract() call unaffected.
    is_album: bool = False
    # hls module only — rid/vid parsed from hls_path, needed to call
    # HlsPublisher.publish and to compute the verify-prefix. None default
    # keeps every other module's extract() call unaffected.
    hls_rid: Optional[str] = None
    hls_vid: Optional[str] = None


@dataclass(frozen=True)
class ModuleConfig:
    """Per-module SELECT + row semantics for the legacy → sb:// migration.

    ``list_rows`` is the escape hatch for ``derived`` (see that module's
    comment below): its "rows" are a UNION of a DB SELECT (thumbnail_path/
    cover_image_path — real columns) and a filesystem walk
    (``preview_sprite.jpg`` has no DB column anywhere, so there is nothing to
    SELECT it FROM) — no single ``select_stmt`` builder can express that, so
    ``list_rows`` owns the whole thing instead. When ``list_rows`` is set,
    ``storage_migration_workflow`` calls it instead of executing
    ``select_stmt(scope_id, limit)`` to produce the batch; ``extract``/
    ``update_row`` are unused in that case (the module's own migrate function
    handles both directly) and are given no-op/raising placeholders.

    Phase C task 2: ``select_sql`` (a raw SQL string) is now ``select_stmt`` —
    a ``(scope_id, limit) -> Select`` builder returning a real ORM statement,
    executed via ``read_scope()`` inside the workflow's ambient
    ``system_request_scope`` (admin-initiated migration, cross-tenant by
    design).
    """

    name: str
    select_stmt: Callable[[Optional[int], int], Any]
    extract: Callable[[dict], RowExtract]
    update_row: Callable[[dict, str, Optional[str]], Awaitable[None]]
    list_rows: Optional[Callable[[Optional[int], int], Awaitable[list[dict]]]] = None


# ── uploads: resource_versions (+ resources.file_path sync) ────────────
#
# Each resource_versions row is one physical file. The scope used for
# content-addressing is the resource's resource_items.scope_id (there is
# no scope column on resources/resource_versions themselves — this mirrors
# how the live upload path resolves it, see resources_service.py
# upload_new_version). LATERAL + LIMIT 1 picks one deterministically if a
# resource has more than one resource_items row (folder/library
# membership) — the scope only affects the storage key prefix, not
# correctness.


def _uploads_select_stmt(scope_id: Optional[int], limit: int):
    """ORM port of the former ``_UPLOADS_SELECT_SQL`` string. ``scope_id``
    is a plain Python filter (applied only when given) rather than the
    ``CAST(:scope_id AS bigint) IS NULL OR ...`` SQL idiom — this repo's
    established convention (object_gc.py et al.) for an optional filter
    resolved once per call, not per-row."""
    ri = (
        select(ResourceItems.scope_id)
        .where(ResourceItems.resource_id == Resources.id)
        .order_by(ResourceItems.id)
        .limit(1)
        .lateral("ri")
    )
    stmt = (
        select(
            ResourceVersions.id,
            ResourceVersions.resource_id,
            ResourceVersions.version_number,
            ResourceVersions.file_path,
            ResourceVersions.filename,
            ResourceVersions.mime_type,
            Resources.current_version,
            Resources.filename.label("resource_filename"),
            Resources.mime_type.label("resource_mime_type"),
            ri.c.scope_id,
        )
        .select_from(ResourceVersions)
        .join(Resources, Resources.id == ResourceVersions.resource_id)
        .join(ri, true(), isouter=True)
        .where(ResourceVersions.file_path.isnot(None))
        .where(ResourceVersions.file_path.notlike("sb://%"))
        # Library assets only. source_type='web' rows are the DOWNLOAD
        # pipeline (global/resources/web/... and the legacy date-bucket
        # layout) — handled by the sibling ``downloads`` module below, not
        # here. The first prod dry-run (2026-07-12) pulled them into THIS
        # module and 70/880 rows failed with IsADirectoryError: some are
        # album DIRECTORIES, not files, which this module's extract/
        # update_row never accounted for.
        .where(Resources.source_type.in_(["upload", "generated", "derived"]))
        .order_by(ResourceVersions.id)
        .limit(limit)
    )
    if scope_id is not None:
        stmt = stmt.where(ri.c.scope_id == scope_id)
    return stmt


def _uploads_extract(row: dict) -> RowExtract:
    scope_id = row.get("scope_id")
    if scope_id is None:
        raise RuntimeError(
            f"resource_versions.id={row.get('id')} has no resource_items scope "
            "— cannot content-address"
        )
    return RowExtract(
        scope_id=int(scope_id),
        mime=row.get("mime_type")
        or row.get("resource_mime_type")
        or "application/octet-stream",
        filename=row.get("filename") or row.get("resource_filename"),
    )


# Child + parent sync as TWO ORM UPDATEs inside ONE ``write_scope()`` session
# → one transaction, one commit (write_scope's session.begin() owns it) —
# same atomicity guarantee the former CTE gave via a single autocommit
# ``db_engine.execute``. ``row["resource_id"]`` is already known from the
# batch SELECT (it's the CTE's own RETURNING value), so no RETURNING
# round-trip is needed — the parent UPDATE's WHERE just uses it directly.
# Both UPDATEs are on models that are either unscoped (ResourceVersions) or
# bulk-UPDATE-forbidden-except-under-SYSTEM (Resources) — both fine here
# since the whole workflow runs under ``system_request_scope`` (see
# ``storage_migration_workflow``).
async def _uploads_update_row(row: dict, file_path: str, sha256: Optional[str]) -> None:
    sync_parent = row.get("version_number") == row.get("current_version")
    async with write_scope() as session:
        await session.execute(
            update(ResourceVersions)
            .where(ResourceVersions.id == row["id"])
            .values(file_path=file_path, file_hash=sha256)
        )
        if sync_parent:
            await session.execute(
                update(Resources)
                .where(Resources.id == row["resource_id"])
                .values(file_path=file_path, file_hash=sha256)
            )


# ── project_files: file_versions (+ project_files.file_path sync) ──────
#
# Structurally identical to uploads, but on the project side: file_versions
# is the version table (models/teams.py), project_files the parent. Scope
# for content-addressing is the owning project_id — deliberately simpler
# than the live-upload path's team-id resolution (_resolve_project_scope_id
# in projects_service.py); a legacy row just needs a stable, unique int
# bucket, not team-membership semantics. Neither table has a sha256/
# file_hash column (unlike resources/resource_versions), so update_row
# only ever touches file_path.


def _project_files_select_stmt(scope_id: Optional[int], limit: int):
    """ORM port of the former ``_PROJECT_FILES_SELECT_SQL`` string."""
    stmt = (
        select(
            FileVersions.id,
            FileVersions.file_id,
            FileVersions.version_number,
            FileVersions.file_path,
            FileVersions.filename,
            FileVersions.mime_type,
            ProjectFiles.current_version,
            ProjectFiles.project_id,
            ProjectFiles.filename.label("file_filename"),
            ProjectFiles.mime_type.label("file_mime_type"),
        )
        .select_from(FileVersions)
        .join(ProjectFiles, ProjectFiles.id == FileVersions.file_id)
        .where(FileVersions.file_path.isnot(None))
        .where(FileVersions.file_path.notlike("sb://%"))
        .order_by(FileVersions.id)
        .limit(limit)
    )
    if scope_id is not None:
        stmt = stmt.where(ProjectFiles.project_id == scope_id)
    return stmt


def _project_files_extract(row: dict) -> RowExtract:
    project_id = row.get("project_id")
    if project_id is None:
        raise RuntimeError(
            f"file_versions.id={row.get('id')} has no project_id — cannot "
            "content-address"
        )
    return RowExtract(
        scope_id=int(project_id),
        mime=row.get("mime_type")
        or row.get("file_mime_type")
        or "application/octet-stream",
        filename=row.get("filename") or row.get("file_filename"),
    )


# Same two-UPDATE-in-one-write_scope() atomicity rationale as
# _uploads_update_row above. Neither table has a file_hash column, so no
# sha256 is written.
async def _project_files_update_row(
    row: dict, file_path: str, sha256: Optional[str]
) -> None:
    sync_parent = row.get("version_number") == row.get("current_version")
    async with write_scope() as session:
        await session.execute(
            update(FileVersions)
            .where(FileVersions.id == row["id"])
            .values(file_path=file_path)
        )
        if sync_parent:
            await session.execute(
                update(ProjectFiles)
                .where(ProjectFiles.id == row["file_id"])
                .values(file_path=file_path)
            )


# ── downloads: resource_versions where source_type='web' ───────────────
#
# The DOWNLOAD pipeline's counterpart to ``uploads`` above — same two
# tables, opposite ``source_type`` filter. The wrinkle uploads/project_files
# don't have: a download's ``file_path`` can point at a DIRECTORY (an album
# of slides, e.g. ``global/resources/web/douyin/{pm_id}/`` full of
# ``{aweme}_0.jpg`` + ``cover.jpg``), not a single file. ``_downloads_extract``
# calls out that case via ``is_album`` so ``_migrate_row`` can branch to
# ``_migrate_album_row`` instead of treating it as one content-addressed
# object (which is exactly the IsADirectoryError the first prod dry-run hit
# — see the ``uploads`` SELECT comment above).


def _downloads_select_stmt(scope_id: Optional[int], limit: int):
    """ORM port of the former ``_DOWNLOADS_SELECT_SQL`` string."""
    ri = (
        select(ResourceItems.scope_id)
        .where(ResourceItems.resource_id == Resources.id)
        .order_by(ResourceItems.id)
        .limit(1)
        .lateral("ri")
    )
    stmt = (
        select(
            ResourceVersions.id,
            ResourceVersions.resource_id,
            ResourceVersions.version_number,
            ResourceVersions.file_path,
            ResourceVersions.filename,
            ResourceVersions.mime_type,
            Resources.current_version,
            ri.c.scope_id,
        )
        .select_from(ResourceVersions)
        .join(Resources, Resources.id == ResourceVersions.resource_id)
        .join(ri, true(), isouter=True)
        .where(ResourceVersions.file_path.isnot(None))
        .where(ResourceVersions.file_path.notlike("sb://%"))
        .where(ResourceVersions.storage_status == "ok")
        .where(Resources.source_type == "web")
        .order_by(ResourceVersions.id)
        .limit(limit)
    )
    if scope_id is not None:
        stmt = stmt.where(ri.c.scope_id == scope_id)
    return stmt


def _downloads_extract(row: dict) -> RowExtract:
    """Classify a download row: single video file, or an album directory.

    ``os.path.isdir`` needs the file to actually be there at extract time —
    fine here because ``_migrate_row`` only calls ``extract()`` after its own
    ``local.exists()`` check has already passed (see call site).
    """
    full = os.path.join(settings.DOWNLOAD_PATH, row["file_path"])
    return RowExtract(
        scope_id=int(row.get("scope_id") or 0),
        mime=row.get("mime_type") or "application/octet-stream",
        filename=row.get("filename"),
        is_album=os.path.isdir(full),
    )


# Same two-UPDATE-in-one-write_scope() atomicity rationale as
# _uploads_update_row above. ``sha256`` is None for an album (no single
# content hash applies — see ``_migrate_album_row``) — omitting file_hash
# from ``.values()`` entirely (rather than binding NULL) leaves any existing
# value alone, the ORM equivalent of the former ``COALESCE(:sha256,
# file_hash)``.
async def _downloads_update_row(
    row: dict, file_path: str, sha256: Optional[str]
) -> None:
    sync_parent = row.get("version_number") == row.get("current_version")
    values: dict = {"file_path": file_path}
    if sha256 is not None:
        values["file_hash"] = sha256
    async with write_scope() as session:
        await session.execute(
            update(ResourceVersions)
            .where(ResourceVersions.id == row["id"])
            .values(**values)
        )
        if sync_parent:
            await session.execute(
                update(Resources)
                .where(Resources.id == row["resource_id"])
                .values(**values)
            )


# ── hls: resource_versions.hls_path (filesystem → HlsPublisher) ────────
#
# ``hls_path`` on disk has TWO different directory layouts depending on
# where the transcode source lived (see transcode_service.py:155-173):
#   - source already sb:// (upload/generated)  → derived/hls/{rid}/{vid}/
#   - source still on the filesystem (download) → {source.parent}/hls/
# The second shape has no rid/vid encoded in the path text at all — and the
# download pipeline never had ObjectStore support until this epic's Task 1,
# so it's predominantly THIS shape among the 97 legacy rows. A path-text
# regex therefore silently mis-handles (or raises on) most of the batch.
# rid/vid are read straight off the row instead: resource_versions.id IS
# the "version_id" every other version lookup keys off
# (repositories/resources_repository.py::get_version_by_id), and
# resource_id is the rid — both already in the SELECT below, so this can't
# fail the way path parsing can. The directory to migrate is still resolved
# from hls_path itself (via resolve_media_source, in _migrate_hls_row),
# independent of which naming convention produced it.


def _hls_select_stmt(scope_id: Optional[int], limit: int):
    """ORM port of the former ``_HLS_SELECT_SQL`` string. ``ri`` correlates
    directly to ``rv.resource_id`` — no join to Resources here (unlike
    uploads/downloads), matching the original SQL exactly."""
    ri = (
        select(ResourceItems.scope_id)
        .where(ResourceItems.resource_id == ResourceVersions.resource_id)
        .order_by(ResourceItems.id)
        .limit(1)
        .lateral("ri")
    )
    stmt = (
        select(
            ResourceVersions.id,
            ResourceVersions.resource_id,
            ResourceVersions.version_number,
            ResourceVersions.hls_path,
            ri.c.scope_id,
        )
        .select_from(ResourceVersions)
        .join(ri, true(), isouter=True)
        .where(ResourceVersions.hls_path.isnot(None))
        .where(ResourceVersions.hls_path.notlike("sb://%"))
        .where(ResourceVersions.storage_status == "ok")
        .order_by(ResourceVersions.id)
        .limit(limit)
    )
    if scope_id is not None:
        stmt = stmt.where(ri.c.scope_id == scope_id)
    return stmt


def _hls_extract(row: dict) -> RowExtract:
    """rid/vid come straight off the row, NOT parsed from ``hls_path`` text.

    ``hls_path`` has two different directory layouts depending on whether
    the transcode source was already ``sb://`` or still on the filesystem
    (see the module-level comment above) — only one of them encodes rid/vid
    in the path itself, so text parsing silently mis-handles (or raises on)
    the other. ``resource_versions.id`` is the same "version_id" every other
    version lookup keys off, and ``resource_id`` is the rid — both are
    already selected, so this can't fail the way path parsing can. Still
    raises (not a silent None) if either is somehow missing — same
    convention as ``_uploads_extract``/``_project_files_extract``: a row
    this migration cannot safely address is a failed row, not a skipped one.
    """
    resource_id = row.get("resource_id")
    version_id = row.get("id")
    if resource_id is None or version_id is None:
        raise RuntimeError(
            f"resource_versions row missing resource_id/id — cannot address "
            f"HLS output: {row!r}"
        )
    return RowExtract(
        scope_id=int(row.get("scope_id") or 0),
        mime="application/vnd.apple.mpegurl",
        filename=None,
        hls_rid=str(resource_id),
        hls_vid=str(version_id),
    )


async def _hls_update_row(row: dict, file_path: str, sha256: Optional[str]) -> None:
    # sha256 is always None for hls (no single content hash for a directory
    # tree) — kept only to match ModuleConfig.update_row's shared signature.
    async with write_scope() as session:
        await session.execute(
            update(ResourceVersions)
            .where(ResourceVersions.id == row["id"])
            .values(hls_path=file_path)
        )


# ── derived: thumbnail_path/cover_image_path (DB-column-driven) + sprite
#    (filesystem walk — has no DB column) ────────────────────────────────
#
# Rework (2026-07-29): the previous version of this module walked
# DOWNLOAD_PATH/derived/{thumbnails,covers}/{rid}/ on disk and found only 10
# directories in prod. A full dry-run of the *other* four modules exposed why
# that vastly undercounts the real backlog: ``resources.thumbnail_path`` has
# 892 non-sb rows and ``cover_image_path`` has 632, and the overwhelming
# majority of them (765 thumbnails) sit NEXT TO THE SOURCE FILE —
# ``global/resources/web/douyin/{pm_id}/thumbnail.webp``, generated by the
# pre-Task-1 download pipeline before ``derived/{rid}/`` was ever a thing —
# plus another 119 under ``teams/``. The disk walk can't reach any of that;
# it only ever saw the small number of thumbnails generated post-Task-1 that
# DerivedArtifactPaths already routed straight into the new layout.
#
# The fix: stop walking a directory and SELECT the authoritative DB columns
# instead — same house style as ``uploads``/``project_files``/``downloads``.
# ``thumbnail_path`` and ``cover_image_path`` are real, DB-addressable
# columns (thumbnail_service.py sets the former after generating; upload_
# resource_cover sets the latter) — wherever the file actually lives
# (next-to-source, teams/, or the old derived/ layout), the column says so
# and resolve_media_source can locate it. ``preview_sprite.jpg`` is the one
# exception: no column anywhere persists its location (neither
# thumbnail_service.py nor resources_crud_router.py ever writes one —
# serve_preview_sprite finds it purely by resource_id + a fixed filename
# convention), so it alone still comes from a disk walk under
# ``derived/thumbnails/{rid}/preview_sprite.jpg``. Row source is therefore a
# UNION: DB-column-driven for thumbnail/cover, disk-walk for sprite.
#
# Every migrated asset — regardless of which of the three it is or where it
# used to live — lands under the SAME clean prefix ``derived/{rid}/``,
# reusing only the original FILENAME (thumbnail.webp / cover.jpg /
# preview_sprite.jpg). That is the exact key shape
# ``serve_resource_cover``/``serve_preview_sprite`` already check
# (``sb://library/derived/{rid}/{filename}``) — see resources_crud_router.py.
# Distinct filenames mean thumbnail/cover/sprite for the SAME resource_id
# never collide under that shared prefix.
#
# Each row here migrates exactly ONE file via ``put_file`` (not ``put_dir``)
# — there's no longer a "directory" to migrate, just one column's/sprite's
# path resolved to one real file on disk.

_DERIVED_COLUMNS = ("thumbnail_path", "cover_image_path")


# Coarse filter only (row-level ``resolve_media_source`` in
# ``_migrate_derived_row`` is still the real authority) — mirrors every other
# module's SELECT convention. A resource can appear at most once here even
# though it may contribute up to two rows (one per non-sb column) once
# ``_list_derived_rows`` fans it out below.
def _derived_db_select_stmt(limit: int):
    """ORM port of the former ``_DERIVED_DB_SELECT_SQL`` string."""
    return (
        select(
            Resources.id.label("resource_id"),
            Resources.thumbnail_path,
            Resources.cover_image_path,
        )
        .where(
            or_(
                and_(
                    Resources.thumbnail_path.isnot(None),
                    Resources.thumbnail_path.notlike("sb://%"),
                ),
                and_(
                    Resources.cover_image_path.isnot(None),
                    Resources.cover_image_path.notlike("sb://%"),
                ),
            )
        )
        .order_by(Resources.id)
        .limit(limit)
    )


# Whitelist: the ONLY two columns ``_migrate_derived_row`` is allowed to
# write, each mapped to its own attribute name — same defense-in-depth
# rationale as ``_PM_ASSETS_COLUMNS_WHITELIST`` below (never build an
# update from an unexpected column value).
_DERIVED_COLUMNS_WHITELIST = ("thumbnail_path", "cover_image_path")


async def _list_derived_rows(scope_id: Optional[int], limit: int) -> list[dict]:
    """DB-column-driven rows for thumbnail_path/cover_image_path, UNION a
    filesystem walk for preview_sprite.jpg (the one derived asset with no DB
    column — see module comment above for why).

    Each returned row is ``{"resource_id", "column", "rel_path"}`` — one row
    per non-sb column value (thumbnail/cover) or per sprite file found on
    disk. ``column`` is ``None`` for a sprite row (nothing to sync back after
    migrating it). ``scope_id`` scoping is NOT supported (derived assets
    carry no scope of their own, unlike the content-addressed modules): a
    non-None value raises rather than silently returning an unscoped batch
    when the caller asked to restrict one.
    """
    if scope_id is not None:
        raise ValueError(
            "storage-migration module 'derived' does not support scope_id "
            "filtering — derived assets are keyed by resource_id only"
        )

    rows: list[dict] = []

    async with read_scope() as session:
        db_rows = (
            (await session.execute(_derived_db_select_stmt(limit))).mappings().all()
        )
    for db_row in db_rows:
        resource_id = db_row["resource_id"]
        for column in _DERIVED_COLUMNS:
            value = db_row.get(column)
            if value and not value.startswith("sb://"):
                rows.append(
                    {"resource_id": resource_id, "column": column, "rel_path": value}
                )

    sprite_base = Path(settings.DOWNLOAD_PATH) / "derived" / "thumbnails"
    if sprite_base.is_dir():
        for rid_dir in sorted(sprite_base.iterdir()):
            if not rid_dir.is_dir():
                continue
            sprite = rid_dir / "preview_sprite.jpg"
            if sprite.is_file():
                rows.append(
                    {
                        "resource_id": rid_dir.name,
                        "column": None,
                        "rel_path": f"derived/thumbnails/{rid_dir.name}/preview_sprite.jpg",
                    }
                )

    # Limit is enforced AFTER the union, not per-source — the DB SELECT's own
    # LIMIT already coarsely caps its contribution, but a resource can fan
    # out into two rows (thumbnail + cover), so the combined list can exceed
    # ``limit`` before this final slice.
    return rows[:limit]


def _derived_extract(row: dict) -> RowExtract:
    # Never actually called — ``derived`` bypasses the generic single-object/
    # album/hls dispatch entirely (see storage_migration_workflow's
    # module-name branch). Raises loudly if some future refactor accidentally
    # routes it through _migrate_row anyway, instead of silently
    # content-addressing a derived asset into the wrong key scheme.
    raise RuntimeError(
        "derived module rows are handled by _migrate_derived_row directly, "
        "not the generic extract()/_migrate_row path"
    )


async def _derived_update_row(row: dict, file_path: str, sha256: Optional[str]) -> None:
    # Same as _derived_extract — unused, present only to satisfy
    # ModuleConfig's required field shape.
    raise RuntimeError(
        "derived module rows are handled by _migrate_derived_row directly, "
        "not the generic update_row() path"
    )


async def _migrate_derived_row(row: dict, *, dry_run: bool, delete_source: bool) -> str:
    """Migrate ONE derived asset (a thumbnail, a cover, or a sprite) to
    ``sb://library/derived/{rid}/{filename}`` — a single ``put_file``, not a
    directory migration (there's no longer a directory backing a row: DB
    columns and the sprite convention both resolve to one real file).

    Same 4-step safety ordering as every other module (verify before any DB
    mutation; dry_run stops there; delete only after the DB sync below has
    committed) — adapted for a single object: the verify step is a
    size-mismatch check on THIS row's own key (never a prefix count — see the
    prefix-sharing note below), and "the DB mutation" is a column UPDATE only
    when this row came from a DB column (``row["column"]`` is ``None`` for a
    sprite row, which has no column to sync).
    """
    resource_id = row["resource_id"]
    rel_path = row["rel_path"]
    column = row.get("column")

    # Defensive idempotent-replay guard, same as _migrate_row: rows fed in
    # here should already be filtered to non-sb (SQL filter / sprite has no
    # scheme at all), so this should never actually trigger — but a stale
    # replay of an already-migrated row must degrade to "skipped", not try to
    # treat an sb:// value as a filesystem path.
    loc = media_storage.resolve_media_source(rel_path)
    if loc.is_object_store:
        return "skipped"

    # Containment guard — same rationale as _migrate_row: rel_path is legacy
    # DB/disk data, never trust it to stay under DOWNLOAD_PATH without
    # checking (a poisoned value with ".." segments must not resolve outside
    # DOWNLOAD_PATH before this row gets stat'd, PUT and possibly unlink'd).
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(base, loc.rel_path or rel_path))
    if not (real == base or real.startswith(base + os.sep)):
        raise RuntimeError(f"derived rel_path escapes DOWNLOAD_PATH: {rel_path!r}")
    local = Path(real)
    if not local.is_file():
        return "missing"

    filename = local.name
    key = f"{media_storage.derived_key_prefix(resource_id)}{filename}"
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    store = media_storage.library_store()
    local_size = local.stat().st_size
    await store.put_file(key, str(local), mime)

    # Verify BEFORE touching the DB — same ordering guarantee as every other
    # module. THIS row's own key only, size-compared against the local file
    # (fix round 1, Finding 2 still applies: ``derived/{rid}/`` has no kind
    # segment, so thumbnail/cover/sprite rows for the SAME resource_id share
    # one prefix — a *prefix* count would be fooled by a sibling row's
    # object; a single-key size check can't be).
    remote_size = await store.get_size(key)
    if remote_size != local_size:
        raise RuntimeError(
            f"derived object size mismatch after put_file: key={key} "
            f"remote={remote_size} local={local_size}"
        )

    if dry_run:
        return "dry_run_ok"

    if column:
        if column not in _DERIVED_COLUMNS_WHITELIST:
            # Defensive — should be unreachable: _list_derived_rows only ever
            # emits one of the two whitelisted columns. Raise loudly rather
            # than silently building an update from an unexpected value.
            raise RuntimeError(
                f"derived: column {column!r} is not in the update whitelist "
                f"({_DERIVED_COLUMNS_WHITELIST}) — refusing to migrate"
            )
        async with write_scope() as session:
            await session.execute(
                update(Resources)
                .where(Resources.id == int(resource_id))
                .values(**{column: media_storage.to_file_path(store.bucket, key)})
            )

    if delete_source:
        # Single-file unlink, NEVER rmtree: a next-to-source thumbnail lives
        # in the SAME directory as the original video/image — removing the
        # directory would take the source down with it.
        local.unlink(missing_ok=True)

    return "migrated"


# ── pm_assets: parsed_media cover/audio columns (DB-column-driven) ─────
#
# Three columns on ``parsed_media`` still hold legacy filesystem paths:
# ``cover_download_path`` (cover.jpg), ``music_download_path`` (BGM
# audio.m4a), ``extract_audio_path`` (extracted audio.m4a). Read-side
# object-store awareness already landed (cover: fix commit 8d28158 in this
# same branch; music/extract: C4/C6/C7), so it's safe to migrate the write
# side now.
#
# Same house style as ``derived``: DB-column-driven, one row per
# ``parsed_media.id`` that fans out to up to 3 migration UNITS (one per
# non-sb column present on that row) via ``_list_pm_assets_rows``, each unit
# migrated independently by ``_migrate_pm_assets_row`` — NOT routed through
# the generic ``_migrate_row``/``extract()``/``update_row()`` path (same
# reason as derived: this module's "rows" don't map 1:1 to what that path
# expects).
#
# Unlike ``derived`` (fixed ``derived/{rid}/{filename}`` key, no scope
# concept), this module uses content-addressed ``store_local_file`` — the
# same content-addressing every other module's single-object path uses.
# That gets automatic dedup for free: a ``cover_download_path`` value that
# happens to be byte-identical to something already content-addressed
# under the same scope_id (e.g. a duplicate of an already-migrated file)
# hits ``store_local_file``'s internal ``exists()`` check and skips the PUT
# — no special-cased "is this a refresh or a real migration" branch is
# needed, content-addressing handles both uniformly.
#
# scope_id is resolved the same way ``uploads``/``downloads`` do: via the
# owning resource's ``resource_items.scope_id`` (LEFT JOIN LATERAL, deterministic
# pick via ORDER BY id LIMIT 1). A parsed_media row with no resource, or a
# resource with no resource_items scope, is an ORPHAN — legacy/system-
# initiated rows that never got a scope. Those can't be content-addressed
# (scope_id is part of the key), so ``_migrate_pm_assets_row`` skips them
# (``skipped_no_scope``) rather than raising — one orphan must not fail the
# whole batch.
#
# Column name only ever comes from the fixed whitelist below (never
# string-interpolated into SQL, and never passed as an arbitrary ``.values()``
# kwarg without the membership check in ``_migrate_pm_assets_row``) — a
# defense-in-depth whitelist even though the only caller
# (``_list_pm_assets_rows``) can only ever emit one of the three known keys.

_PM_ASSETS_MIME: dict[str, str] = {
    "cover_download_path": "image/jpeg",
    "music_download_path": "audio/mp4",
    "extract_audio_path": "audio/mp4",
}

# Whitelist: the ONLY three columns this module is allowed to write.
# ``_migrate_pm_assets_row`` checks membership (raising on a miss) instead of
# ever building ``update(ParsedMedia).values(**{column: ...})`` from an
# unvetted column value — row data is untrusted-ish legacy input and must
# never reach a write unchecked.
_PM_ASSETS_COLUMNS_WHITELIST = (
    "cover_download_path",
    "music_download_path",
    "extract_audio_path",
)


def _pm_assets_select_stmt(scope_id: Optional[int], limit: int):
    """ORM port of the former ``_PM_ASSETS_SELECT_SQL`` string. The
    ``resources`` join stays a PLAIN (non-LATERAL) LEFT JOIN — same as the
    legacy SQL — so a media_id with more than one resources row fans out
    exactly like the original; only ``ri`` (resource_items) is a
    deterministic LATERAL pick."""
    ri = (
        select(ResourceItems.scope_id)
        .where(ResourceItems.resource_id == Resources.id)
        .order_by(ResourceItems.id)
        .limit(1)
        .lateral("ri")
    )
    stmt = (
        select(
            ParsedMedia.id.label("pm_id"),
            ParsedMedia.cover_download_path,
            ParsedMedia.music_download_path,
            ParsedMedia.extract_audio_path,
            ri.c.scope_id,
        )
        .select_from(ParsedMedia)
        .join(Resources, Resources.media_id == ParsedMedia.id, isouter=True)
        .join(ri, true(), isouter=True)
        .where(
            or_(
                and_(
                    ParsedMedia.cover_download_path.isnot(None),
                    ParsedMedia.cover_download_path.notlike("sb://%"),
                ),
                and_(
                    ParsedMedia.music_download_path.isnot(None),
                    ParsedMedia.music_download_path.notlike("sb://%"),
                ),
                and_(
                    ParsedMedia.extract_audio_path.isnot(None),
                    ParsedMedia.extract_audio_path.notlike("sb://%"),
                ),
            )
        )
        .order_by(ParsedMedia.id)
        .limit(limit)
    )
    if scope_id is not None:
        stmt = stmt.where(ri.c.scope_id == scope_id)
    return stmt


async def _list_pm_assets_rows(scope_id: Optional[int], limit: int) -> list[dict]:
    """DB-column-driven rows for the three parsed_media asset columns.

    Each returned row is ONE migration unit: ``{"pm_id", "column", "rel_path",
    "scope_id", "mime"}`` — one per non-sb column value on a given
    ``parsed_media`` row (up to 3 per row: cover/music/extract). ``scope_id``
    is whatever ``resource_items.scope_id`` resolved to for that row's
    resource — ``None`` when the parsed_media row is an orphan (no resource,
    or a resource with no resource_items scope); that's preserved as-is here
    (not skipped/raised) — the skip decision for a NULL scope belongs to
    ``_migrate_pm_assets_row``, not this listing function.
    """
    async with read_scope() as session:
        db_rows = (
            (await session.execute(_pm_assets_select_stmt(scope_id, limit)))
            .mappings()
            .all()
        )

    rows: list[dict] = []
    for db_row in db_rows:
        pm_id = db_row["pm_id"]
        row_scope_id = db_row.get("scope_id")
        for column in _PM_ASSETS_COLUMNS_WHITELIST:
            value = db_row.get(column)
            if value and not value.startswith("sb://"):
                rows.append(
                    {
                        "pm_id": pm_id,
                        "column": column,
                        "rel_path": value,
                        "scope_id": row_scope_id,
                        "mime": _PM_ASSETS_MIME[column],
                    }
                )

    # Limit enforced AFTER the fan-out, not per-DB-row — the SELECT's own
    # LIMIT coarsely caps the number of parsed_media rows, but each row can
    # fan out into up to 3 units, so the combined list can exceed ``limit``
    # before this final slice (same union-limit convention as
    # ``_list_derived_rows``).
    return rows[:limit]


def _pm_assets_extract(row: dict) -> RowExtract:
    # Never actually called — pm_assets bypasses the generic single-object/
    # album/hls dispatch entirely (see storage_migration_workflow's
    # module-name branch), same as derived. Raises loudly if some future
    # refactor accidentally routes it through _migrate_row anyway.
    raise RuntimeError(
        "pm_assets module rows are handled by _migrate_pm_assets_row directly, "
        "not the generic extract()/_migrate_row path"
    )


async def _pm_assets_update_row(
    row: dict, file_path: str, sha256: Optional[str]
) -> None:
    # Same as _pm_assets_extract — unused, present only to satisfy
    # ModuleConfig's required field shape.
    raise RuntimeError(
        "pm_assets module rows are handled by _migrate_pm_assets_row directly, "
        "not the generic update_row() path"
    )


async def _migrate_pm_assets_row(
    row: dict, *, dry_run: bool, delete_source: bool
) -> str:
    """Migrate ONE parsed_media asset (cover, music, or extracted audio) via
    content-addressed ``store_local_file`` — a single object write, dedup-
    safe by construction (same content + scope + extension always resolves
    to the same key, so a byte-identical file already migrated elsewhere
    just skips the PUT).

    Same 4-step safety ordering as every other module (verify before any DB
    mutation; dry_run stops there; delete only after the DB sync below has
    committed), plus one extra early exit unique to this module: a ``None``
    scope_id (orphan parsed_media with no resource / no resource_items
    scope) can't be content-addressed at all, so it's skipped before ever
    touching the filesystem or store.
    """
    pm_id = row["pm_id"]
    column = row["column"]
    rel_path = row["rel_path"]
    scope_id = row.get("scope_id")
    mime = row["mime"]

    if column not in _PM_ASSETS_COLUMNS_WHITELIST:
        # Defensive — should be unreachable: _list_pm_assets_rows only ever
        # emits one of the three whitelisted columns. Raise loudly rather
        # than silently building an update from an unexpected column value.
        raise RuntimeError(
            f"pm_assets: column {column!r} is not in the update whitelist "
            f"({_PM_ASSETS_COLUMNS_WHITELIST}) — refusing to migrate"
        )

    if scope_id is None:
        # Orphan parsed_media — no resource, or a resource with no
        # resource_items scope — cannot content-address without a scope.
        # Skip (not raise): one orphan must not fail the whole batch.
        logger.warning(
            f"[storage-migration] pm_assets pm_id={pm_id} column={column} has "
            "no resolvable scope (orphan parsed_media) — skip"
        )
        return "skipped_no_scope"

    loc = media_storage.resolve_media_source(rel_path)
    if loc.is_object_store:
        return "skipped"  # already migrated — idempotent replay

    # Containment guard — same rationale as every other module: rel_path is
    # legacy DB data, never trust it to stay under DOWNLOAD_PATH without
    # checking.
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(base, loc.rel_path or rel_path))
    if not (real == base or real.startswith(base + os.sep)):
        raise RuntimeError(f"pm_assets rel_path escapes DOWNLOAD_PATH: {rel_path!r}")
    local = Path(real)
    if not local.is_file():
        return "missing"

    local_size = local.stat().st_size
    store = media_storage.library_store()
    stored = await media_storage.store_local_file(
        scope_id=int(scope_id),
        source_path=str(local),
        mime=mime,
        filename=local.name,
        store=store,
    )

    if not stored.file_path.startswith("sb://"):
        raise RuntimeError(
            f"pm_assets store_local_file returned a non-sb:// path: "
            f"pm_id={pm_id} column={column} got={stored.file_path!r}"
        )

    # Verify BEFORE touching the DB — same ordering guarantee as every other
    # module. Re-resolve the key via resolve_media_source (not a hardcoded
    # "sb://library/" strip) so this keeps working regardless of bucket name.
    stored_loc = media_storage.resolve_media_source(stored.file_path)
    remote_size = await store.get_size(stored_loc.key)
    if remote_size != local_size:
        raise RuntimeError(
            f"pm_assets size mismatch after store_local_file: pm_id={pm_id} "
            f"column={column} remote={remote_size} local={local_size}"
        )

    if dry_run:
        return "dry_run_ok"

    async with write_scope() as session:
        await session.execute(
            update(ParsedMedia)
            .where(ParsedMedia.id == int(pm_id))
            .values(**{column: stored.file_path})
        )

    if delete_source:
        # Single-file unlink, NEVER rmtree — cover/audio sit NEXT TO the
        # source video (same directory); removing the directory would take
        # the video down with it.
        local.unlink(missing_ok=True)

    return "migrated"


# ── web_resource_files: resources.file_path with no resource_versions row ──
#
# ``downloads`` (above) selects via ``resource_versions JOIN resources`` —
# that JOIN is exactly why it misses 202 web resources (mostly qishui audio)
# whose ``resources.file_path`` is set but which never got a
# ``resource_versions`` row at all (legacy download pipeline wrote straight
# to the resource, no version history). This module picks those up with a
# plain SELECT off ``resources`` alone — no JOIN, no fan-out (unlike
# ``pm_assets``, there's exactly one column/one migration unit per row here).
#
# ``AND NOT EXISTS (... resource_versions ...)`` makes the split from
# ``downloads`` STRUCTURAL, not a data-state assumption. Without it, a web
# resource that DOES have a resource_versions row but hasn't been migrated by
# ``downloads`` yet (``resources.file_path`` still filesystem) would ALSO
# match this module's SELECT — this module would migrate
# ``resources.file_path`` while leaving ``resource_versions.file_path``
# untouched, and a subsequent ``delete_source`` run would delete the shared
# local file out from under the still-filesystem ``resource_versions`` row
# (nothing else ever fixes that dangling path). Requiring zero
# resource_versions rows makes the two modules' row sets structurally
# disjoint — ``downloads`` owns every resource_id that has an rv row,
# regardless of run order, and this module owns only what never had one.
#
# Same content-addressed ``store_local_file`` write path as ``pm_assets`` —
# not the fixed-prefix ``derived`` style, since a web resource has no natural
# "kind" segment to key by. Already-``sb://`` rows are excluded by the SQL
# filter itself (idempotent — this can't double-run against ``downloads`` or
# a prior pass of itself), and ``_migrate_web_resource_files_row`` keeps the
# same defensive ``resolve_media_source`` check as every other module's own
# migrate function in case of a stale replay.
#
# Bypasses the generic ``_migrate_row``/``extract()``/``update_row()`` path
# (same reason as ``derived``/``pm_assets``: this module's own function owns
# the whole 4-step ordering directly) — but unlike those two, rows still come
# from a plain SELECT (``select_stmt``, no ``list_rows``), since there's no
# fan-out or filesystem walk here to justify one.
#
# ONLY ``resources.file_path`` is written here. ``parsed_media.download_path``
# (also set on qishui rows) is intentionally NOT touched — that column is
# refreshed by a post-migration SQL pass, not by this module (see task brief:
# keeping the two write paths separate avoids this module needing to resolve
# resources → parsed_media on top of everything else it already does).


def _web_resource_files_select_stmt(scope_id: Optional[int], limit: int):
    """ORM port of the former ``_WEB_RESOURCE_FILES_SELECT_SQL`` string. The
    ``NOT EXISTS (SELECT 1 FROM resource_versions ...)`` structural split
    from ``downloads`` becomes an ``~exists()`` correlated subquery."""
    ri = (
        select(ResourceItems.scope_id)
        .where(ResourceItems.resource_id == Resources.id)
        .order_by(ResourceItems.id)
        .limit(1)
        .lateral("ri")
    )
    no_versions = ~(
        select(ResourceVersions.id)
        .where(ResourceVersions.resource_id == Resources.id)
        .exists()
    )
    stmt = (
        select(
            Resources.id.label("resource_id"),
            Resources.file_path,
            ri.c.scope_id,
        )
        .select_from(Resources)
        .join(ri, true(), isouter=True)
        .where(Resources.source_type == "web")
        .where(Resources.file_path.isnot(None))
        .where(Resources.file_path.notlike("sb://%"))
        .where(Resources.is_trashed.isnot(True))
        .where(no_versions)
        .order_by(Resources.id)
        .limit(limit)
    )
    if scope_id is not None:
        stmt = stmt.where(ri.c.scope_id == scope_id)
    return stmt


def _web_resource_files_extract(row: dict) -> RowExtract:
    # Never actually called — web_resource_files bypasses the generic
    # single-object/album/hls dispatch entirely (see
    # storage_migration_workflow's module-name branch), same as derived/
    # pm_assets. Raises loudly if some future refactor accidentally routes it
    # through _migrate_row anyway.
    raise RuntimeError(
        "web_resource_files module rows are handled by "
        "_migrate_web_resource_files_row directly, not the generic "
        "extract()/_migrate_row path"
    )


async def _web_resource_files_update_row(
    row: dict, file_path: str, sha256: Optional[str]
) -> None:
    # Same as _web_resource_files_extract — unused, present only to satisfy
    # ModuleConfig's required field shape.
    raise RuntimeError(
        "web_resource_files module rows are handled by "
        "_migrate_web_resource_files_row directly, not the generic "
        "update_row() path"
    )


async def _migrate_web_resource_files_row(
    row: dict, *, dry_run: bool, delete_source: bool
) -> str:
    """Migrate ONE orphan web resource (no resource_versions row) via
    content-addressed ``store_local_file`` — a single object write, dedup-
    safe by construction (same content + scope + extension always resolves
    to the same key).

    Same 4-step safety ordering as every other module (verify before any DB
    mutation; dry_run stops there; delete only after the DB update below has
    committed). Missing-file is checked BEFORE the orphan-scope check (a row
    that is both missing AND scope-less reports "missing" — matches the task
    brief's ordering, cheapest/most-fundamental check first).
    """
    resource_id = row["resource_id"]
    rel_path = row["file_path"]
    scope_id = row.get("scope_id")

    # Defensive idempotent-replay guard, same as pm_assets/derived: rows fed
    # in here should already be filtered to non-sb by the SELECT above, but a
    # stale replay of an already-migrated row must degrade to "skipped", not
    # try to treat an sb:// value as a filesystem path.
    loc = media_storage.resolve_media_source(rel_path)
    if loc.is_object_store:
        return "skipped"

    # Containment guard — same rationale as every other module: rel_path is
    # legacy DB data, never trust it to stay under DOWNLOAD_PATH without
    # checking (a poisoned value with ".." segments must not resolve outside
    # DOWNLOAD_PATH before this row gets stat'd, PUT and possibly unlink'd).
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(base, loc.rel_path or rel_path))
    if not (real == base or real.startswith(base + os.sep)):
        raise RuntimeError(
            f"web_resource_files file_path escapes DOWNLOAD_PATH: {rel_path!r}"
        )
    local = Path(real)
    if not local.is_file():
        return "missing"

    if scope_id is None:
        # Orphan resource — no resource_items scope — cannot content-address
        # without a scope. Skip (not raise): one orphan must not fail the
        # whole batch.
        logger.warning(
            f"[storage-migration] web_resource_files resource_id={resource_id} "
            "has no resolvable scope (orphan resource) — skip"
        )
        return "skipped_no_scope"

    mime = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
    local_size = local.stat().st_size
    store = media_storage.library_store()
    stored = await media_storage.store_local_file(
        scope_id=int(scope_id),
        source_path=str(local),
        mime=mime,
        filename=local.name,
        store=store,
    )

    if not stored.file_path.startswith("sb://"):
        raise RuntimeError(
            f"web_resource_files store_local_file returned a non-sb:// path: "
            f"resource_id={resource_id} got={stored.file_path!r}"
        )

    # Verify BEFORE touching the DB — same ordering guarantee as every other
    # module. Re-resolve the key via resolve_media_source (not a hardcoded
    # "sb://library/" strip) so this keeps working regardless of bucket name.
    stored_loc = media_storage.resolve_media_source(stored.file_path)
    remote_size = await store.get_size(stored_loc.key)
    if remote_size != local_size:
        raise RuntimeError(
            f"web_resource_files size mismatch after store_local_file: "
            f"resource_id={resource_id} remote={remote_size} local={local_size}"
        )

    if dry_run:
        return "dry_run_ok"

    # ONLY resources.file_path — parsed_media.download_path is refreshed by a
    # post-migration SQL pass, not here (see module comment above). file_hash
    # omitted from .values() when sha256 is None — ORM equivalent of the
    # former ``COALESCE(:sha, file_hash)`` (leave any existing value alone).
    values: dict = {"file_path": stored.file_path}
    if stored.sha256 is not None:
        values["file_hash"] = stored.sha256
    async with write_scope() as session:
        await session.execute(
            update(Resources).where(Resources.id == int(resource_id)).values(**values)
        )

    if delete_source:
        # Single-file unlink, NEVER rmtree — the resource's directory may
        # hold sibling files.
        local.unlink(missing_ok=True)

    return "migrated"


# storyboard: SKIPPED — see module docstring above. Do not add an entry.


def _no_select_stmt(scope_id: Optional[int], limit: int):
    # Never executed — this module's rows come from its own list_rows
    # function (see the module comment above), not select_stmt. Raises
    # loudly rather than silently returning an empty/wrong statement if some
    # future refactor accidentally calls it.
    raise RuntimeError(
        "this module's rows come from list_rows, not select_stmt — should "
        "be unreachable"
    )


_MODULES: dict[str, ModuleConfig] = {
    "uploads": ModuleConfig(
        name="uploads",
        select_stmt=_uploads_select_stmt,
        extract=_uploads_extract,
        update_row=_uploads_update_row,
    ),
    "project_files": ModuleConfig(
        name="project_files",
        select_stmt=_project_files_select_stmt,
        extract=_project_files_extract,
        update_row=_project_files_update_row,
    ),
    "downloads": ModuleConfig(
        name="downloads",
        select_stmt=_downloads_select_stmt,
        extract=_downloads_extract,
        update_row=_downloads_update_row,
    ),
    "hls": ModuleConfig(
        name="hls",
        select_stmt=_hls_select_stmt,
        extract=_hls_extract,
        update_row=_hls_update_row,
    ),
    "derived": ModuleConfig(
        name="derived",
        # Never executed — this module's rows come from _list_derived_rows
        # (DB-column-driven for thumbnail/cover, disk walk for sprite; see
        # the module comment above), not select_stmt.
        select_stmt=_no_select_stmt,
        extract=_derived_extract,
        update_row=_derived_update_row,
        list_rows=_list_derived_rows,
    ),
    "pm_assets": ModuleConfig(
        name="pm_assets",
        # Never executed — this module's rows come from _list_pm_assets_rows
        # (DB-column-driven fan-out over the 3 parsed_media asset columns;
        # see the module comment above), not select_stmt.
        select_stmt=_no_select_stmt,
        extract=_pm_assets_extract,
        update_row=_pm_assets_update_row,
        list_rows=_list_pm_assets_rows,
    ),
    "web_resource_files": ModuleConfig(
        name="web_resource_files",
        select_stmt=_web_resource_files_select_stmt,
        extract=_web_resource_files_extract,
        update_row=_web_resource_files_update_row,
    ),
}


# ── Row-wise migration core ─────────────────────────────────────────────


async def _migrate_row(
    row: dict,
    module_cfg: ModuleConfig,
    *,
    dry_run: bool,
    delete_source: bool,
) -> str:
    """Migrate one row. Returns an outcome tag: skipped / missing /
    dry_run_ok / migrated. Raises on any failure — see module docstring
    for the safety-contract ordering this enforces."""
    if module_cfg.name == "hls":
        # hls rows key off hls_path, not file_path, and delegate the actual
        # upload to HlsPublisher (see _migrate_hls_row) rather than
        # store_local_file/put_dir — different enough to warrant its own
        # function instead of shoehorning a third branch in here.
        return await _migrate_hls_row(
            row, module_cfg, dry_run=dry_run, delete_source=delete_source
        )
    file_path = row["file_path"]
    loc = media_storage.resolve_media_source(file_path)
    if loc.is_object_store:
        return "skipped"  # already migrated — idempotent replay

    # Containment guard (mirrors materialize()'s in media_storage.py): a
    # poisoned legacy file_path with ".." segments or symlink tricks must
    # never resolve outside DOWNLOAD_PATH. This path gets stat'd, PUT and
    # — with delete_source — unlink'd; without this check a hostile row
    # value is an arbitrary-file-delete primitive. Raise → the caller's
    # per-row try/except counts the row failed, nothing mutated.
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(base, loc.rel_path or ""))
    if not (real == base or real.startswith(base + os.sep)):
        raise RuntimeError(f"file_path escapes DOWNLOAD_PATH: {file_path!r}")
    local = Path(real)
    if not local.exists():
        logger.warning(f"[storage-migration] missing local file, skip: {file_path}")
        return "missing"

    extract = module_cfg.extract(row)

    if extract.is_album:
        return await _migrate_album_row(
            row,
            local,
            extract,
            module_cfg,
            dry_run=dry_run,
            delete_source=delete_source,
        )

    local_size = local.stat().st_size

    stored = await media_storage.store_local_file(
        scope_id=extract.scope_id,
        source_path=str(local),
        mime=extract.mime,
        filename=extract.filename,
    )

    # Verify BEFORE touching the DB row — a raise here leaves the row
    # completely unmutated (the object may exist in storage from this or a
    # prior attempt, but that's harmless: content-addressed + dedup-safe).
    size = await media_storage.library_store().get_size(
        stored.file_path.removeprefix("sb://library/")
    )
    if size != local_size:
        raise RuntimeError(
            f"size mismatch after PUT: {file_path} {size} != {local_size}"
        )

    if dry_run:
        return "dry_run_ok"

    await module_cfg.update_row(row, stored.file_path, stored.sha256)

    if delete_source:
        local.unlink(missing_ok=True)

    return "migrated"


async def _migrate_album_row(
    row: dict,
    local: Path,
    extract: RowExtract,
    module_cfg: ModuleConfig,
    *,
    dry_run: bool,
    delete_source: bool,
) -> str:
    """Album directory → prefix-form ``sb://`` object (many files, one row).

    Same 4-step ordering as ``_migrate_row``, adapted for a directory: there
    is no single sha256/size to check, so the verify step is a file-COUNT
    comparison (local rglob vs. what ``put_dir`` left listed under the
    prefix) instead — it still catches a partial/aborted upload the same way
    the single-object size check catches a truncated PUT.
    """
    if not local.is_dir():
        # extract() already branched on os.path.isdir, so this would only
        # fire on a TOCTOU race (deleted/replaced between extract and here).
        raise RuntimeError(f"expected album directory, got a file: {local}")

    local_files = [p for p in local.rglob("*") if p.is_file()]
    if not local_files:
        raise RuntimeError(f"album directory is empty, nothing to migrate: {local}")

    store = media_storage.library_store()
    # row["id"] is resource_versions.id (a VERSION id, not resources.id) —
    # deliberate: the prefix is self-referential, written straight back into
    # this same row's file_path below, and source_type='web' rows are
    # effectively never re-versioned, so any stable-per-row id works fine.
    prefix = media_storage.album_key_prefix(extract.scope_id, row["id"])

    def _key(rel: str) -> str:
        return f"{prefix}{rel}"

    # skip_existing=True: a replayed batch after a partial failure shouldn't
    # re-PUT files that already landed (mirrors store_local_file's dedup-PUT
    # for the single-object path).
    await store.put_dir(str(local), _key, skip_existing=True)

    # Verify BEFORE touching the DB row — same ordering guarantee as the
    # single-object path's get_size check.
    remote_keys = await store.list_prefix(prefix)
    if len(remote_keys) != len(local_files):
        raise RuntimeError(
            f"album file count mismatch after put_dir: id={row.get('id')} "
            f"local={len(local_files)} remote={len(remote_keys)}"
        )

    if dry_run:
        return "dry_run_ok"

    file_path = media_storage.to_file_path(store.bucket, prefix)
    await module_cfg.update_row(row, file_path, None)

    if delete_source:
        shutil.rmtree(str(local), ignore_errors=True)

    return "migrated"


async def _migrate_hls_row(
    row: dict,
    module_cfg: ModuleConfig,
    *,
    dry_run: bool,
    delete_source: bool,
) -> str:
    """Filesystem HLS directory → object store, via ``HlsPublisher.publish``.

    Structurally like ``_migrate_album_row`` (a directory, not a single
    file, so no single content hash to check) but delegates the actual
    upload to ``HlsPublisher.publish`` instead of ``library_store().put_dir``
    directly — the publisher already encodes the one non-negotiable HLS
    invariant (master.m3u8 uploaded LAST; see hls_publisher.py docstring),
    which this migration must not bypass by re-implementing its own upload
    order.
    """
    hls_path = row["hls_path"]
    loc = media_storage.resolve_media_source(hls_path)
    if loc.is_object_store:
        return "skipped"  # already migrated — idempotent replay

    # Same containment guard as _migrate_row — hls_path is legacy row data,
    # never trust it to stay under DOWNLOAD_PATH without checking.
    base = os.path.realpath(settings.DOWNLOAD_PATH)
    real = os.path.realpath(os.path.join(base, loc.rel_path or ""))
    if not (real == base or real.startswith(base + os.sep)):
        raise RuntimeError(f"hls_path escapes DOWNLOAD_PATH: {hls_path!r}")

    # hls_path points at master.m3u8 itself; the migration unit is its
    # parent directory (the whole segment/tier tree).
    hls_dir = Path(real).parent
    if not hls_dir.is_dir():
        logger.warning(f"[storage-migration] missing local HLS dir, skip: {hls_path}")
        return "missing"

    extract = module_cfg.extract(row)  # raises if hls_path doesn't parse

    local_files = [p for p in hls_dir.rglob("*") if p.is_file()]
    if not local_files:
        raise RuntimeError(f"HLS directory is empty, nothing to migrate: {hls_dir}")

    new_hls_path = await HlsPublisher().publish(
        hls_dir, Path(base), extract.hls_rid, extract.hls_vid
    )

    # Verify BEFORE touching the DB row — same ordering guarantee as
    # _migrate_album_row's file-count check (an HLS tree has no single
    # content hash either). This also catches a forgotten HLS_OBJECT_STORE
    # flag: publish() no-ops back to the same fs-relative path when the
    # flag is off (nothing uploaded), so remote count would be 0 here and
    # the mismatch below raises instead of silently "succeeding".
    store = media_storage.library_store()
    prefix = media_storage.hls_key_prefix(extract.hls_rid, extract.hls_vid)
    remote_keys = await store.list_prefix(prefix)
    if len(remote_keys) != len(local_files):
        raise RuntimeError(
            f"HLS file count mismatch after publish: id={row.get('id')} "
            f"local={len(local_files)} remote={len(remote_keys)}"
        )

    if dry_run:
        return "dry_run_ok"

    await module_cfg.update_row(row, new_hls_path, None)

    if delete_source:
        shutil.rmtree(str(hls_dir), ignore_errors=True)

    return "migrated"


# ── Workflow ─────────────────────────────────────────────────────────────


@DBOS.workflow()
async def storage_migration_workflow(
    module: str,
    scope_id: Optional[int],
    limit: int,
    dry_run: bool,
    delete_source: bool,
) -> dict[str, Any]:
    """Migrate up to ``limit`` not-yet-migrated rows of ``module`` into the
    ``library`` object-store bucket.

    ``scope_id`` restricts the batch to one scope (team/project) when
    given, else the whole module is eligible. Re-running the same (or a
    fresh) ``workflow_id`` is safe — already-``sb://`` rows are skipped.
    """
    from app.services.infra.unified_task_manager import get_task_manager

    module_cfg = _MODULES.get(module)
    if module_cfg is None:
        raise ValueError(f"unknown storage-migration module: {module!r}")

    manager = get_task_manager()
    task_id = DBOS.workflow_id

    # ── task_tracking row (lifecycle via manager API only — CLAUDE.md 路线 C) ──
    try:
        await manager.create(
            user_id=SYSTEM_RUN_USER_ID,
            task_type="storage_migration",
            title=f"Migrate {module} to object storage"[:200],
            subtitle=f"scope={scope_id} limit={limit} dry_run={dry_run}",
            dbos_workflow_id=task_id,
            metadata={
                "module": module,
                "scope_id": scope_id,
                "dry_run": dry_run,
                "delete_source": delete_source,
            },
        )
    except Exception as e:
        logger.warning(
            f"[storage-migration] create task_tracking failed (non-fatal): {e}"
        )
    try:
        await manager.start(
            task_id, user_id=SYSTEM_RUN_USER_ID, task_type="storage_migration"
        )
    except Exception as e:
        logger.warning(f"[storage-migration] start {task_id}: {e}")

    # Admin-triggered, cross-tenant batch migration by design (see module
    # docstring) — the whole read+migrate pass runs under ONE
    # system_request_scope so the tenant-scoped Resources model is neither
    # filtered (a per-user scope would silently skip other users' legacy
    # rows) nor fail-closed RAISEd, and the bulk Core UPDATEs inside each
    # module's update_row (forbidden on Resources under a real user Scope)
    # are allowed. Nested read_scope()/write_scope() calls in the row-level
    # migrate functions don't set scope themselves — they see this ambient
    # SYSTEM scope via the ContextVar for the whole workflow body.
    #
    # Gated on is_enforced("resources") (Final Review Finding 1): every
    # other system_request_scope site in this batch is gated the same way
    # (fail-open to nullcontext() while the flag is off) — this one was
    # accidentally left unconditional. Whole-workflow-body scope, rather
    # than per-statement, is kept deliberately (not narrowed to Finding 1's
    # suggested minimal-block shape): the bulk Core UPDATEs inside
    # update_row are forbidden under ANY real user Scope regardless
    # (_forbid_scoped_bulk_dml), and an admin-triggered full-library
    # migration has no per-user identity to narrow to in the first place —
    # wrapping 12 call sites individually would only add noise, not safety.
    scope_cm = (
        system_request_scope(
            reason=f"admin-initiated storage migration: module={module}"
        )
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        # ``derived``/``pm_assets`` have no single SELECT to run — their rows
        # come from ``module_cfg.list_rows`` (DB-column-driven fan-out / disk
        # walk). Every other module fetches via the shared select_stmt path.
        if module_cfg.list_rows is not None:
            rows = await module_cfg.list_rows(scope_id, limit)
        else:
            async with read_scope() as session:
                rows = (
                    (await session.execute(module_cfg.select_stmt(scope_id, limit)))
                    .mappings()
                    .all()
                )

        counts: dict[str, int] = {
            "migrated": 0,
            "skipped": 0,
            "missing": 0,
            "dry_run_ok": 0,
            "failed": 0,
        }
        total = len(rows)
        for i, row in enumerate(rows, start=1):
            try:
                if module == "derived":
                    outcome = await _migrate_derived_row(
                        row, dry_run=dry_run, delete_source=delete_source
                    )
                elif module == "pm_assets":
                    outcome = await _migrate_pm_assets_row(
                        row, dry_run=dry_run, delete_source=delete_source
                    )
                elif module == "web_resource_files":
                    outcome = await _migrate_web_resource_files_row(
                        row, dry_run=dry_run, delete_source=delete_source
                    )
                else:
                    outcome = await _migrate_row(
                        row, module_cfg, dry_run=dry_run, delete_source=delete_source
                    )
                counts[outcome] = counts.get(outcome, 0) + 1
            except Exception as e:
                counts["failed"] += 1
                row_ref = row.get("id", row.get("resource_id", row.get("pm_id")))
                logger.warning(
                    f"[storage-migration] module={module} row={row_ref} "
                    f"failed (batch continues): {e!r}"
                )
            if i % _PROGRESS_EVERY == 0 or i == total:
                try:
                    await manager.update_progress(
                        task_id, int(i * 100 / total) if total else 100
                    )
                except Exception as e:
                    logger.debug(
                        f"[storage-migration] progress update failed (non-fatal): {e}"
                    )

    result: dict[str, Any] = {"module": module, "total": total, **counts}

    if counts["failed"] > 0:
        # Persist the partial counts into task metadata BEFORE raising so a
        # failed run is still observable (patch_metadata is the unthrottled
        # business-decoration API — metadata jsonb only, never the phase
        # columns, so it's route-C compliant on the failure path too).
        try:
            await manager.patch_metadata(task_id, result)
        except Exception as e:
            logger.warning(
                f"[storage-migration] failed-run metadata patch (non-fatal): {e}"
            )
        # CLAUDE.md 路线 C rule 4: raise, never return a failed dict. DBOS
        # marks the workflow ERROR and mirror_dbos_lifecycle_to_tracking
        # writes phase=failed for us — calling manager.fail() here too
        # would race the trigger over the same columns.
        raise RuntimeError(
            f"[storage-migration] module={module}: {counts['failed']}/{total} "
            "row(s) failed"
        )

    try:
        await manager.complete(
            task_id,
            subtitle=f"{counts['migrated']} migrated, {counts['skipped']} skipped",
            metadata_patch=result,
        )
    except Exception as e:
        logger.warning(f"[storage-migration] complete {task_id}: {e}")

    return result
