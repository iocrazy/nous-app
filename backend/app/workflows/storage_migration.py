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

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from dbos import DBOS
from loguru import logger

from app.core.config import settings
from app.db import engine as db_engine
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
    """Per-module SELECT + row semantics for the legacy → sb:// migration."""

    name: str
    select_sql: str
    extract: Callable[[dict], RowExtract]
    update_row: Callable[[dict, str, Optional[str]], Awaitable[None]]


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

_UPLOADS_SELECT_SQL = """
    SELECT
        rv.id,
        rv.resource_id,
        rv.version_number,
        rv.file_path,
        rv.filename,
        rv.mime_type,
        r.current_version,
        r.filename AS resource_filename,
        r.mime_type AS resource_mime_type,
        ri.scope_id
    FROM resource_versions rv
    JOIN resources r ON r.id = rv.resource_id
    LEFT JOIN LATERAL (
        SELECT scope_id FROM resource_items
        WHERE resource_id = r.id ORDER BY id LIMIT 1
    ) ri ON true
    WHERE rv.file_path IS NOT NULL
      AND rv.file_path NOT LIKE 'sb://%'
      -- Library assets only. source_type='web' rows are the DOWNLOAD
      -- pipeline (global/resources/web/... and the legacy date-bucket
      -- layout) — handled by the sibling ``downloads`` module below, not
      -- here. The first prod dry-run (2026-07-12) pulled them into THIS
      -- module and 70/880 rows failed with IsADirectoryError: some are
      -- album DIRECTORIES, not files, which this module's extract/
      -- update_row never accounted for.
      AND r.source_type IN ('upload', 'generated', 'derived')
      AND (CAST(:scope_id AS bigint) IS NULL OR ri.scope_id = :scope_id)
    ORDER BY rv.id
    LIMIT :limit
"""


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


# Child + parent sync as ONE data-modifying CTE statement → one implicit
# transaction (db_engine has no cross-statement transaction helper; every
# db_engine.execute opens its own engine.begin()). Two separate autocommit
# UPDATEs left a crash window that permanently orphaned the parent's
# file_path: the replayed batch SELECT excludes the already-sb child, so
# the parent would never get synced. ``:sync_parent`` gates the parent leg
# (false → the outer UPDATE matches nothing; the CTE still runs).
_UPLOADS_UPDATE_SQL = """
    WITH v AS (
        UPDATE resource_versions
        SET file_path = :file_path, file_hash = :sha256
        WHERE id = :id
        RETURNING resource_id
    )
    UPDATE resources r
    SET file_path = :file_path, file_hash = :sha256
    FROM v
    WHERE r.id = v.resource_id
      AND CAST(:sync_parent AS boolean)
"""


async def _uploads_update_row(row: dict, file_path: str, sha256: Optional[str]) -> None:
    await db_engine.execute(
        _UPLOADS_UPDATE_SQL,
        {
            "file_path": file_path,
            "sha256": sha256,
            "id": row["id"],
            "sync_parent": row.get("version_number") == row.get("current_version"),
        },
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

_PROJECT_FILES_SELECT_SQL = """
    SELECT
        fv.id,
        fv.file_id,
        fv.version_number,
        fv.file_path,
        fv.filename,
        fv.mime_type,
        pf.current_version,
        pf.project_id,
        pf.filename AS file_filename,
        pf.mime_type AS file_mime_type
    FROM file_versions fv
    JOIN project_files pf ON pf.id = fv.file_id
    WHERE fv.file_path IS NOT NULL
      AND fv.file_path NOT LIKE 'sb://%'
      AND (CAST(:scope_id AS bigint) IS NULL OR pf.project_id = :scope_id)
    ORDER BY fv.id
    LIMIT :limit
"""


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


# Same single-statement CTE atomicity rationale as _UPLOADS_UPDATE_SQL.
_PROJECT_FILES_UPDATE_SQL = """
    WITH v AS (
        UPDATE file_versions
        SET file_path = :file_path
        WHERE id = :id
        RETURNING file_id
    )
    UPDATE project_files pf
    SET file_path = :file_path
    FROM v
    WHERE pf.id = v.file_id
      AND CAST(:sync_parent AS boolean)
"""


async def _project_files_update_row(
    row: dict, file_path: str, sha256: Optional[str]
) -> None:
    await db_engine.execute(
        _PROJECT_FILES_UPDATE_SQL,
        {
            "file_path": file_path,
            "id": row["id"],
            "sync_parent": row.get("version_number") == row.get("current_version"),
        },
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

_DOWNLOADS_SELECT_SQL = """
    SELECT rv.id, rv.resource_id, rv.version_number, rv.file_path,
           rv.filename, rv.mime_type, r.current_version, ri.scope_id,
           pm.id AS parsed_media_id
    FROM resource_versions rv
    JOIN resources r ON r.id = rv.resource_id
    LEFT JOIN LATERAL (
      SELECT scope_id FROM resource_items WHERE resource_id = r.id
      ORDER BY id LIMIT 1
    ) ri ON true
    LEFT JOIN LATERAL (
      SELECT id FROM parsed_media pm2
      WHERE rv.file_path LIKE '%'||pm2.id||'%' LIMIT 1
    ) pm ON true
    WHERE rv.file_path IS NOT NULL
      AND rv.file_path NOT LIKE 'sb://%'
      AND rv.storage_status = 'ok'
      AND r.source_type = 'web'
      AND (CAST(:scope_id AS bigint) IS NULL OR ri.scope_id = :scope_id)
    ORDER BY rv.id LIMIT :limit
"""


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


# Same single-statement CTE atomicity rationale as _UPLOADS_UPDATE_SQL.
# ``sha256`` is None for an album (no single content hash applies — see
# ``_migrate_album_row``), so COALESCE leaves any existing file_hash alone
# instead of clobbering it with NULL.
_DOWNLOADS_UPDATE_SQL = """
    WITH v AS (
        UPDATE resource_versions
        SET file_path = :file_path,
            file_hash = COALESCE(:sha256, file_hash)
        WHERE id = :id
        RETURNING resource_id
    )
    UPDATE resources r
    SET file_path = :file_path,
        file_hash = COALESCE(:sha256, r.file_hash)
    FROM v
    WHERE r.id = v.resource_id
      AND CAST(:sync_parent AS boolean)
"""


async def _downloads_update_row(
    row: dict, file_path: str, sha256: Optional[str]
) -> None:
    await db_engine.execute(
        _DOWNLOADS_UPDATE_SQL,
        {
            "file_path": file_path,
            "sha256": sha256,
            "id": row["id"],
            "sync_parent": row.get("version_number") == row.get("current_version"),
        },
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

_HLS_SELECT_SQL = """
    SELECT rv.id, rv.resource_id, rv.version_number, rv.hls_path, ri.scope_id
    FROM resource_versions rv
    LEFT JOIN LATERAL (
        SELECT scope_id FROM resource_items
        WHERE resource_id = rv.resource_id ORDER BY id LIMIT 1
    ) ri ON true
    WHERE rv.hls_path IS NOT NULL
      AND rv.hls_path NOT LIKE 'sb://%'
      AND rv.storage_status = 'ok'
      AND (CAST(:scope_id AS bigint) IS NULL OR ri.scope_id = :scope_id)
    ORDER BY rv.id
    LIMIT :limit
"""


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


_HLS_UPDATE_SQL = """
    UPDATE resource_versions
    SET hls_path = :file_path
    WHERE id = :id
"""


async def _hls_update_row(row: dict, file_path: str, sha256: Optional[str]) -> None:
    # sha256 is always None for hls (no single content hash for a directory
    # tree) — kept only to match ModuleConfig.update_row's shared signature.
    await db_engine.execute(_HLS_UPDATE_SQL, {"file_path": file_path, "id": row["id"]})


# storyboard: SKIPPED — see module docstring above. Do not add an entry.

_MODULES: dict[str, ModuleConfig] = {
    "uploads": ModuleConfig(
        name="uploads",
        select_sql=_UPLOADS_SELECT_SQL,
        extract=_uploads_extract,
        update_row=_uploads_update_row,
    ),
    "project_files": ModuleConfig(
        name="project_files",
        select_sql=_PROJECT_FILES_SELECT_SQL,
        extract=_project_files_extract,
        update_row=_project_files_update_row,
    ),
    "downloads": ModuleConfig(
        name="downloads",
        select_sql=_DOWNLOADS_SELECT_SQL,
        extract=_downloads_extract,
        update_row=_downloads_update_row,
    ),
    "hls": ModuleConfig(
        name="hls",
        select_sql=_HLS_SELECT_SQL,
        extract=_hls_extract,
        update_row=_hls_update_row,
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

    rows = await db_engine.fetch_all(
        module_cfg.select_sql, {"scope_id": scope_id, "limit": limit}
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
            outcome = await _migrate_row(
                row, module_cfg, dry_run=dry_run, delete_source=delete_source
            )
            counts[outcome] = counts.get(outcome, 0) + 1
        except Exception as e:
            counts["failed"] += 1
            logger.warning(
                f"[storage-migration] module={module} row={row.get('id')} "
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
