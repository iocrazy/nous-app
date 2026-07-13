"""storage_migration DBOS workflow — legacy filesystem → Supabase Storage.

Task 4.1 of the storage-unification epic (PR-4). Moves existing files that
still live on the local filesystem into the ``library`` object-store bucket,
row by row, for one module at a time (``uploads`` / ``project_files``).

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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from dbos import DBOS
from loguru import logger

from app.core.config import settings
from app.db import engine as db_engine
from app.services.library import media_storage

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
      AND (:scope_id::bigint IS NULL OR ri.scope_id = :scope_id)
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


async def _uploads_update_row(row: dict, file_path: str, sha256: Optional[str]) -> None:
    await db_engine.execute(
        "UPDATE resource_versions SET file_path = :file_path, file_hash = :sha256 "
        "WHERE id = :id",
        {"file_path": file_path, "sha256": sha256, "id": row["id"]},
    )
    if row.get("version_number") == row.get("current_version"):
        await db_engine.execute(
            "UPDATE resources SET file_path = :file_path, file_hash = :sha256 "
            "WHERE id = :id",
            {"file_path": file_path, "sha256": sha256, "id": row["resource_id"]},
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
      AND (:scope_id::bigint IS NULL OR pf.project_id = :scope_id)
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


async def _project_files_update_row(
    row: dict, file_path: str, sha256: Optional[str]
) -> None:
    await db_engine.execute(
        "UPDATE file_versions SET file_path = :file_path WHERE id = :id",
        {"file_path": file_path, "id": row["id"]},
    )
    if row.get("version_number") == row.get("current_version"):
        await db_engine.execute(
            "UPDATE project_files SET file_path = :file_path WHERE id = :id",
            {"file_path": file_path, "id": row["file_id"]},
        )


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
    file_path = row["file_path"]
    loc = media_storage.resolve_media_source(file_path)
    if loc.is_object_store:
        return "skipped"  # already migrated — idempotent replay

    local = Path(settings.DOWNLOAD_PATH) / (loc.rel_path or "")
    if not local.exists():
        logger.warning(f"[storage-migration] missing local file, skip: {file_path}")
        return "missing"

    extract = module_cfg.extract(row)
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
