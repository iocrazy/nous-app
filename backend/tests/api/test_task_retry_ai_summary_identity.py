# backend/tests/api/test_task_retry_ai_summary_identity.py

"""F2 (2026-08-08 终审 Important): POST /tasks/{task_id}/retry re-dispatches
``ai_summary`` using ``auth.user_id`` (the retrying caller) as the
workflow's ``user_id`` kwarg — the fourth place summary dispatch identity
diverged from resource ownership, alongside ai_router.py's
``trigger_summary_by_resource`` (Task 2, commit 3c45716) and
download_helpers.py's ``chain_summary_for_tags`` (Task 4 / F1). A teammate
retrying a failed summary task on a shared resource must still dispatch the
workflow as the resource CREATOR — the workflow's ``creator_id`` filter
(``load_summary_inputs``) and the points ledger both key off that identity.

Retry itself stays keyed to the caller: ``tracker.retry_task`` only finds
rows where ``TaskTracking.user_id == <retrying caller>`` (a teammate can't
retry someone else's task_tracking row), so ``user_id`` in ``retry_task``'s
body IS the row's original triggering caller — never a stand-in for the
resource owner. Resolving the owner is a NEW read this fix adds.

That new read must itself be scope-safe: ``retry_task`` runs under
``ScopedRequestDep``, which opens ``request_scope(Scope(user_id=<caller>))``
for the whole request. With ``SCOPE_ENFORCE_RESOURCES`` on (production's
actual setting), resolving the resource's ``creator_id`` under that ambient
caller scope would hit the exact same choke-point mechanism as F1: the
choke point injects ``creator_id == caller``, so a resource owned by
someone else is invisible and the code falls back to the caller again —
silently defeating the fix it was supposed to be. So this read is wrapped
in ``system_request_scope`` the same way F1 wraps
``download_helpers.chain_summary_for_tags``'s lookup.

Direct-coroutine-call + monkeypatch convention (see
test_task_retry_module_gate.py / test_ai_router_summary_identity.py — no
HTTP test client exists for this router).
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


def _auth(user_id: str = "caller-1") -> MagicMock:
    auth = MagicMock()
    auth.user_id = user_id
    return auth


def _patch_retry_row(monkeypatch, tm, *, resource_id: str, media_id: str):
    tracker = MagicMock()
    tracker.retry_task = AsyncMock(
        return_value={
            "task_type": "ai_summary",
            "resource_id": resource_id,
            "media_id": media_id,
        }
    )
    monkeypatch.setattr(tm, "get_task_manager", lambda: tracker)
    monkeypatch.setattr(tm, "_peek_task_type", AsyncMock(return_value="ai_summary"))
    return tracker


@pytest.mark.asyncio
async def test_retry_dispatches_ai_summary_as_resource_owner_not_caller(monkeypatch):
    """Caller ("caller-1") != resource creator ("owner-1"). The retried
    workflow dispatch must use the owner's identity, while the
    task_tracking row's own attribution is untouched by this fix (retry_task
    already required caller == row owner to find the row at all)."""
    tm = importlib.import_module("app.api.task_manager_router")

    _patch_retry_row(monkeypatch, tm, resource_id="res-1", media_id="123")

    from app.repositories.resources_repository import ResourcesRepository

    async def _fake_get_resource_by_id(self, resource_id):
        return {"id": resource_id, "creator_id": "owner-1"}

    monkeypatch.setattr(
        ResourcesRepository, "get_resource_by_id", _fake_get_resource_by_id
    )

    dispatched: list = []

    async def _start(name, **kwargs):
        dispatched.append({"name": name, **kwargs})

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        AsyncMock(side_effect=_start),
    )

    await tm.retry_task("task-1", _auth("caller-1"), MagicMock())

    assert len(dispatched) == 1
    kwargs = dispatched[0]["dbos_workflow_kwargs"]
    assert kwargs["user_id"] == "owner-1"  # resource creator_id, not caller
    assert kwargs["parsed_media_id"] == 123


@pytest.mark.asyncio
async def test_retry_falls_back_to_caller_when_resource_missing(monkeypatch):
    """No resource found for resource_id (deleted / never linked) — falls
    back to the retrying caller rather than crashing or dispatching an
    unattributed workflow."""
    tm = importlib.import_module("app.api.task_manager_router")

    _patch_retry_row(monkeypatch, tm, resource_id="res-missing", media_id="123")

    from app.repositories.resources_repository import ResourcesRepository

    async def _fake_get_resource_by_id(self, resource_id):
        return None

    monkeypatch.setattr(
        ResourcesRepository, "get_resource_by_id", _fake_get_resource_by_id
    )

    dispatched: list = []

    async def _start(name, **kwargs):
        dispatched.append({"name": name, **kwargs})

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        AsyncMock(side_effect=_start),
    )

    await tm.retry_task("task-2", _auth("caller-1"), MagicMock())

    assert dispatched[0]["dbos_workflow_kwargs"]["user_id"] == "caller-1"


@pytest.mark.asyncio
async def test_retry_resolves_owner_under_system_scope_when_enforced(monkeypatch):
    """With SCOPE_ENFORCE_RESOURCES on, the owner-resolving read must NOT
    inherit the ambient CALLER scope that ScopedRequestDep opens for the
    whole request — that's the F1-shaped bug this fix must avoid
    reintroducing. Simulates the FastAPI dependency by opening
    request_scope(Scope(user_id=<caller>)) around the call (mirrors what
    ScopedRequestDep does before the endpoint body runs), then asserts the
    resource read observes SYSTEM instead."""
    import app.db.scope as scope_module
    from app.db.scope import SYSTEM, Scope, current_scope, request_scope
    from app.repositories.resources_repository import ResourcesRepository

    monkeypatch.setattr(scope_module.settings, "SCOPE_ENFORCE_RESOURCES", True)

    tm = importlib.import_module("app.api.task_manager_router")
    _patch_retry_row(monkeypatch, tm, resource_id="res-1", media_id="123")

    observed_scope: dict[str, object] = {}

    async def _fake_get_resource_by_id(self, resource_id):
        observed_scope["scope"] = current_scope()
        return {"id": resource_id, "creator_id": "owner-1"}

    monkeypatch.setattr(
        ResourcesRepository, "get_resource_by_id", _fake_get_resource_by_id
    )

    dispatched: list = []

    async def _start(name, **kwargs):
        dispatched.append({"name": name, **kwargs})

    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        AsyncMock(side_effect=_start),
    )

    async with request_scope(Scope(user_id="caller-1")):
        await tm.retry_task("task-3", _auth("caller-1"), MagicMock())

    assert observed_scope.get("scope") is SYSTEM
    assert dispatched[0]["dbos_workflow_kwargs"]["user_id"] == "owner-1"
