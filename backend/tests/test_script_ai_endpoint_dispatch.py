"""Regression tests for the 4 script-AI dispatch endpoints.

Background (confirmed via prod application_logs, task_type=script_outline_gen):
all 4 endpoints in ``app.api.script_ai_router`` called
``UnifiedTaskManager.create()`` WITHOUT ``dbos_workflow_id`` and then
``start_workflow_routed()`` WITHOUT ``workflow_id``. ``task_tracking.
dbos_workflow_id`` is NOT NULL with no default, so the INSERT raised
asyncpg 23502 ("null value in column dbos_workflow_id violates not-null
constraint"), which the endpoint's generic ``except Exception`` turned into
an HTTP 500 on every "Create Story -> Generate Outline" click. This has been
broken since 23ca9d28 — the file never once threaded a shared wf_id.

Even setting the 500 aside: the DBOS<->task_tracking lifecycle mirror
trigger associates rows by matching ``dbos_workflow_id`` to the DBOS
workflow_id. Without a shared id the task_tracking row would stay
QUEUED/pending forever and the frontend ``useTaskCompletion`` poll would
never fire.

Pinned contract (same rule as reference_dbos_dispatch_endpoint_wf_id /
test_extract_audio_endpoint_dispatch.py / test_resources_ai_router_
dispatch.py): each endpoint must call ``mgr.create(..., dbos_workflow_id=
wf_id)`` and then ``start_workflow_routed(..., workflow_id=wf_id)`` with
THE SAME uuid string.

These tests call the endpoint functions directly with mocked
``get_task_manager`` / ``start_workflow_routed`` / ``_verify_script_access``
so they exercise the actual runtime call, not just a source-grep.
"""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.deps import AuthContext
from app.schemas.script import (
    ConvertToStoryboardRequest,
    CreateBranchesRequest,
    ExpandChapterRequest,
    GenerateOutlineRequest,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

# app/api/__init__.py does `from app.api.script_ai_router import router as
# script_ai_router`, which rebinds the `script_ai_router` NAME inside the
# `app.api` package namespace to the APIRouter instance. `import
# app.api.script_ai_router` (or `from app.api import script_ai_router`)
# would therefore hand back the router object, not the module — use
# importlib.import_module to reach the real module unambiguously (same
# trick as test_extract_audio_endpoint_dispatch.py).
script_ai_router = importlib.import_module("app.api.script_ai_router")


def _auth() -> AuthContext:
    return AuthContext(user_id=str(uuid.uuid4()), auth_type="jwt")


def _assert_shared_valid_uuid(mgr_create: AsyncMock, dispatch: AsyncMock) -> None:
    """Shared assertion: dbos_workflow_id passed to create() must be a
    valid uuid string and must equal the workflow_id passed to dispatch."""
    create_kwargs = mgr_create.call_args.kwargs
    dispatch_kwargs = dispatch.call_args.kwargs

    wf_id = create_kwargs.get("dbos_workflow_id")
    assert wf_id is not None, "mgr.create() must be called with dbos_workflow_id"
    # raises ValueError if not a valid uuid string
    uuid.UUID(wf_id)

    dispatched_id = dispatch_kwargs.get("workflow_id")
    assert (
        dispatched_id is not None
    ), "start_workflow_routed() must be called with workflow_id"
    uuid.UUID(dispatched_id)

    assert wf_id == dispatched_id, (
        "the pre-created task_tracking row's dbos_workflow_id must match "
        "the dispatched DBOS workflow_id, or the lifecycle mirror trigger "
        "can never associate the two rows"
    )


@pytest.fixture
def mock_task_manager(monkeypatch):
    """Patch get_task_manager() with a mock manager whose create() records
    the kwargs it was called with and returns a fake task_id."""
    mgr = AsyncMock()
    mgr.create = AsyncMock(return_value=str(uuid.uuid4()))
    monkeypatch.setattr(script_ai_router, "get_task_manager", lambda: mgr)
    return mgr


@pytest.fixture
def mock_verify_access(monkeypatch):
    # PR-B2: the private _verify_script_access copy was collapsed into the
    # shared app.core.scope_guards.verify_script_access, imported into the
    # router module — patch it by the name the router now calls.
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(script_ai_router, "verify_script_access", mock)
    return mock


async def test_generate_outline_threads_shared_wf_id(
    monkeypatch, mock_task_manager, mock_verify_access
):
    dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )

    body = GenerateOutlineRequest(
        script_id=str(uuid.uuid4()), premise="a detective in a snowstorm"
    )
    result = await script_ai_router.generate_outline(_auth(), body)

    assert result["success"] is True
    mock_task_manager.create.assert_awaited_once()
    dispatch.assert_awaited_once()
    _assert_shared_valid_uuid(mock_task_manager.create, dispatch)


async def test_expand_chapter_threads_shared_wf_id(
    monkeypatch, mock_task_manager, mock_verify_access
):
    dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )

    body = ExpandChapterRequest(
        script_id=str(uuid.uuid4()),
        chapter_id=str(uuid.uuid4()),
        title="Chapter 1",
        summary="The detective arrives.",
    )
    result = await script_ai_router.expand_chapter(_auth(), body)

    assert result["success"] is True
    mock_task_manager.create.assert_awaited_once()
    dispatch.assert_awaited_once()
    _assert_shared_valid_uuid(mock_task_manager.create, dispatch)


async def test_create_branches_threads_shared_wf_id(
    monkeypatch, mock_task_manager, mock_verify_access
):
    dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )

    body = CreateBranchesRequest(
        script_id=str(uuid.uuid4()),
        chapter_id=str(uuid.uuid4()),
        title="Chapter 1",
        summary="The detective arrives.",
    )
    result = await script_ai_router.create_branches(_auth(), body)

    assert result["success"] is True
    mock_task_manager.create.assert_awaited_once()
    dispatch.assert_awaited_once()
    _assert_shared_valid_uuid(mock_task_manager.create, dispatch)


async def test_convert_to_storyboard_is_retired_410(
    monkeypatch, mock_task_manager, mock_verify_access
):
    """Retired in the Phase B P4 cutover: the script→legacy-workbench bridge no
    longer dispatches a workflow (it wrote the now-deprecated storyboard_nodes /
    script_storyboard_links tables) — it raises 410 Gone instead."""
    from fastapi import HTTPException

    dispatch = AsyncMock(return_value={"mode": "dbos"})
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed", dispatch
    )

    body = ConvertToStoryboardRequest(
        script_id=str(uuid.uuid4()),
        chapter_id=str(uuid.uuid4()),
    )
    with pytest.raises(HTTPException) as exc:
        await script_ai_router.convert_to_storyboard(_auth(), body)

    assert exc.value.status_code == 410
    # No task row created and nothing dispatched — the endpoint short-circuits.
    mock_task_manager.create.assert_not_awaited()
    dispatch.assert_not_awaited()
