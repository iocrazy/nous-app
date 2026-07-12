"""dbos_orchestrator.cancel_workflow — the engine-cancel path Stop relies on
(P1-1). Cancel writes CANCELLED to the shared sys DB; the mirror trigger owns
task_tracking phase, so this helper never touches task_tracking."""

from unittest.mock import AsyncMock

import pytest

import app.services.infra.dbos_orchestrator as o


@pytest.mark.asyncio
async def test_raises_when_dbos_not_enabled(monkeypatch):
    monkeypatch.setattr(o, "_dbos", None)
    monkeypatch.setattr(o, "_client", None)
    with pytest.raises(RuntimeError, match="not enabled"):
        await o.cancel_workflow("wf-1")


@pytest.mark.asyncio
async def test_gateway_client_path_cancels_via_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(o, "_client", client)
    monkeypatch.setattr(o, "_dbos", None)
    await o.cancel_workflow("wf-2")
    client.cancel_workflow_async.assert_awaited_once_with("wf-2")


@pytest.mark.asyncio
async def test_in_process_path_cancels_via_dbos_singleton(monkeypatch):
    monkeypatch.setattr(o, "_client", None)
    monkeypatch.setattr(o, "_dbos", object())  # combined/worker: DBOS launched
    cancel = AsyncMock()
    import dbos

    monkeypatch.setattr(dbos.DBOS, "cancel_workflow_async", cancel)
    await o.cancel_workflow("wf-3")
    cancel.assert_awaited_once_with("wf-3")
