"""Admin endpoint tests for POST /admin/storage-migration (Task 4.2, PR-4
storage-unification epic).

House convention (see test_scene_convert_dispatch.py /
test_admin_ai_usage.py): call the endpoint function directly, stub
``start_workflow_routed`` at its defining module (the endpoint imports it
locally at call time, so patching the source resolves it).

Cases:
1. unknown module -> 400 HTTPException, dispatch never called.
2. valid dispatch -> workflow dispatched with the exact request args +
   the returned workflow_id matches what start_workflow_routed returned.
3. defaults are dry_run=True, delete_source=False.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.admin.storage_migration_router import (
    StorageMigrationRequest,
    dispatch_storage_migration,
)

pytestmark = pytest.mark.asyncio


def _auth():
    auth = MagicMock()
    auth.user_id = "admin-1"
    return auth


def _dispatch_mock(monkeypatch, workflow_id="wf-123"):
    dispatch = AsyncMock(
        return_value={
            "mode": "dbos",
            "task_type": "storage_migration",
            "dbos_workflow_id": workflow_id,
        }
    )
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )
    return dispatch


async def test_unknown_module_rejected_before_dispatch(monkeypatch):
    dispatch = _dispatch_mock(monkeypatch)
    body = StorageMigrationRequest(module="not_a_real_module")

    with pytest.raises(HTTPException) as exc_info:
        await dispatch_storage_migration(body, _auth())

    assert exc_info.value.status_code == 400
    dispatch.assert_not_awaited()


async def test_valid_dispatch_passes_exact_args_and_returns_workflow_id(monkeypatch):
    dispatch = _dispatch_mock(monkeypatch, workflow_id="wf-abc")
    body = StorageMigrationRequest(
        module="uploads",
        scope_id=42,
        limit=100,
        dry_run=False,
        delete_source=True,
    )

    result = await dispatch_storage_migration(body, _auth())

    dispatch.assert_awaited_once()
    assert dispatch.call_args.args[0] == "storage_migration"
    from app.workflows.storage_migration import storage_migration_workflow

    assert (
        dispatch.call_args.kwargs["dbos_workflow_callable"]
        is storage_migration_workflow
    )
    assert dispatch.call_args.kwargs["dbos_workflow_kwargs"] == {
        "module": "uploads",
        "scope_id": 42,
        "limit": 100,
        "dry_run": False,
        "delete_source": True,
    }

    assert result.workflow_id == "wf-abc"
    assert result.module == "uploads"
    assert result.dry_run is False
    assert result.delete_source is True
    assert result.limit == 100


async def test_defaults_are_dry_run_true_delete_source_false(monkeypatch):
    dispatch = _dispatch_mock(monkeypatch, workflow_id="wf-def")
    body = StorageMigrationRequest(module="project_files")

    result = await dispatch_storage_migration(body, _auth())

    assert body.dry_run is True
    assert body.delete_source is False
    assert body.limit == 500
    assert result.dry_run is True
    assert result.delete_source is False
    assert dispatch.call_args.kwargs["dbos_workflow_kwargs"]["dry_run"] is True
    assert dispatch.call_args.kwargs["dbos_workflow_kwargs"]["delete_source"] is False
