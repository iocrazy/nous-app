"""Workforce actions and the agent drawer are platform-admin only.

The persistent agents are shared system presets. Before this gate any
logged-in user could pause one (RunRecorder then refuses every user's runs on
it), dismiss its whole inbox (every user's queued Delegate), cancel its current
task, or open the drawer, which lists raw inbox/outbox payloads and run
summaries — other users' prompts and answers. ``/ai-library/agents/{slug}/pause``
refuses presets outright; the workforce route was the side door.

Each action is checked in pairs: a regular user (and a user with no profile
row) gets 403 and the action never runs; an admin gets through.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

mod = importlib.import_module("app.api.workforce_router")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
AGENT_ID = "00000000-0000-0000-0000-0000000000a1"
TASK_ID = "00000000-0000-0000-0000-0000000000b2"
BASE = "/api/v1/workforce"

_ROLE: dict[str, Any] = {"role": "admin"}


class _RoleRow:
    def __init__(self, role: Any) -> None:
        self._role = role

    def first(self) -> Any:
        return None if self._role is None else (self._role,)


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    """Signed in; ``get_admin_auth``'s role lookup is scripted."""
    _ROLE["role"] = "admin"

    class _Session:
        async def execute(self, *a, **kw):
            return _RoleRow(_ROLE["role"])

    @asynccontextmanager
    async def _scope(*a, **kw):
        yield _Session()

    # get_admin_auth imports read_scope from app.db.session at call time; the
    # router bound its own copy at import, so this patch only reaches the gate.
    monkeypatch.setattr("app.db.session.read_scope", _scope)

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


class _AgentRepo:
    def __init__(self) -> None:
        self.updates: list[tuple[UUID, dict[str, Any]]] = []

    async def get_by_slug(self, slug: str) -> dict[str, Any]:
        return {
            "id": AGENT_ID,
            "slug": slug,
            "name": "Coordinator",
            "icon": None,
            "model": None,
            "persistent": True,
            "paused_reason": None,
        }

    async def update_fields(self, agent_id: UUID, fields: dict[str, Any]) -> None:
        self.updates.append((agent_id, fields))


@pytest.fixture
def agents(monkeypatch) -> _AgentRepo:
    repo = _AgentRepo()
    monkeypatch.setattr(mod, "get_agent_repository", lambda: repo)
    return repo


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def mappings(self) -> "_Rows":
        return self


class _RouterSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any, *a, **kw) -> _Rows:
        self.statements.append(stmt)
        return _Rows([])


@pytest.fixture
def router_session(monkeypatch) -> _RouterSession:
    session = _RouterSession()

    @asynccontextmanager
    async def _scope(*a, **kw):
        yield session

    monkeypatch.setattr(mod, "read_scope", _scope)
    monkeypatch.setattr(mod, "write_scope", _scope)
    return session


ACTIONS = [
    ("post", f"{BASE}/agents/coordinator/pause"),
    ("post", f"{BASE}/agents/coordinator/resume"),
    ("post", f"{BASE}/agents/coordinator/clear-inbox"),
    ("get", f"{BASE}/agents/coordinator/detail"),
    ("post", f"{BASE}/tasks/{TASK_ID}/cancel"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", None])
@pytest.mark.parametrize("method,path", ACTIONS)
async def test_a_non_admin_is_refused_and_nothing_runs(
    client, agents, router_session, monkeypatch, role, method, path
):
    touched: list[str] = []

    def _workforce_repo():
        touched.append("workforce")
        raise AssertionError("the workforce repository must not be reached")

    monkeypatch.setattr(mod, "get_agent_workforce_repository", _workforce_repo)
    _ROLE["role"] = role
    resp = await getattr(client, method)(path)
    assert resp.status_code == 403, resp.text
    assert agents.updates == []
    assert router_session.statements == []
    assert touched == []


@pytest.mark.asyncio
async def test_an_admin_can_pause_and_resume(client, agents):
    resp = await client.post(f"{BASE}/agents/coordinator/pause", json={})
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"{BASE}/agents/coordinator/resume")
    assert resp.status_code == 200, resp.text
    assert [fields for _, fields in agents.updates] == [
        {"paused_reason": "manual"},
        {"paused_reason": None},
    ]


@pytest.mark.asyncio
async def test_an_admin_can_clear_the_inbox_and_open_the_drawer(
    client, agents, router_session
):
    resp = await client.post(f"{BASE}/agents/coordinator/clear-inbox")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"slug": "coordinator", "cleared": 0}
    resp = await client.get(f"{BASE}/agents/coordinator/detail")
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent"]["id"] == AGENT_ID


@pytest.mark.asyncio
async def test_the_board_stays_open_to_a_regular_user(client, router_session):
    _ROLE["role"] = "user"
    resp = await client.get(f"{BASE}/board")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"agents": [], "recent_state_history": []}


@pytest.mark.asyncio
async def test_healthz_never_echoes_the_driver_message(client, monkeypatch):
    """The probe is anonymous; a connection error's text carries host, port
    and user. Only the class name goes out."""

    class _Boom(Exception):
        pass

    @asynccontextmanager
    async def _broken(*a, **kw):
        raise _Boom("connect to db.internal:55434 user=postgres failed")
        yield  # pragma: no cover

    async def _none():
        return None

    class _Pool:
        async def inflight_count(self) -> int:
            return 0

    monkeypatch.setattr(mod, "read_scope", _broken)
    monkeypatch.setattr(mod, "dbos_is_launched", lambda: True)
    monkeypatch.setattr(mod, "DbosAgentWorkforcePool", _Pool)
    monkeypatch.setattr(mod, "_oldest_undispatched_age_s", _none)
    app.dependency_overrides.pop(get_auth, None)
    resp = await client.get(f"{BASE}/healthz")
    assert resp.status_code == 200, resp.text
    assert resp.json()["supabase"] == {"reachable": False, "error": "_Boom"}
    assert "55434" not in resp.text
