"""Admin storage observability — read-only endpoints over the S3-migrated
library. DB is the index (7 sb:// columns); this router exposes stats, per-row
storage status for the media table, and per-media asset detail. Deep S3
verification lives in Task 3 (same file). Read logic is module-level
``_fetch_*`` functions so tests hit them without HTTP/auth plumbing."""

from __future__ import annotations

from contextlib import nullcontext

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import and_, func, or_, select, true

from app.core.admin_deps import AdminAuthDep
from app.db.scope import is_enforced, system_request_scope
from app.db.session import read_scope
from app.models import ParsedMedia, ResourceItems, Resources, ResourceVersions
from app.utils.admin_helpers import create_audit_log

router = APIRouter()


def _fs_cond(col):
    """ORM equivalent of the legacy ``(col IS NOT NULL AND col LIKE '%/%'
    AND col NOT LIKE 'sb://%')`` fs-residue predicate — a path that still
    looks like a filesystem path (has a ``/``) but is not an ``sb://`` key."""
    return and_(col.isnot(None), col.like("%/%"), col.notlike("sb://%"))


def _resource_lateral():
    """LATERAL pick of ONE resources row per parsed_media (deterministic via
    ORDER BY id LIMIT 1). ``resources.media_id`` is NOT unique in prod
    (verified: 2 media_id values have 2 matching resources rows each) — a
    plain JOIN would fan a media row out into >1 row for those ids, and
    ``_fetch_media_status`` would silently drop all but the last one when the
    frontend collapses by media_id, potentially hiding a real fs_residue row
    behind a later ok-looking one."""
    return (
        select(
            Resources.id,
            Resources.thumbnail_path,
            Resources.cover_image_path,
            Resources.file_path,
        )
        .where(Resources.media_id == ParsedMedia.id)
        .order_by(Resources.id)
        .limit(1)
        .lateral("r")
    )


def _resource_version_lateral(r_lateral):
    """LATERAL pick of the newest resource_versions row for the resource
    picked above (ORDER BY id DESC LIMIT 1) — same deterministic-pick
    convention as ``_resource_lateral``."""
    return (
        select(ResourceVersions.hls_path, ResourceVersions.file_path)
        .where(ResourceVersions.resource_id == r_lateral.c.id)
        .order_by(ResourceVersions.id.desc())
        .limit(1)
        .lateral("rv")
    )


def _pm_r_rv_lateral() -> tuple:
    """Fresh (r, rv) LATERAL pair shared by all three storage-observability
    reads below — Phase C task 2 ORM port of the former ``_JOINS`` raw-SQL
    string. Built fresh per call (never module-cached) since each caller
    embeds them in its own top-level statement."""
    r = _resource_lateral()
    rv = _resource_version_lateral(r)
    return r, rv


def _resources_scope_cm(reason: str):
    """``system_request_scope``, gated on ``is_enforced("resources")``.

    Every read below LEFT JOINs Resources (a scoped model) so an unmatched
    parsed_media row (no resource created yet) is preserved — the choke
    point treats an OUTER JOIN target as non-filtering (the tenant predicate
    would land in the row-preserving ON clause, not a real filter) and
    fail-closed RAISEs under a real user ``Scope``, so these admin-only
    cross-tenant reads must run as SYSTEM. Admin authorization is already
    enforced at the route layer (``AdminAuthDep``) — this is a global
    storage-observability view by design, not a per-user one. Gated so the
    flag-off path stays byte-for-byte legacy (no wrap, no audit log)."""
    return (
        system_request_scope(reason=reason)
        if is_enforced("resources")
        else nullcontext()
    )


async def _fetch_latest_audit() -> dict:
    """Most recent storage_audit run, read from task_tracking (route C: never
    query dbos.workflow_status). Returns the audit dict the /audit endpoint
    serves; status 'none' when no scan has ever run."""
    from sqlalchemy import select

    from app.db.session import read_scope
    from app.models import TaskTracking

    async with read_scope() as session:
        row = (
            (
                await session.execute(
                    select(
                        TaskTracking.phase,
                        # Label back to the DB column name ("metadata"): the
                        # ORM attribute is metadata_ (reserved-name collision
                        # avoidance) and would otherwise change the mapping
                        # key downstream code reads via row.get("metadata").
                        TaskTracking.metadata_.label("metadata"),
                        TaskTracking.completed_at,
                    )
                    .where(TaskTracking.task_type == "storage_audit")
                    .order_by(TaskTracking.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    if not row:
        return {
            "status": "none",
            "scanned": 0,
            "errors": 0,
            "scanned_at": None,
            "missing": [],
        }
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        import json

        meta = json.loads(meta or "{}")
    return {
        "status": row.get("phase") or "queued",
        "scanned": int(meta.get("scanned") or 0),
        "errors": int(meta.get("errors") or 0),
        "scanned_at": meta.get("scanned_at"),
        "missing": meta.get("missing") or [],
        "missing_truncated": bool(meta.get("missing_truncated")),
    }


async def _fetch_stats() -> dict:
    async with _resources_scope_cm("admin-storage-stats: cross-tenant by design"):
        async with read_scope() as session:
            videos = (
                (
                    await session.execute(
                        select(
                            func.count().label("count"),
                            func.coalesce(func.sum(ParsedMedia.storage_size), 0).label(
                                "size_bytes"
                            ),
                        ).where(ParsedMedia.download_path.like("sb://%"))
                    )
                )
                .mappings()
                .first()
            )

            r, rv = _pm_r_rv_lateral()
            fs_where = or_(
                _fs_cond(ParsedMedia.download_path),
                _fs_cond(ParsedMedia.cover_download_path),
                _fs_cond(ParsedMedia.music_download_path),
                _fs_cond(ParsedMedia.extract_audio_path),
                _fs_cond(r.c.thumbnail_path),
                _fs_cond(r.c.cover_image_path),
                _fs_cond(r.c.file_path),
                _fs_cond(rv.c.hls_path),
                _fs_cond(rv.c.file_path),
            )
            fs = (
                (
                    await session.execute(
                        select(func.count().label("n"))
                        .select_from(ParsedMedia)
                        .join(r, true(), isouter=True)
                        .join(rv, true(), isouter=True)
                        .where(fs_where)
                    )
                )
                .mappings()
                .first()
            )

            orphans = (
                (
                    await session.execute(
                        select(func.count().label("n"))
                        .select_from(ParsedMedia)
                        .join(
                            Resources,
                            Resources.media_id == ParsedMedia.id,
                            isouter=True,
                        )
                        .where(Resources.id.is_(None))
                    )
                )
                .mappings()
                .first()
            )

            hls = (
                (
                    await session.execute(
                        select(func.count().label("n")).where(
                            ResourceVersions.hls_path.like("sb://%")
                        )
                    )
                )
                .mappings()
                .first()
            )

    audit = await _fetch_latest_audit()
    return {
        "videos": {
            "count": int(videos["count"]),
            "size_bytes": int(videos["size_bytes"]),
        },
        "fs_residue": int(fs["n"]),
        "orphans": int(orphans["n"]),
        "hls_ready": int(hls["n"]),
        "broken": len(audit["missing"]) if audit["status"] == "completed" else None,
        "last_scan": (
            {
                "at": audit["scanned_at"],
                "scanned": audit["scanned"],
                "missing": len(audit["missing"]),
                "errors": audit["errors"],
            }
            if audit["status"] == "completed"
            else None
        ),
    }


def _is_sb(v) -> bool:
    return bool(v) and str(v).startswith("sb://")


def _is_fs(v) -> bool:
    return bool(v) and "/" in str(v) and not str(v).startswith("sb://")


async def _fetch_media_status(media_ids: list[int]) -> list[dict]:
    async with _resources_scope_cm(
        "admin-storage-media-status: cross-tenant by design"
    ):
        async with read_scope() as session:
            r, rv = _pm_r_rv_lateral()
            ri = (
                select(ResourceItems.scope_id)
                .where(ResourceItems.resource_id == r.c.id)
                .order_by(ResourceItems.id)
                .limit(1)
                .lateral("ri")
            )
            stmt = (
                select(
                    ParsedMedia.id.label("media_id"),
                    ParsedMedia.download_path.label("video_key"),
                    ParsedMedia.storage_size.label("video_size"),
                    ParsedMedia.video_download_status,
                    ParsedMedia.cover_download_path.label("cover_path"),
                    r.c.thumbnail_path,
                    r.c.cover_image_path.label("res_cover_image"),
                    r.c.file_path.label("res_file_path"),
                    rv.c.hls_path,
                    rv.c.file_path.label("rv_file_path"),
                    ri.c.scope_id,
                )
                .select_from(ParsedMedia)
                .join(r, true(), isouter=True)
                .join(rv, true(), isouter=True)
                .join(ri, true(), isouter=True)
                .where(ParsedMedia.id.in_(media_ids))
            )
            rows = (await session.execute(stmt)).mappings().all()
    out = []
    for row in rows:
        fs = any(
            _is_fs(row.get(c))
            for c in (
                "video_key",
                "cover_path",
                "thumbnail_path",
                "res_cover_image",
                "res_file_path",
                "hls_path",
                "rv_file_path",
            )
        )
        if fs:
            st = "fs_residue"
        elif not row.get("video_key") or row.get("video_download_status") == "failed":
            st = "no_video"
        else:
            st = "ok"
        out.append(
            {
                "media_id": str(row["media_id"]),
                "video_key": row.get("video_key"),
                "video_size": row.get("video_size"),
                "cover_ok": _is_sb(row.get("cover_path")),
                "thumbnail_ok": _is_sb(row.get("thumbnail_path")),
                "hls_ok": _is_sb(row.get("hls_path")),
                "storage_status": st,
                "scope_id": (
                    str(row["scope_id"]) if row.get("scope_id") is not None else None
                ),
            }
        )
    return out


async def _fetch_media_detail(media_id: int) -> dict:
    async with _resources_scope_cm(
        "admin-storage-media-detail: cross-tenant by design"
    ):
        async with read_scope() as session:
            r, rv = _pm_r_rv_lateral()
            scope_id_subq = (
                select(ResourceItems.scope_id)
                .where(ResourceItems.resource_id == r.c.id)
                .order_by(ResourceItems.id)
                .limit(1)
                .scalar_subquery()
            )
            stmt = (
                select(
                    ParsedMedia.id.label("media_id"),
                    ParsedMedia.download_path,
                    ParsedMedia.storage_size,
                    ParsedMedia.cover_download_path,
                    r.c.id.label("resource_id"),
                    r.c.thumbnail_path,
                    rv.c.hls_path,
                    scope_id_subq.label("scope_id"),
                )
                .select_from(ParsedMedia)
                .join(r, true(), isouter=True)
                .join(rv, true(), isouter=True)
                .where(ParsedMedia.id == media_id)
            )
            row = (await session.execute(stmt)).mappings().first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Media not found"
        )
    rid = row.get("resource_id")
    sprite_key = f"sb://library/derived/{rid}/preview_sprite.jpg" if rid else None
    assets = [
        {
            "kind": "video",
            "key": row.get("download_path"),
            "size_bytes": row.get("storage_size"),
            "present_in_db": _is_sb(row.get("download_path")),
        },
        {
            "kind": "cover",
            "key": row.get("cover_download_path"),
            "size_bytes": None,
            "present_in_db": _is_sb(row.get("cover_download_path")),
        },
        {
            "kind": "thumbnail",
            "key": row.get("thumbnail_path"),
            "size_bytes": None,
            "present_in_db": _is_sb(row.get("thumbnail_path")),
        },
        # sprite has no DB column anywhere — key is the serve-side convention
        {
            "kind": "sprite",
            "key": sprite_key,
            "size_bytes": None,
            "present_in_db": False,
        },
        {
            "kind": "hls",
            "key": row.get("hls_path"),
            "size_bytes": None,
            "present_in_db": _is_sb(row.get("hls_path")),
        },
    ]
    return {
        "assets": assets,
        "scope_id": str(row["scope_id"]) if row.get("scope_id") is not None else None,
    }


@router.get("/stats")
async def get_storage_stats(auth: AdminAuthDep):
    return await _fetch_stats()


@router.get("/media-status")
async def get_media_status(auth: AdminAuthDep, media_ids: str):
    try:
        ids = [int(x) for x in media_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(
            status_code=422, detail="media_ids must be comma-separated ints"
        )
    if not ids or len(ids) > 200:
        raise HTTPException(status_code=422, detail="media_ids must contain 1-200 ids")
    return {"rows": await _fetch_media_status(ids)}


@router.get("/media/{media_id}/detail")
async def get_media_storage_detail(auth: AdminAuthDep, media_id: int):
    return await _fetch_media_detail(media_id)


_SB_PREFIX = "sb://library/"


async def _verify_keys(store, assets: list[dict]) -> list[dict]:
    """Probe each asset's key, reusing storage_audit's own classifier
    (``_probe_one``) rather than ``ObjectStore.exists()`` — that method
    collapses every exception (a real 404, a timeout, a 5xx) into a bare
    ``False``, so it cannot tell "confirmed gone" from "storage had a
    hiccup" (see storage_audit.py module docstring). Mapping:
    present -> exists=True, missing -> exists=False,
    error (uncertain) -> exists=None. Never conflate missing with error."""
    from app.workflows.storage_audit import _probe_one

    out = []
    for a in assets:
        key = a.get("key")
        if not key:
            continue
        bare = key[len(_SB_PREFIX) :] if key.startswith(_SB_PREFIX) else key
        outcome = await _probe_one(store, bare)
        exists = {"present": True, "missing": False, "error": None}[outcome]
        out.append({"kind": a["kind"], "key": key, "exists": exists})
    return out


async def _find_running_audit() -> str | None:
    """Most recent still-running storage_audit workflow id, if any — used
    to dedup /verify dispatch so an admin double-click doesn't spawn a
    second full-library scan. Column is ``dbos_workflow_id`` (task_tracking's
    actual PK per app/models/ops.py::TaskTracking), not ``task_id``.

    Bounded to the last 2 hours: without a time cap, a run stuck in
    queued/in_progress (crashed worker, DBOS recovery failure) would dedup
    every future /verify dispatch forever — a full-library scan realistically
    finishes in minutes, so anything older than 2h is presumed dead and
    should self-heal rather than block new dispatches indefinitely."""
    from sqlalchemy import func, select, text

    from app.db.session import read_scope
    from app.models import TaskTracking

    async with read_scope() as session:
        row = (
            (
                await session.execute(
                    select(TaskTracking.dbos_workflow_id)
                    .where(TaskTracking.task_type == "storage_audit")
                    .where(TaskTracking.phase.in_(["queued", "in_progress"]))
                    # INTERVAL literal via a text() fragment (structural
                    # exception, non-full-statement) — no ORM interval type.
                    .where(
                        TaskTracking.created_at
                        > func.now() - text("interval '2 hours'")
                    )
                    .order_by(TaskTracking.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
    return row["dbos_workflow_id"] if row else None


@router.post("/media/{media_id}/verify")
async def verify_media_on_s3(auth: AdminAuthDep, media_id: int):
    """Synchronous single-media S3 existence check — probes every asset key
    from ``_fetch_media_detail`` right now (no workflow dispatch)."""
    from app.services.library.media_storage import library_store

    detail = await _fetch_media_detail(media_id)
    return {"results": await _verify_keys(library_store(), detail["assets"])}


@router.post("/verify")
async def dispatch_deep_verify(auth: AdminAuthDep, request: Request):
    """Dispatch a full-library storage_audit workflow run, deduped against
    any run still queued/in_progress.

    Pre-creates the task_tracking row BEFORE dispatching the workflow (I5
    fix). Fact established while fixing this: ``start_workflow_routed``
    (app/services/infra/dbos_orchestrator.py) does NOT create any
    task_tracking row itself — it only calls ``DBOS.start_workflow(...)``
    (or the dormant DBOSClient.enqueue path) and returns immediately with
    ``handle.workflow_id``; the workflow body executes concurrently in the
    background. ``storage_audit_workflow`` is the one that calls
    ``manager.create()``, and it does so from INSIDE the workflow body —
    so there is a real race: this endpoint could return to the frontend
    before that INSERT has landed, and an immediate ``GET /audit`` poll
    would see no row yet (no queued/in_progress task to poll), so the UI's
    polling loop never starts and the Deep Verify button springs back.

    Fix: mint the workflow id here, INSERT the task_tracking row
    ourselves via ``manager.create()`` (phase=QUEUED, route-C compliant —
    it's the sanctioned manager API, not a raw phase PATCH), and only THEN
    dispatch the workflow with that same id via ``workflow_id=``. By the
    time this endpoint returns, the row is guaranteed to exist. The
    workflow body's own ``manager.create()`` call will hit the
    already-existing row (INTEGRITY / duplicate PK on dbos_workflow_id);
    that call is already wrapped in try/except in storage_audit.py
    (non-fatal, logs a warning) — so a workflow replay/re-run never
    crashes on the duplicate insert.
    """
    running = await _find_running_audit()
    if running:
        return {"workflow_id": running, "already_running": True}

    import uuid

    from app.services.infra.dbos_orchestrator import start_workflow_routed
    from app.services.infra.unified_task_manager import get_task_manager
    from app.workflows.storage_audit import storage_audit_workflow

    workflow_id = str(uuid.uuid4())
    manager = get_task_manager()
    # user_id MUST be the dispatching admin, NOT SYSTEM_RUN_USER_ID:
    # task_tracking.user_id has an FK to auth.users (unified_tasks_user_id_fkey)
    # and the all-zeros system UUID has no auth.users row, so a system-id
    # insert raises ForeignKeyViolation and the whole dispatch 500s (found in
    # prod smoke 2026-07-31; storage_migration's identical in-workflow create
    # has been failing the same way for weeks — just silently, because that
    # call is wrapped in a non-fatal try/except).
    await manager.create(
        user_id=auth.user_id,
        task_type="storage_audit",
        title="Deep S3 storage audit",
        dbos_workflow_id=workflow_id,
    )

    result = await start_workflow_routed(
        "storage_audit",
        dbos_workflow_callable=storage_audit_workflow,
        dbos_workflow_kwargs={},
        workflow_id=workflow_id,
    )

    await create_audit_log(
        admin_id=auth.user_id,
        action="storage_audit_dispatch",
        target_type="workflow",
        target_id=result["dbos_workflow_id"],
        details={"workflow_id": result["dbos_workflow_id"]},
        ip_address=request.client.host if request.client else None,
    )

    return {"workflow_id": result["dbos_workflow_id"], "already_running": False}


@router.get("/audit")
async def get_storage_audit(auth: AdminAuthDep):
    return await _fetch_latest_audit()
