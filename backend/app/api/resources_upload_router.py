# backend/app/api/resources_upload_router.py

"""
Resources Upload Router

Upload, duplicate detection, link-existing, and permission endpoints.
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from loguru import logger

from app.core.deps import AuthDep
from app.core.scope_dep import scoped_request
from app.core.scope_guards import verify_scope_access
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.resources_batch import CheckDuplicatesRequest, CheckDuplicatesResponse
from app.services.library.permission_service import PermissionService
from app.services.library.resources_service import ResourcesService

# All endpoints require auth (AuthDep) and are resources-dedicated → establish
# the ambient tenant Scope at the ROUTER level. Inert until SCOPE_ENFORCE_RESOURCES.
router = APIRouter(prefix="/resources", dependencies=[Depends(scoped_request)])

MAX_UPLOAD_SIZE = 500 * 1024 * 1024  # 500 MB


# ============================================
# Permission endpoints
# ============================================


@router.get("/permissions")
async def get_effective_permissions(
    auth: AuthDep,
    object_type: str = Query(..., pattern="^(folder|library|resource|project)$"),
    object_id: str = Query(...),
    team_id: str = Query(...),
):
    """Get the effective role and capabilities for the current user on an object."""
    try:
        svc = PermissionService()
        result = await svc.get_effective_role(
            user_id=auth.user_id,
            object_type=object_type,
            object_id=object_id,
            team_id=team_id,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"Failed to get permissions: {e}")
        raise HTTPException(status_code=500, detail="Failed to get permissions")


# ============================================
# Duplicate detection endpoints
# ============================================


@router.get("/check-duplicate")
async def check_duplicate(
    auth: AuthDep,
    file_hash: str = Query(..., min_length=64, max_length=64),
    file_size: int = Query(..., gt=0),
):
    """Check if a file with the same hash already exists."""
    try:
        repo = ResourcesRepository()
        matches = await repo.find_by_hash(file_hash, auth.user_id)
        # Secondary file_size check to guard against hash collisions
        exact = [m for m in matches if m.get("file_size_bytes") == file_size]
        return {
            "duplicate": len(exact) > 0,
            "existing": exact[0] if exact else None,
        }
    except Exception as e:
        logger.error(f"Failed to check duplicate: {e}")
        raise HTTPException(status_code=500, detail="Failed to check duplicate")


_MAX_BATCH_DEDUP = 100
# 100 × 64-char SHA-256 ≈ 6.5 KB URI — safely below the self-hosted
# Supabase nginx/kong large_client_header_buffers default of 8 KB.
# Client must chunk to this size; keep in sync with frontend checkChunk.


@router.post("/check-duplicates")
async def check_duplicates_batch(
    body: CheckDuplicatesRequest,
    auth: AuthDep,
) -> CheckDuplicatesResponse:
    """Batch duplicate check for bulk import.

    Accepts up to 100 file hashes/sizes in one request and returns one
    result per input item (order preserved).  ``duplicate=True`` only when
    a row with the same hash *and* the same ``file_size_bytes`` exists for
    the authenticated user (collision guard identical to the single endpoint).
    """
    if len(body.items) > _MAX_BATCH_DEDUP:
        raise HTTPException(
            status_code=422,
            detail=f"Too many items: {len(body.items)} > {_MAX_BATCH_DEDUP}",
        )

    try:
        repo = ResourcesRepository()
        file_hashes = [item.file_hash for item in body.items]
        hash_map = await repo.find_by_hashes(file_hashes, auth.user_id)

        results = []
        for item in body.items:
            row = hash_map.get(item.file_hash)
            if row and row.get("file_size_bytes") == item.file_size:
                results.append(
                    {"file_hash": item.file_hash, "duplicate": True, "existing": row}
                )
            else:
                results.append(
                    {"file_hash": item.file_hash, "duplicate": False, "existing": None}
                )

        return CheckDuplicatesResponse(results=results)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to batch check duplicates: {}", e)
        raise HTTPException(status_code=500, detail="Failed to check duplicates")


@router.post("/link-existing")
async def link_existing_resource(
    auth: AuthDep,
    resource_id: str = Query(...),
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
    library_id: Optional[str] = Query(None),
):
    """Link an existing resource to the current scope/folder (use existing)."""
    try:
        repo = ResourcesRepository()
        resource = await repo.get_resource_by_id(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Resource not found")

        # PR-E 4b: no longer write resource_items.scope_type (nullable post
        # mig 240, dropped in 4c); scope_id alone locates the scope.
        existing_item = await repo.find_resource_item(
            resource_id, None, scope_id, folder_id
        )
        if existing_item:
            return {"success": True, "data": resource, "already_linked": True}

        item_data = {
            "resource_id": resource_id,
            "scope_id": scope_id,
            "folder_id": folder_id,
            "library_id": library_id,
            "added_by": auth.user_id,
        }
        await repo.create_resource_item(item_data)
        return {"success": True, "data": resource}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to link existing resource: {e}")
        raise HTTPException(status_code=500, detail="Failed to link existing resource")


# ============================================
# Upload endpoint
# ============================================


@router.post("/upload")
async def upload_resource(
    auth: AuthDep,
    scope_id: str = Query(...),
    folder_id: Optional[str] = Query(None),
    library_id: Optional[str] = Query(None),
    file: UploadFile = File(...),
    _scope_guard: None = Depends(verify_scope_access),
):
    """Upload a file to the resource library.

    `_scope_guard` enforces that the caller owns `scope_id` (personal scope)
    or is a member of it (team scope) before the handler runs.
    """
    from app.services.infra.unified_task_manager import get_task_manager

    tracker = get_task_manager()
    unified_task_id = None

    try:
        if file.size and file.size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413, detail="File too large. Maximum size is 500 MB."
            )

        # Create unified task for upload tracking
        try:
            unified_task_id = await tracker.create(
                user_id=auth.user_id,
                task_type="upload",
                title=f"Upload {file.filename or 'file'}",
                total_bytes=file.size,
            )
            await tracker.start(unified_task_id)
        except Exception as e:
            logger.warning(f"[TaskManager] Failed to track upload: {e}")

        svc = ResourcesService()
        result = await svc.upload_resource(
            user_id=auth.user_id,
            file=file,
            scope_id=scope_id,
            folder_id=folder_id,
            library_id=library_id,
        )

        # Mark upload complete
        if unified_task_id:
            try:
                await tracker.complete(
                    unified_task_id,
                    metadata_patch={
                        "resource_id": str(result.get("id", "")),
                    },
                )
            except Exception:
                pass

        # Post-processing (ffprobe metadata + thumbnail + transcode) runs
        # asynchronously in upload_postprocess_workflow so the request returns
        # as soon as the bytes are written + the resource row exists. The
        # frontend (ResourcesContext) reloads on the resources UPDATE the
        # workflow writes (thumbnail_path), so metadata/thumbnail fill in live.
        if result.get("file_path") and result.get("mime_type"):
            try:
                from app.services.infra.dbos_orchestrator import (
                    start_workflow_routed,
                )
                from app.workflows.upload_postprocess import (
                    upload_postprocess_workflow,
                )

                await start_workflow_routed(
                    "upload_postprocess",
                    dbos_workflow_callable=upload_postprocess_workflow,
                    dbos_workflow_kwargs={
                        "resource_id": str(result["id"]),
                        "file_path": result["file_path"],
                        "file_type": result.get("file_type", ""),
                        "mime_type": result.get("mime_type", ""),
                        "user_id": auth.user_id,
                    },
                )
            except Exception as e:
                logger.warning(f"[Upload] postprocess dispatch failed (non-fatal): {e}")

        return {"success": True, "data": result}
    except HTTPException:
        if unified_task_id:
            try:
                await tracker.fail(unified_task_id, "Upload rejected")
            except Exception:
                pass
        raise
    except Exception as e:
        logger.error(f"Failed to upload resource: {e}")
        if unified_task_id:
            try:
                await tracker.fail(unified_task_id, str(e)[:500])
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="Failed to upload resource")
