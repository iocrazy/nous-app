"""Deep S3 existence audit for the migrated library.

Collects every sb:// key from the 11 index columns and probes each in bounded
concurrency chunks. Results go into the run's task_tracking metadata (jsonb)
— no new table. Route C: phase columns are trigger-owned; this workflow only
``complete``s with a ``metadata_patch`` for its business fields and never
PATCHes phase/status/progress directly.

Probing deliberately does NOT use ``ObjectStore.exists()``: that method
(``media_storage.py::ObjectStore.exists``) collapses every exception —
a real 404, a timeout, a connection failure, a 5xx — into a bare ``False``,
so it cannot tell "confirmed gone" from "storage had a hiccup". Calling it
here would silently misreport every network flap as a broken object. Instead
``_probe_one`` calls ``store.get_size()`` directly and classifies the
outcome itself: an HTTP 404 means the object is genuinely missing; any other
exception (timeout, connection error, 5xx, ...) is *uncertain* and counted
as ``errors``, never as missing.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any

import httpx
from dbos import DBOS
from loguru import logger
from sqlalchemy import BigInteger, cast, literal, null, select, union_all

from app.db.scope import is_enforced, system_request_scope
from app.db.session import read_scope
from app.models import (
    FileVersions,
    ParsedMedia,
    ProjectFiles,
    Resources,
    ResourceVersions,
)

# System-initiated batch job — no single owning end user. Matches the
# existing SYSTEM_RUN_USER_ID convention (app/workflows/storage_migration.py,
# app/api/admin/ai_usage_router.py, app/services/topics/topic_scorer.py).
# task_tracking.user_id is UUID NOT NULL, so this can't be blank.
SYSTEM_RUN_USER_ID = "00000000-0000-0000-0000-000000000000"

_PREFIX = "sb://library/"
_CHUNK = 50
_MISSING_CAP = 500

# Same 11 index columns as ``app.services.library.object_gc``'s reference
# query (``_PARSED_MEDIA_COLS`` / ``_RESOURCES_COLS`` / `_RESOURCE_VERSIONS_COLS``
# / ``_PROJECT_FILES_COLS`` / ``_FILE_VERSIONS_COLS``) — keep both lists in
# sync; a column added to one and not the other lets reference-safe deletion
# either leak an object or (less likely but still a drift) miss a live
# reference.
#
# I3: project_files / file_versions (projects_service.upload_file /
# upload_new_version) write into the SAME library bucket with the SAME
# content-addressed scheme as a resource upload (both resolve scope_id to the
# owning team's snowflake), so a byte-identical upload to a project and to
# that team's resource library can produce one object referenced from two
# tables neither object_gc nor this audit previously scanned.
#
# Phase C task 2: migrated off the hand-built UNION ALL SQL string to a real
# ORM ``union_all()`` of per-column ``select()`` arms — same 11 (column,
# table) pairs as before, each arm projecting (key, kind, media_id,
# resource_id) with consistent types across the union (BigInteger for the two
# id columns, a ``CAST(NULL AS bigint)`` for the arms with no resource_id).
_NULL_RESOURCE_ID = cast(null(), BigInteger)

_COLLECT_ARMS = [
    select(
        ParsedMedia.download_path.label("key"),
        literal("video").label("kind"),
        ParsedMedia.id.label("media_id"),
        _NULL_RESOURCE_ID.label("resource_id"),
    ).where(ParsedMedia.download_path.like("sb://%")),
    select(
        ParsedMedia.cover_download_path.label("key"),
        literal("cover").label("kind"),
        ParsedMedia.id.label("media_id"),
        _NULL_RESOURCE_ID.label("resource_id"),
    ).where(ParsedMedia.cover_download_path.like("sb://%")),
    select(
        ParsedMedia.music_download_path.label("key"),
        literal("music").label("kind"),
        ParsedMedia.id.label("media_id"),
        _NULL_RESOURCE_ID.label("resource_id"),
    ).where(ParsedMedia.music_download_path.like("sb://%")),
    select(
        ParsedMedia.extract_audio_path.label("key"),
        literal("extract_audio").label("kind"),
        ParsedMedia.id.label("media_id"),
        _NULL_RESOURCE_ID.label("resource_id"),
    ).where(ParsedMedia.extract_audio_path.like("sb://%")),
    select(
        Resources.thumbnail_path.label("key"),
        literal("thumbnail").label("kind"),
        Resources.media_id.label("media_id"),
        Resources.id.label("resource_id"),
    ).where(Resources.thumbnail_path.like("sb://%")),
    select(
        Resources.cover_image_path.label("key"),
        literal("cover_image").label("kind"),
        Resources.media_id.label("media_id"),
        Resources.id.label("resource_id"),
    ).where(Resources.cover_image_path.like("sb://%")),
    select(
        Resources.file_path.label("key"),
        literal("file").label("kind"),
        Resources.media_id.label("media_id"),
        Resources.id.label("resource_id"),
    ).where(Resources.file_path.like("sb://%")),
    select(
        ResourceVersions.hls_path.label("key"),
        literal("hls").label("kind"),
        Resources.media_id.label("media_id"),
        ResourceVersions.resource_id.label("resource_id"),
    )
    .join(Resources, Resources.id == ResourceVersions.resource_id)
    .where(ResourceVersions.hls_path.like("sb://%")),
    select(
        ResourceVersions.file_path.label("key"),
        literal("version_file").label("kind"),
        Resources.media_id.label("media_id"),
        ResourceVersions.resource_id.label("resource_id"),
    )
    .join(Resources, Resources.id == ResourceVersions.resource_id)
    .where(ResourceVersions.file_path.like("sb://%")),
    select(
        ProjectFiles.file_path.label("key"),
        literal("project_file").label("kind"),
        ProjectFiles.media_id.label("media_id"),
        ProjectFiles.id.label("resource_id"),
    ).where(ProjectFiles.file_path.like("sb://%")),
    select(
        FileVersions.file_path.label("key"),
        literal("project_file_version").label("kind"),
        ProjectFiles.media_id.label("media_id"),
        FileVersions.file_id.label("resource_id"),
    )
    .join(ProjectFiles, ProjectFiles.id == FileVersions.file_id)
    .where(FileVersions.file_path.like("sb://%")),
]

_COLLECT_STMT = union_all(*_COLLECT_ARMS)


async def collect_audit_keys_step() -> list[dict]:
    """Fetch every sb:// key across the 11 index columns, strip the
    ``sb://library/`` prefix, and de-dupe (the same object can be
    referenced from more than one column/table).

    Full-library scan by design — system scope, unconditionally correct
    regardless of any ambient caller scope (a per-user read would silently
    miss every other user's sb:// keys, exactly the false-missing report a
    storage audit must never produce). Gated on ``is_enforced("resources")``
    to stay byte-for-byte legacy (no wrap, no audit log) while enforcement is
    off.
    """
    scope_cm = (
        system_request_scope(
            reason="storage-audit-full-library-scan: audits every user's "
            "sb:// keys, must not be filtered to any single tenant"
        )
        if is_enforced("resources")
        else nullcontext()
    )
    async with scope_cm:
        async with read_scope() as session:
            rows = (await session.execute(_COLLECT_STMT)).mappings().all()

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


async def _probe_one(store, key: str) -> str:
    """Probe a single key. Returns ``"present"`` / ``"missing"`` / ``"error"``.

    Two shapes: a plain object key, or an ALBUM PREFIX (key ends in "/",
    e.g. ``t{scope}/album/{rid}/`` — the carousel/album layout has no single
    file to address). ``get_size`` is a HEAD on exactly one key, so pointing
    it at a prefix always 404s — the deep scan was counting every one of the
    library's album prefixes as ``missing`` (I4: one prior run's ``errors``
    count matched the library's prefix-shaped key count exactly). A prefix is
    probed instead via ``list_prefix``: any object found under it → present;
    none → missing; the listing call itself raising → error (uncertain, same
    as a plain-key network failure below — never counted as missing).

    For a plain key: calls ``store.get_size(key)`` directly (the HEAD-shaped
    primitive ``ObjectStore.exists()`` wraps) so the exception itself can be
    inspected rather than swallowed. Only a genuine HTTP 404
    (``httpx.HTTPStatusError`` with ``response.status_code == 404``) counts
    as ``"missing"`` — a 404 is the storage server explicitly saying the
    object doesn't exist. Every other failure (timeout, connection error,
    5xx, ...) is uncertain and classified ``"error"``: a network flap must
    never masquerade as proof the object is gone.
    """
    if key.endswith("/"):
        try:
            keys = await store.list_prefix(key)
            return "present" if keys else "missing"
        except Exception as e:  # noqa: BLE001 — uncertain, not missing
            logger.debug(f"[storage-audit] list_prefix({key!r}) failed: {e!r}")
            return "error"

    try:
        await store.get_size(key)
        return "present"
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            return "missing"
        logger.debug(
            f"[storage-audit] get_size({key!r}) HTTP {e.response.status_code} "
            "— uncertain, counted as error"
        )
        return "error"
    except Exception as e:  # noqa: BLE001 — uncertain, not missing
        logger.debug(f"[storage-audit] get_size({key!r}) failed: {e!r}")
        return "error"


async def _probe_keys(store, rows: list[dict], chunk_size: int = _CHUNK):
    """Probe every row's key in bounded concurrency chunks. Missing objects
    are collected; uncertain failures are counted as ``errors`` and never
    treated as missing (see ``_probe_one``)."""
    missing: list[dict] = []
    errors = 0

    async def probe(row):
        nonlocal errors
        outcome = await _probe_one(store, row["key"])
        if outcome == "missing":
            missing.append(row)
        elif outcome == "error":
            errors += 1

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
    library index tables and probe each via ``_probe_one`` (HTTP 404 =
    missing; any other failure = uncertain ``errors``, never missing).

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

    # Route C: this is the workflow's ONLY product (the scan result lives
    # solely in task_tracking.metadata — no other table holds it). A failed
    # complete() here must NOT be swallowed: if it were, the workflow would
    # still return normally and DBOS would mark it SUCCESS, leaving
    # phase=completed with empty metadata — a silent "Broken 0" false-green
    # in the UI. Let it raise so DBOS marks the run ERROR instead.
    await manager.complete(
        task_id,
        subtitle=f"{len(missing)} missing, {errors} errors",
        metadata_patch=result,
    )

    logger.info(
        f"[storage-audit] scanned={len(rows)} missing={len(missing)} errors={errors}"
    )
    return {"scanned": len(rows), "missing": len(missing), "errors": errors}
