"""``DELETE /schedules/{id}`` and ``POST /schedules/{id}/fire-now`` say what
actually happened.

* A delete that matched nothing (someone else's schedule, or gone) answered
  ``200 {"ok": true, "deleted": 0}``; it is a typed 404 now.
* fire-now on a disabled schedule answered ``queued_for_next_tick: true`` —
  the master scheduler only scans enabled rows, so it never fired while the UI
  toasted "Routine fired". It is a typed 409 ``schedule_disabled`` and writes
  nothing.
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

mod = importlib.import_module("app.api.schedules_router")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
SID = "00000000-0000-0000-0000-0000000000a1"
BASE = "/api/v1/schedules"


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, *answers: list[Any]) -> None:
        self.answers = list(answers)
        self.sql: list[str] = []

    async def execute(self, stmt: Any, *a, **kw) -> _Rows:
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))
        return _Rows(self.answers.pop(0) if self.answers else [])


def _use(monkeypatch, session: _Session) -> _Session:
    @asynccontextmanager
    async def _scope(*a, **kw):
        yield session

    monkeypatch.setattr(mod, "read_scope", _scope)
    monkeypatch.setattr(mod, "write_scope", _scope)
    return session


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
async def test_delete_of_own_schedule(client, monkeypatch):
    s = _use(monkeypatch, _Session([(SID,)]))
    resp = await client.delete(f"{BASE}/{SID}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "deleted": 1}
    assert "user_schedules.user_id" in s.sql[0]


@pytest.mark.asyncio
async def test_delete_that_matched_nothing_is_a_typed_404(client, monkeypatch):
    _use(monkeypatch, _Session([]))
    resp = await client.delete(f"{BASE}/{SID}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_fire_now_on_an_enabled_schedule(client, monkeypatch):
    s = _use(monkeypatch, _Session([{"id": SID, "enabled": True}], [(SID,)]))
    resp = await client.post(f"{BASE}/{SID}/fire-now")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "queued_for_next_tick": True}
    assert s.sql[1].startswith("UPDATE")
    assert "user_schedules.user_id" in s.sql[1]


@pytest.mark.asyncio
async def test_fire_now_on_a_disabled_schedule_is_refused(client, monkeypatch):
    s = _use(monkeypatch, _Session([{"id": SID, "enabled": False}]))
    resp = await client.post(f"{BASE}/{SID}/fire-now")
    assert resp.status_code == 409, resp.text
    assert resp.json()["details"]["code"] == "schedule_disabled"
    assert len(s.sql) == 1  # nothing written


@pytest.mark.asyncio
async def test_fire_now_on_someone_elses_schedule_is_404(client, monkeypatch):
    s = _use(monkeypatch, _Session([]))
    resp = await client.post(f"{BASE}/{SID}/fire-now")
    assert resp.status_code == 404, resp.text
    assert len(s.sql) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [("delete", ""), ("post", "/fire-now")])
async def test_a_malformed_id_is_a_422_not_a_500(client, monkeypatch, method, path):
    _use(monkeypatch, _Session())
    resp = await getattr(client, method)(f"{BASE}/not-a-uuid{path}")
    assert resp.status_code == 422, resp.text
