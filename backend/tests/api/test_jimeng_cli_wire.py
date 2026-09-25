"""Jimeng CLI routes: wire parity after they gained response models (P9).

Each ``data`` is one of two shapes, told apart by which keys are present.
Every branch the handler has is driven here through real HTTP, and the body
must equal ``jsonable_encoder`` of the dict the handler builds for it
(``tests/api/wire_parity.py``): a union member that swallowed a key, or a
default that added one, shows up as a diff.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from tests.api.wire_parity import assert_wire_unchanged

r = sys.modules["app.api.jimeng_cli_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _RoleRow:
    def first(self):
        return ("admin",)


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    class _Session:
        async def execute(self, *a, **kw):
            return _RoleRow()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _cli(result):
    async def _run(args, timeout_s=30):
        if isinstance(result, BaseException):
            raise result
        return result

    return _run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cli_result,expected",
    [
        (
            (0, 'noise {"total_credit": 66, "user_id": 1, "vip_level": "vip1"}', ""),
            {
                "available": True,
                "logged_in": True,
                "total_credit": 66,
                "user_id": "1",
                "vip_level": "vip1",
            },
        ),
        (
            (0, '{"total_credit": 12.5}', ""),
            {
                "available": True,
                "logged_in": True,
                "total_credit": 12.5,
                "user_id": "",
                "vip_level": "",
            },
        ),
        (
            (0, "not json at all", ""),
            {
                "available": True,
                "logged_in": True,
                "total_credit": None,
                "user_id": "",
                "vip_level": "",
            },
        ),
        (
            (1, "", "not logged in, run dreamina login"),
            {
                "available": True,
                "logged_in": False,
                "reason": "not logged in, run dreamina login",
            },
        ),
        (
            FileNotFoundError(),
            {"available": False, "logged_in": False, "reason": "cli_missing"},
        ),
        (
            asyncio.TimeoutError(),
            {"available": True, "logged_in": False, "reason": "timeout"},
        ),
    ],
    ids=["logged-in", "sparse", "unparseable", "logged-out", "missing", "timeout"],
)
async def test_status_wire_unchanged(monkeypatch, client, cli_result, expected):
    monkeypatch.setattr(r, "_run_dreamina", _cli(cli_result))
    resp = await client.get("/api/v1/jimeng-cli/status")
    assert_wire_unchanged(resp, {"data": expected})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flow",
    [
        {"verification_uri": "https://auth.example/device", "user_code": "AB-12"},
        {"verification_uri": "https://auth.example/device", "user_code": None},
        {"already_logged_in": True},
    ],
    ids=["link", "link-no-code", "reused"],
)
async def test_login_wire_unchanged(monkeypatch, client, flow):
    async def _start():
        return dict(flow)

    monkeypatch.setattr(r, "_start_login_flow", _start)
    resp = await client.post("/api/v1/jimeng-cli/login")
    assert_wire_unchanged(resp, {"data": flow})
