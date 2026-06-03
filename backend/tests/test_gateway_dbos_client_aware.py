"""Gateway→DBOSClient prep (DORMANT): status/control/send callsites must
be client-aware.

These callsites use the in-process DBOS singleton today. We make each
dispatch route through the gateway `DBOSClient` handle WHEN it is set
(`dbos_orchestrator.get_dbos_client() is not None`), falling back to the
existing `DBOS.*` path otherwise.

The client is None everywhere today, so the existing `DBOS.*` branch is
always taken — ZERO behavior change. These tests pin BOTH branches:

  * client-set  → the corresponding client method is invoked
  * client-None → the existing `DBOS.*` path is invoked

Tested helpers (one per callsite group):
  * workflows_router._status_read / _retrieve_result / _steps_read /
    _cancel / _resume / _fork
  * admin.celery_router._list_workflows
  * system_monitor_service._list_workflows_async
  * approval_gate.signal_approval_decision
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest

from app.services.infra import dbos_orchestrator

# NB: app.api.__init__ binds the names `workflows_router` / `celery_router`
# to the APIRouter instances (re-exported for include_router), shadowing
# the submodule names. Import the actual modules via importlib.
wr = importlib.import_module("app.api.workflows_router")
cr = importlib.import_module("app.api.admin.celery_router")
sms = importlib.import_module("app.services.infra.system_monitor_service")
approval_gate = importlib.import_module("app.agent_framework.approval_gate")


# ── Fakes ────────────────────────────────────────────────────────────


class _FakeStatus:
    def __init__(self, status: str = "SUCCESS"):
        self.workflow_id = "wf-1"
        self.status = status
        self.name = "parse"
        self.queue_name = None
        self.created_at = None
        self.updated_at = None
        self.error = None
        self.executor_id = None
        self.app_version = None
        self.authenticated_user = None
        self.input = None
        self.output = None


class _FakeAsyncHandle:
    """Mirrors WorkflowHandleClientAsyncPolling: get_status() is async."""

    def __init__(self, status: _FakeStatus):
        self.workflow_id = "forked-wf"
        self._status = status

    async def get_status(self) -> _FakeStatus:
        return self._status

    async def get_result(self) -> Any:
        return None


class _FakeClient:
    """Records calls to assert routing through the client."""

    def __init__(self, status: _FakeStatus | None = None, raise_missing: bool = False):
        self.calls: list[tuple[str, tuple, dict]] = []
        self._status = status or _FakeStatus()
        self._raise_missing = raise_missing

    async def retrieve_workflow_async(self, workflow_id: str):
        self.calls.append(("retrieve_workflow_async", (workflow_id,), {}))
        if self._raise_missing:
            from dbos._error import DBOSNonExistentWorkflowError

            raise DBOSNonExistentWorkflowError("target", workflow_id)
        return _FakeAsyncHandle(self._status)

    async def list_workflow_steps_async(self, workflow_id: str):
        self.calls.append(("list_workflow_steps_async", (workflow_id,), {}))
        return [{"function_id": 1, "function_name": "step1"}]

    async def cancel_workflow_async(self, workflow_id: str) -> None:
        self.calls.append(("cancel_workflow_async", (workflow_id,), {}))

    async def resume_workflow_async(self, workflow_id: str) -> None:
        self.calls.append(("resume_workflow_async", (workflow_id,), {}))

    async def fork_workflow_async(self, workflow_id: str, start_step, **kw):
        self.calls.append(("fork_workflow_async", (workflow_id, start_step), kw))
        return _FakeAsyncHandle(self._status)

    def list_workflows(self, **kwargs):
        self.calls.append(("list_workflows", (), kwargs))
        return [self._status]

    async def list_workflows_async(self, **kwargs):
        self.calls.append(("list_workflows_async", (), kwargs))
        return [self._status]

    def send(self, destination_id, message, topic=None, **kw):
        self.calls.append(("send", (destination_id, message), {"topic": topic, **kw}))


def _install_fake_dbos(monkeypatch, record: list[tuple[str, tuple, dict]]):
    """Install a fake `dbos` module whose DBOS records every call. Used
    to assert the existing in-process path is taken when client is None."""

    class _FakeDBOS:
        @staticmethod
        async def get_workflow_status_async(workflow_id):
            record.append(("get_workflow_status_async", (workflow_id,), {}))
            return _FakeStatus()

        @staticmethod
        def retrieve_workflow(workflow_id):
            record.append(("retrieve_workflow", (workflow_id,), {}))

            class _H:
                async def get_result_async(self_inner):
                    return None

            return _H()

        @staticmethod
        async def list_workflow_steps_async(workflow_id):
            record.append(("list_workflow_steps_async", (workflow_id,), {}))
            return [{"function_id": 1, "function_name": "step1"}]

        @staticmethod
        async def cancel_workflow_async(workflow_id):
            record.append(("cancel_workflow_async", (workflow_id,), {}))

        @staticmethod
        async def resume_workflow_async(workflow_id):
            record.append(("resume_workflow_async", (workflow_id,), {}))

        @staticmethod
        async def fork_workflow_async(workflow_id, start_step):
            record.append(("fork_workflow_async", (workflow_id, start_step), {}))

            class _H:
                workflow_id = "forked-wf"

            return _H()

        @staticmethod
        def list_workflows(**kwargs):
            record.append(("list_workflows", (), kwargs))
            return [_FakeStatus()]

        @staticmethod
        async def list_workflows_async(**kwargs):
            record.append(("list_workflows_async", (), kwargs))
            return [_FakeStatus()]

        @staticmethod
        def send(destination_id, message, topic=None, **kw):
            record.append(("send", (destination_id, message), {"topic": topic, **kw}))

    fake_mod = types.ModuleType("dbos")
    fake_mod.DBOS = _FakeDBOS
    monkeypatch.setitem(sys.modules, "dbos", fake_mod)


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setattr(dbos_orchestrator, "_client", None, raising=False)
    yield


# ── workflows_router ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_read_uses_client_when_set(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    ws = await wr._status_read("wf-1")
    assert ws is fc._status
    assert [c[0] for c in fc.calls] == ["retrieve_workflow_async"]


@pytest.mark.asyncio
async def test_status_read_client_missing_returns_none(monkeypatch):
    """Client raises DBOSNonExistentWorkflowError for unknown ids → must
    map back to None to preserve the 404 contract."""
    fc = _FakeClient(raise_missing=True)
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    assert await wr._status_read("nope") is None


@pytest.mark.asyncio
async def test_status_read_falls_back_to_dbos_when_none(monkeypatch):
    record: list = []
    _install_fake_dbos(monkeypatch, record)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    ws = await wr._status_read("wf-1")
    assert ws is not None
    assert record[0][0] == "get_workflow_status_async"


@pytest.mark.asyncio
async def test_retrieve_result_routes(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    await wr._retrieve_result("wf-1")
    assert "retrieve_workflow_async" in [c[0] for c in fc.calls]

    record: list = []
    _install_fake_dbos(monkeypatch, record)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    await wr._retrieve_result("wf-1")
    assert record[0][0] == "retrieve_workflow"


@pytest.mark.asyncio
async def test_steps_read_routes(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    steps = await wr._steps_read("wf-1")
    assert steps and fc.calls[0][0] == "list_workflow_steps_async"

    record: list = []
    _install_fake_dbos(monkeypatch, record)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    await wr._steps_read("wf-1")
    assert record[0][0] == "list_workflow_steps_async"


@pytest.mark.asyncio
async def test_cancel_resume_route(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    await wr._cancel("wf-1")
    await wr._resume("wf-1")
    names = [c[0] for c in fc.calls]
    assert names == ["cancel_workflow_async", "resume_workflow_async"]

    record: list = []
    _install_fake_dbos(monkeypatch, record)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    await wr._cancel("wf-1")
    await wr._resume("wf-1")
    assert [r[0] for r in record] == [
        "cancel_workflow_async",
        "resume_workflow_async",
    ]


@pytest.mark.asyncio
async def test_fork_positional_start_step(monkeypatch):
    """fork_workflow_async(workflow_id, start_step) takes start_step
    positionally — the client call must pass 1 positionally."""
    fc = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    handle = await wr._fork("wf-1")
    assert handle.workflow_id == "forked-wf"
    call = fc.calls[0]
    assert call[0] == "fork_workflow_async"
    assert call[1] == ("wf-1", 1)  # start_step passed positionally as 1
    assert call[2] == {}  # no kwargs

    record: list = []
    _install_fake_dbos(monkeypatch, record)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    await wr._fork("wf-1")
    assert record[0][0] == "fork_workflow_async"
    assert record[0][1] == ("wf-1", 1)


# ── admin.celery_router ──────────────────────────────────────────────


def test_celery_list_workflows_routes(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    out = cr._list_workflows(status="RUNNING")
    assert out == [fc._status]
    assert fc.calls[0] == ("list_workflows", (), {"status": "RUNNING"})

    record: list = []
    _install_fake_dbos(monkeypatch, record)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    cr._list_workflows(queue_name="agent_workforce", status="ENQUEUED")
    assert record[0][0] == "list_workflows"
    assert record[0][2] == {"queue_name": "agent_workforce", "status": "ENQUEUED"}


# ── system_monitor_service ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_monitor_list_workflows_async_routes(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    out = await sms._list_workflows_async(status="RUNNING")
    assert out == [fc._status]
    assert fc.calls[0] == ("list_workflows_async", (), {"status": "RUNNING"})

    record: list = []
    _install_fake_dbos(monkeypatch, record)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    await sms._list_workflows_async(status="RUNNING")
    assert record[0][0] == "list_workflows_async"
    assert record[0][2] == {"status": "RUNNING"}


# ── approval_gate ────────────────────────────────────────────────────


def test_signal_approval_uses_client_send(monkeypatch):
    fc = _FakeClient()
    monkeypatch.setattr(dbos_orchestrator, "_client", fc)
    ok = approval_gate.signal_approval_decision(
        workflow_id="wf-1", approval_id="appr-1", approved=True, note="ok"
    )
    assert ok is True
    assert fc.calls[0][0] == "send"
    dest, payload = fc.calls[0][1]
    assert dest == "wf-1"
    assert payload == {"approved": True, "note": "ok"}
    # topic passed as keyword
    assert fc.calls[0][2]["topic"] == approval_gate._topic_for("appr-1")


def test_signal_approval_falls_back_to_dbos_send(monkeypatch):
    record: list = []
    _install_fake_dbos(monkeypatch, record)
    monkeypatch.setattr(dbos_orchestrator, "_client", None)
    ok = approval_gate.signal_approval_decision(
        workflow_id="wf-1", approval_id="appr-1", approved=False, note=None
    )
    assert ok is True
    assert record[0][0] == "send"
    assert record[0][1] == ("wf-1", {"approved": False, "note": None})
    assert record[0][2]["topic"] == approval_gate._topic_for("appr-1")
