"""Sprint 5.5 — bounds dispatch gate in start_workflow_routed."""
from __future__ import annotations

import pytest

from app.agent_framework.bounds import BoundsAdvertisement, BoundsRegistry
from app.services.infra import dbos_orchestrator
@pytest.fixture
def registered_workflow_callable():
    """Stand-in for a @DBOS.workflow function — only its __name__ matters
    for the gate check."""
    def my_workflow(*args, **kwargs):
        ...
    my_workflow.__name__ = "my_workflow"
    return my_workflow


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Each test starts with no bounds registry. After R1 the gate is
    always-on; no env flag to set."""
    dbos_orchestrator.set_bounds_registry(None)
    yield
    dbos_orchestrator.set_bounds_registry(None)


@pytest.mark.asyncio
async def test_no_registry_skips_gate(monkeypatch, registered_workflow_callable):
    """Combined-mode (no registry wired) — gate is a no-op, dispatch
    proceeds. Validates back-compat with existing single-process deploys."""
    # Stub routing + DBOS-enabled + DBOS.start_workflow
    async def _routing(_):
        return dbos_orchestrator.RoutingDecision(task_type="x", mode="dbos")

    monkeypatch.setattr(dbos_orchestrator, "get_routing", _routing)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)

    class _Handle:
        workflow_id = "wf-1"

    class _DBOS:
        @staticmethod
        def start_workflow(_callable, **_kw):
            return _Handle()

    class _SetWorkflowID:
        def __init__(self, *_):
            ...
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False

    class _Auth:
        def __init__(self, *_, **__):
            ...
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False

    import sys

    fake_dbos = type(sys)("dbos")
    fake_dbos.DBOS = _DBOS
    fake_dbos.SetWorkflowID = _SetWorkflowID
    fake_dbos.DBOSContextSetAuth = _Auth
    monkeypatch.setitem(sys.modules, "dbos", fake_dbos)

    result = await dbos_orchestrator.start_workflow_routed(
        "x", dbos_workflow_callable=registered_workflow_callable
    )
    assert result["dbos_workflow_id"] == "wf-1"


@pytest.mark.asyncio
async def test_empty_registry_skips_gate(
    monkeypatch, registered_workflow_callable
):
    """Registry wired but no live bounds (pre-discovery / startup race) —
    don't block dispatch."""
    dbos_orchestrator.set_bounds_registry(BoundsRegistry())

    async def _routing(_):
        return dbos_orchestrator.RoutingDecision(task_type="x", mode="dbos")

    monkeypatch.setattr(dbos_orchestrator, "get_routing", _routing)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)

    class _Handle:
        workflow_id = "wf-2"

    class _DBOS:
        @staticmethod
        def start_workflow(_callable, **_kw):
            return _Handle()

    class _Ctx:
        def __init__(self, *_, **__): ...
        def __enter__(self): return self
        def __exit__(self, *_): return False

    import sys

    fake_dbos = type(sys)("dbos")
    fake_dbos.DBOS = _DBOS
    fake_dbos.SetWorkflowID = _Ctx
    fake_dbos.DBOSContextSetAuth = _Ctx
    monkeypatch.setitem(sys.modules, "dbos", fake_dbos)

    result = await dbos_orchestrator.start_workflow_routed(
        "x", dbos_workflow_callable=registered_workflow_callable
    )
    assert result["dbos_workflow_id"] == "wf-2"


@pytest.mark.asyncio
async def test_registry_with_live_worker_advertising_workflow_passes(
    monkeypatch, registered_workflow_callable
):
    reg = BoundsRegistry()
    reg.register(
        BoundsAdvertisement(
            worker_id="w-1",
            role="worker",
            workflows=frozenset({"my_workflow"}),
        )
    )
    dbos_orchestrator.set_bounds_registry(reg)

    async def _routing(_):
        return dbos_orchestrator.RoutingDecision(task_type="x", mode="dbos")

    monkeypatch.setattr(dbos_orchestrator, "get_routing", _routing)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)

    class _Handle:
        workflow_id = "wf-3"

    class _DBOS:
        @staticmethod
        def start_workflow(_callable, **_kw):
            return _Handle()

    class _Ctx:
        def __init__(self, *_, **__): ...
        def __enter__(self): return self
        def __exit__(self, *_): return False

    import sys

    fake_dbos = type(sys)("dbos")
    fake_dbos.DBOS = _DBOS
    fake_dbos.SetWorkflowID = _Ctx
    fake_dbos.DBOSContextSetAuth = _Ctx
    monkeypatch.setitem(sys.modules, "dbos", fake_dbos)

    result = await dbos_orchestrator.start_workflow_routed(
        "x", dbos_workflow_callable=registered_workflow_callable
    )
    assert result["dbos_workflow_id"] == "wf-3"


@pytest.mark.asyncio
async def test_combined_mode_self_bound_passes_naturally(
    monkeypatch, registered_workflow_callable
):
    """R1 — Combined-mode worker self-registers all local workflows in
    its bound; the gate finds it can_dispatch, no env flag needed."""
    reg = BoundsRegistry()
    reg.register(
        BoundsAdvertisement(
            worker_id="combined-self",
            role="combined",
            workflows=frozenset({"my_workflow"}),
        )
    )
    dbos_orchestrator.set_bounds_registry(reg)

    async def _routing(_):
        return dbos_orchestrator.RoutingDecision(task_type="x", mode="dbos")

    monkeypatch.setattr(dbos_orchestrator, "get_routing", _routing)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)

    class _Handle:
        workflow_id = "wf-combined"

    class _DBOS:
        @staticmethod
        def start_workflow(_callable, **_kw):
            return _Handle()

    class _Ctx:
        def __init__(self, *_, **__): ...
        def __enter__(self): return self
        def __exit__(self, *_): return False

    import sys

    fake_dbos = type(sys)("dbos")
    fake_dbos.DBOS = _DBOS
    fake_dbos.SetWorkflowID = _Ctx
    fake_dbos.DBOSContextSetAuth = _Ctx
    monkeypatch.setitem(sys.modules, "dbos", fake_dbos)

    result = await dbos_orchestrator.start_workflow_routed(
        "x", dbos_workflow_callable=registered_workflow_callable
    )
    assert result["dbos_workflow_id"] == "wf-combined"


@pytest.mark.asyncio
async def test_registry_without_advertised_workflow_fails_fast(
    monkeypatch, registered_workflow_callable
):
    """Critical case — at least one live worker, but none can run THIS
    workflow. Without the gate the job sits in the queue forever."""
    reg = BoundsRegistry()
    reg.register(
        BoundsAdvertisement(
            worker_id="w-1",
            role="worker",
            workflows=frozenset({"some_other_workflow"}),
        )
    )
    dbos_orchestrator.set_bounds_registry(reg)

    async def _routing(_):
        return dbos_orchestrator.RoutingDecision(task_type="x", mode="dbos")

    monkeypatch.setattr(dbos_orchestrator, "get_routing", _routing)
    monkeypatch.setattr(dbos_orchestrator, "is_enabled", lambda: True)

    with pytest.raises(RuntimeError, match="no live worker advertises"):
        await dbos_orchestrator.start_workflow_routed(
            "x", dbos_workflow_callable=registered_workflow_callable
        )
