"""``GET /workforce/tasks/by-inbox/{id}`` over real HTTP.

Two defects the direct-call tests could not see:

* the route took ``get_current_user``, which returns a ``dict``; the owner
  check did ``str(getattr(user, "id", user))`` — the whole dict — so every
  real caller, the sender included, got 403. The unit tests passed a
  ``SimpleNamespace`` and stayed green.
* the reply lookup filtered ``agent_outbox`` on ``reply_to_message_id``, a
  column that table does not have (it is agent_inbox's), so a terminal task
  raised instead of returning the worker's answer.
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

mod = importlib.import_module("app.api.workforce_router")

pytestmark = pytest.mark.unit

OWNER = "00000000-0000-0000-0000-000000000042"
STRANGER = "00000000-0000-0000-0000-000000000099"
INBOX = "00000000-0000-0000-0000-0000000000c3"
TASK = "00000000-0000-0000-0000-0000000000d4"
AGENT = "00000000-0000-0000-0000-0000000000a1"

_CALLER = {"id": OWNER}


class _Rows:
    def __init__(self, rows: Any) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> Any:
        if isinstance(self._rows, list):
            return self._rows[0] if self._rows else None
        return self._rows

    def all(self) -> Any:
        return self._rows if isinstance(self._rows, list) else [self._rows]


class _Session:
    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.outbox_sql: list[str] = []

    async def execute(self, stmt: Any, *a, **kw) -> _Rows:
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        if "agent_outbox" in sql:
            self.outbox_sql.append(sql)
            return _Rows(
                [
                    {
                        "id": "00000000-0000-0000-0000-0000000000e5",
                        "sender_agent_id": AGENT,
                        "message_type": "task_result",
                        "payload": {"content": "done"},
                        "created_at": None,
                        "delivered": True,
                        "delivered_at": None,
                    }
                ]
            )
        if "task_tracking" not in sql:
            return _Rows(
                {
                    "id": INBOX,
                    "recipient_agent_id": AGENT,
                    "sender_kind": "user",
                    "sender_user_id": OWNER,
                    "sender_agent_id": None,
                    "reply_to_message_id": None,
                }
            )
        return _Rows(
            {
                "dbos_workflow_id": TASK,
                "agent_id": AGENT,
                "phase": self.phase,
                "started_at": None,
                "completed_at": None,
                "error_code": None,
                "error_msg": None,
                "created_at": None,
                "inbox_message_id": INBOX,
                "metadata": {},
            }
        )


@pytest.fixture
def session(monkeypatch) -> _Session:
    s = _Session("done")

    @asynccontextmanager
    async def _scope(*a, **kw):
        yield s

    monkeypatch.setattr(mod, "read_scope", _scope)
    return s


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    _CALLER["id"] = OWNER

    async def _fake_auth() -> AuthContext:
        return AuthContext(user_id=_CALLER["id"], auth_type="jwt")

    async def _fake_verify(token: str) -> dict[str, Any]:
        return {"sub": _CALLER["id"]}

    # Both auth dependencies answer for the same caller, so the test measures
    # the owner check, not which dependency the route happens to use.
    monkeypatch.setattr("app.core.deps.verify_jwt", _fake_verify)
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer t"},
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_the_sender_reads_the_task_and_the_answer(client, session):
    resp = await client.get(f"/api/v1/workforce/tasks/by-inbox/{INBOX}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task"]["id"] == TASK
    assert body["outbox_response"]["payload"] == {"content": "done"}
    (sql,) = session.outbox_sql
    assert "agent_outbox.task_id" in sql
    assert "reply_to_message_id" not in sql


@pytest.mark.asyncio
async def test_someone_else_is_refused(client, session):
    _CALLER["id"] = STRANGER
    resp = await client.get(f"/api/v1/workforce/tasks/by-inbox/{INBOX}")
    assert resp.status_code == 403, resp.text
    assert session.outbox_sql == []
