"""Client-aware dispatch for the issues router (gateway→DBOSClient prep).

The dispatch helper `_dispatch_execute_issue(issue_id, wf_id)` routes through the
gateway DBOSClient when one is constructed, else falls back to the in-process
`SetWorkflowID + DBOS.start_workflow` path. Client is None today → fallback path
→ zero behavior change. These unit-test the extracted helper directly (the
endpoint itself needs heavy auth/DB setup).
"""

from __future__ import annotations

import importlib

import pytest

from app.services.infra import dbos_orchestrator
from app.services.issues import issue_dispatch

# NOTE: `app/api/__init__.py` rebinds the name `issues_router` to the APIRouter
# instance (shadowing the submodule), so a plain `import app.api.issues_router
# as issues_router` would yield the router object. Load the actual module.
issues_router = importlib.import_module("app.api.issues_router")


@pytest.fixture(autouse=True)
def _no_marker_write(monkeypatch):
    """Phase 2b-2: the helper now stamps ``execution_state.dispatching`` before
    it enqueues. These tests are about the ENQUEUE, so the marker write is
    stubbed — otherwise every case would reach for a database."""

    async def _merge(issue_id, patch):
        return None

    monkeypatch.setattr(issue_dispatch, "merge_execution_state", _merge)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list = []

    def enqueue(self, options, *args):
        self.calls.append((options, args))
        return "fake-wf-handle"


async def test_dispatch_uses_client_when_set(monkeypatch):
    """When a DBOSClient is constructed, dispatch enqueues via client.enqueue
    with EnqueueOptions(workflow_name="execute_issue", queue_name="dbos_dispatch",
    workflow_id=wf_id) and the issue_id positional — NOT DBOS.start_workflow."""
    fake = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fake)
    # No build-info in test env → pinned version is None → no app_version key.
    monkeypatch.setattr(dbos_orchestrator, "_resolve_pinned_app_version", lambda: None)

    # Spy that must NOT be called on the client path.
    from dbos import DBOS

    called = {"start": False}
    monkeypatch.setattr(
        DBOS, "start_workflow", lambda *a, **k: called.__setitem__("start", True)
    )

    await issues_router._dispatch_execute_issue(12345, "issue-12345-abc")

    assert called["start"] is False
    assert len(fake.calls) == 1
    options, args = fake.calls[0]
    assert options["workflow_name"] == "execute_issue"
    assert options["queue_name"] == "dbos_dispatch"
    assert options["workflow_id"] == "issue-12345-abc"
    assert "app_version" not in options
    # M4 Autopilot (task O2): `auto` now rides as a second positional arg
    # (default False for a manual dispatch — this call site never passes it).
    assert args == (12345, False)


async def test_dispatch_pins_app_version_when_present(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fake)
    monkeypatch.setattr(
        dbos_orchestrator, "_resolve_pinned_app_version", lambda: "deadbeef"
    )

    await issues_router._dispatch_execute_issue(7, "issue-7-xyz")

    options, args = fake.calls[0]
    assert options["app_version"] == "deadbeef"
    assert args == (7, False)


async def test_dispatch_falls_back_to_start_workflow_when_client_none(monkeypatch):
    """Client None (today's reality) → existing SetWorkflowID + DBOS.start_workflow
    path with the same positional args. Zero behavior change."""
    monkeypatch.setattr(dbos_orchestrator, "_client", None)

    from dbos import DBOS

    spy: list = []
    monkeypatch.setattr(
        DBOS, "start_workflow", lambda wf, *args: spy.append((wf, args))
    )

    await issues_router._dispatch_execute_issue(99, "issue-99-def")

    assert len(spy) == 1
    wf, args = spy[0]
    assert wf is issues_router.execute_issue
    assert args == (99, False)
