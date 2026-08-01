"""Deep S3 existence audit for the migrated library.

Collects every sb:// key from the 7 index columns and probes each with
``store.exists`` in bounded chunks. Results go into the run's task_tracking
metadata (jsonb) — no new table. Route C: phase columns are trigger-owned;
this workflow only ``complete``s with a ``metadata_patch`` for its business
fields and never PATCHes phase/status/progress directly. A storage-call
exception counts as *uncertain* (``errors``), never as missing — network
flaps must not masquerade as broken objects.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from dbos import DBOS
from loguru import logger

from app.db import engine as db_engine

# System-initiated batch job — no single owning end user. Matches the
# existing SYSTEM_RUN_USER_ID convention (app/workflows/storage_migration.py,
# app/api/admin/ai_usage_router.py, app/services/topics/topic_scorer.py).
# task_tracking.user_id is UUID NOT NULL, so this can't be blank.
SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

_PREFIX = "sb://library/"
_CHUNK = 50
_MISSING_CAP = 500

_COLLECT_SQL = """
    SELECT download_path AS key, 'video' AS kind, id AS media_id,
           NULL::bigint AS resource_id
    FROM parsed_media WHERE download_path LIKE 'sb://%'
    UNION ALL
    SELECT cover_download_path, 'cover', id, NULL::bigint
    FROM parsed_media WHERE cover_download_path LIKE 'sb://%'
    UNION ALL
    SELECT thumbnail_path, 'thumbnail', NULL::bigint, id
    FROM resources WHERE thumbnail_path LIKE 'sb://%'
    UNION ALL
    SELECT cover_image_path, 'cover_image', NULL::bigint, id
    FROM resources WHERE cover_image_path LIKE 'sb://%'
    UNION ALL
    SELECT file_path, 'file', NULL::bigint, id
    FROM resources WHERE file_path LIKE 'sb://%'
    UNION ALL
    SELECT hls_path, 'hls', NULL::bigint, resource_id
    FROM resource_versions WHERE hls_path LIKE 'sb://%'
    UNION ALL
    SELECT file_path, 'version_file', NULL::bigint, resource_id
    FROM resource_versions WHERE file_path LIKE 'sb://%'
"""


async def collect_audit_keys_step() -> list[dict]:
    """Fetch every sb:// key across the 7 index columns, strip the
    ``sb://library/`` prefix, and de-dupe (the same object can be
    referenced from more than one column/table)."""
    rows = await db_engine.fetch_all(_COLLECT_SQL)
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        key = str(r["key"])
        if key.startswith(_PREFIX):
            key = key[len(_PREFIX) :]
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "kind": r["kind"],
                "media_id": r.get("media_id"),
                "resource_id": r.get("resource_id"),
            }
        )
    return out


async def _probe_keys(store, rows: list[dict], chunk_size: int = _CHUNK):
    """Probe every row's key with ``store.exists`` in bounded concurrency
    chunks. Missing objects are collected; exceptions are counted as
    ``errors`` (uncertain) and never treated as missing."""
    missing: list[dict] = []
    errors = 0

    async def probe(row):
        nonlocal errors
        try:
            if not await store.exists(row["key"]):
                missing.append(row)
        except Exception as e:  # noqa: BLE001 — uncertain, not missing
            errors += 1
            logger.debug(f"[storage-audit] exists() failed for {row['key']}: {e!r}")

    for i in range(0, len(rows), chunk_size):
        await asyncio.gather(*(probe(r) for r in rows[i : i + chunk_size]))
    return missing, errors


def _cap_missing(missing: list[dict]):
    """Cap the ``missing`` list at ``_MISSING_CAP`` rows so metadata jsonb
    never grows unbounded on a badly-broken library. Returns
    ``(capped_list, truncated_bool)``."""
    if len(missing) > _MISSING_CAP:
        return missing[:_MISSING_CAP], True
    return missing, False


@DBOS.workflow()
async def storage_audit_workflow() -> dict[str, Any]:
    """Deep S3 existence audit — scan every ``sb://`` key referenced by the
    library index tables and probe each with ``store.exists``.

    No parameters: always audits the whole library. Results land in this
    run's ``task_tracking.metadata`` (jsonb); no new table. Route C: never
    PATCH phase/status/progress directly — ``manager.create/start/complete``
    own those columns, this workflow only supplies business decoration via
    ``metadata_patch``.
    """
    from app.services.infra.unified_task_manager import get_task_manager
    from app.services.library.media_storage import library_store

    manager = get_task_manager()
    task_id = DBOS.workflow_id

    try:
        await manager.create(
            user_id=SYSTEM_RUN_USER_ID,
            task_type="storage_audit",
            title="Deep S3 storage audit",
            dbos_workflow_id=task_id,
        )
    except Exception as e:
        logger.warning(f"[storage-audit] create task_tracking failed (non-fatal): {e}")
    try:
        await manager.start(
            task_id, user_id=SYSTEM_RUN_USER_ID, task_type="storage_audit"
        )
    except Exception as e:
        logger.warning(f"[storage-audit] start {task_id}: {e}")

    rows = await collect_audit_keys_step()
    missing, errors = await _probe_keys(library_store(), rows)
    capped, truncated = _cap_missing(
        [
            {
                "key": m["key"],
                "kind": m["kind"],
                "media_id": str(m["media_id"]) if m.get("media_id") else None,
                "resource_id": str(m["resource_id"]) if m.get("resource_id") else None,
            }
            for m in missing
        ]
    )
    result: dict[str, Any] = {
        "kind": "storage_audit",
        "scanned": len(rows),
        "errors": errors,
        "missing": capped,
        "missing_truncated": truncated,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        await manager.complete(
            task_id,
            subtitle=f"{len(missing)} missing, {errors} errors",
            metadata_patch=result,
        )
    except Exception as e:
        logger.warning(f"[storage-audit] complete {task_id}: {e}")

    logger.info(
        f"[storage-audit] scanned={len(rows)} missing={len(missing)} errors={errors}"
    )
    return {"scanned": len(rows), "missing": len(missing), "errors": errors}
