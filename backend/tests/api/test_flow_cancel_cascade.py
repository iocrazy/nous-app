"""``POST /flows/{id}/cancel`` cancels the children's workflows.

It used to write ``phase/status='cancelled'`` and ``error_msg`` onto each
child's ``task_tracking`` row and never touch the workflow: the child kept
running, and the mirror trigger could flip the row back. It also imported a
module that no longer exists (``app.services.abort_registry``), so the cascade
raised 500 after the flow row was already marked cancelled. Now each child's DBOS
workflow is cancelled and the trigger records the outcome; the route writes
only the ``error_code`` decoration.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.core.deps import AuthContext, get_auth
from app.main import app

flows = importlib.import_module("app.api.flows_router")
workflows = importlib.import_module("app.api.workflows_router")
agent_framework = importlib.import_module("app.agent_framework")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
FLOW = "00000000-0000-0000-0000-0000000000f1"


class _Rows:
    def __init__(self, rows: Any) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> Any:
        return self._rows


class _Session:
    def __init__(self, reads: list[list[dict[str, Any]]]) -> None:
        self.reads = reads
        self.writes: list[str] = []

    async def execute(self, stmt: Any, *a, **kw) -> _Rows:
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        if sql.startswith("UPDATE"):
            self.writes.append(sql)
            return _Rows([])
        return _Rows(self.reads.pop(0))


@pytest.fixture
def session(monkeypatch) -> _Session:
    s = _Session(
        [
            [{"id": FLOW, "state": "running", "cascade_cancel": True}],
            [
                {"dbos_workflow_id": "wf-a", "phase": "processing"},
                {"dbos_workflow_id": "wf-b", "phase": "queued"},
            ],
        ]
    )

    @asynccontextmanager
    async def _scope(*a, **kw):
        yield s

    monkeypatch.setattr(flows, "read_scope", _scope)
    monkeypatch.setattr(flows, "write_scope", _scope)
    return s


@pytest.fixture
def cancelled(monkeypatch) -> list[str]:
    out: list[str] = []

    async def _cancel(workflow_id: str) -> None:
        if workflow_id == "wf-b":
            raise RuntimeError("DBOS refused")
        out.append(workflow_id)

    async def _kill(wf_id: str, grace_seconds: float = 0) -> int:
        return 0

    monkeypatch.setattr(workflows, "_cancel", _cancel)
    monkeypatch.setattr(agent_framework, "cancel_workflow_subprocesses", _kill)
    return out


@pytest.fixture(autouse=True)
def _auth():
    async def _fake_auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_children_are_cancelled_through_dbos(client, session, cancelled):
    resp = await client.post(f"/api/v1/flows/{FLOW}/cancel")
    assert resp.status_code == 200, resp.text
    # wf-b's cancel was refused, so it is not counted and not decorated.
    assert resp.json() == {"ok": True, "cascaded": 1}
    assert cancelled == ["wf-a"]
    flow_write, child_write = session.writes
    assert "task_flows" in flow_write and "task_tracking" in child_write
    set_clause = child_write.split(" SET ")[1].split(" WHERE ")[0]
    assert set_clause == "error_code=%(error_code)s"
