"""Admin API endpoint: dispatch the storage_migration DBOS workflow.

Task 4.2 of the storage-unification epic (PR-4). Task 4.1
(``app.workflows.storage_migration``) already implements the row-wise
idempotent migration; this endpoint only validates the request and
dispatches it — mirrors the dispatch pattern used throughout the admin
surface (``start_workflow_routed`` + ``dbos_workflow_callable`` +
``dbos_workflow_kwargs``, see ``admin/transcode_router.py::retry_transcode``).

``module`` is validated against the workflow's own ``_MODULES`` registry
(imported, not duplicated) so this endpoint can never drift out of sync
with what the workflow actually supports.

Defaults are deliberately conservative: ``dry_run=True`` and
``delete_source=False`` — an admin must explicitly opt into a live run
and, separately, into deleting source files after migration.
``delete_source=True`` combined with ``dry_run=True`` is accepted (not
rejected) because it is harmless: the workflow's safety contract (see
``storage_migration.py`` module docstring, step 2) short-circuits at
``dry_run`` BEFORE any DB mutation or delete, so ``delete_source`` is
simply inert whenever ``dry_run`` is true — there is no unsafe
combination to guard against.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from app.core.admin_deps import AdminAuthDep
from app.workflows.storage_migration import _MODULES, storage_migration_workflow

router = APIRouter()


class StorageMigrationRequest(BaseModel):
    """Body for POST /admin/storage-migration."""

    module: str
    scope_id: Optional[int] = None
    limit: int = Field(default=500, ge=1, le=5000)
    dry_run: bool = True
    delete_source: bool = False


class StorageMigrationResponse(BaseModel):
    workflow_id: str
    module: str
    dry_run: bool
    delete_source: bool
    limit: int


@router.post("", response_model=StorageMigrationResponse)
async def dispatch_storage_migration(
    body: StorageMigrationRequest,
    auth: AdminAuthDep,
):
    """Dispatch one ``storage_migration_workflow`` run for ``body.module``.

    404/500 handling of the underlying batch (missing files, size
    mismatches, etc.) happens inside the workflow itself and is surfaced
    via ``task_tracking`` — this endpoint only validates the module name
    and enqueues the run.
    """
    if body.module not in _MODULES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unknown storage-migration module: {body.module!r}. "
                f"Valid modules: {sorted(_MODULES)}"
            ),
        )

    from app.services.infra.dbos_orchestrator import start_workflow_routed

    result = await start_workflow_routed(
        "storage_migration",
        dbos_workflow_callable=storage_migration_workflow,
        dbos_workflow_kwargs={
            "module": body.module,
            "scope_id": body.scope_id,
            "limit": body.limit,
            "dry_run": body.dry_run,
            "delete_source": body.delete_source,
        },
    )
    workflow_id = result["dbos_workflow_id"]

    logger.info(
        f"[Admin] storage_migration dispatched: module={body.module} "
        f"scope_id={body.scope_id} limit={body.limit} dry_run={body.dry_run} "
        f"delete_source={body.delete_source} workflow_id={workflow_id} "
        f"by admin={auth.user_id}"
    )

    return StorageMigrationResponse(
        workflow_id=workflow_id,
        module=body.module,
        dry_run=body.dry_run,
        delete_source=body.delete_source,
        limit=body.limit,
    )
