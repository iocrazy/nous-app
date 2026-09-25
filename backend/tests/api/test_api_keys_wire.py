"""API key delete / revoke: ownership and wire parity.

Both routes run through the real repository; only the database session is
scripted. The scripted session answers each statement the way Postgres would
for a table holding one key owned by ``OWNER``: it matches when the statement
names that key AND every ``user_id`` condition it carries names the owner.
Dropping the owner condition from the repository makes a stranger match.

Pinned here: ``delete`` used to return True whether or not a row matched, so
the route answered 「密钥已删除」 for another user's key id (nothing was
deleted — the WHERE had the owner — but the caller was told it was).
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import ApiKeys
from tests.api.wire_parity import assert_wire_unchanged, sample_orm

repo_mod = sys.modules["app.repositories.api_key_repository"]

pytestmark = pytest.mark.unit

OWNER = "00000000-0000-0000-0000-000000000042"
STRANGER = "00000000-0000-0000-0000-000000000099"
KEY_ID = "key_live_abc123"

_CALLER = {"user_id": OWNER}


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=_CALLER["user_id"], auth_type="jwt")


class _Scalars:
    def __init__(self, objs: list[Any]):
        self._objs = objs

    def first(self) -> Any:
        return self._objs[0] if self._objs else None


class _Result:
    def __init__(self, objs: list[Any], rowcount: int):
        self._objs = objs
        self.rowcount = rowcount

    def scalars(self) -> _Scalars:
        return _Scalars(self._objs)


def _matches(stmt) -> bool:
    params = stmt.compile().params
    users = [v for k, v in params.items() if k.startswith("user_id")]
    return KEY_ID in params.values() and all(str(u) == OWNER for u in users)


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    _CALLER["user_id"] = OWNER
    executed: list = []

    class _Session:
        async def execute(self, stmt, *a, **kw):
            executed.append(stmt)
            hit = _matches(stmt)
            row = sample_orm(ApiKeys, key_id=KEY_ID, user_id=OWNER, status="revoked")
            return _Result([row] if hit else [], 1 if hit else 0)

    @asynccontextmanager
    async def _write_scope():
        yield _Session()

    monkeypatch.setattr(repo_mod, "write_scope", _write_scope)
    app.dependency_overrides[get_auth] = _fake_auth
    yield executed
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_owner_deletes_own_key(client):
    resp = await client.delete(f"/api/v1/api-keys/{KEY_ID}")
    assert_wire_unchanged(resp, {"success": True, "message": "密钥已删除"})


@pytest.mark.asyncio
async def test_stranger_delete_is_404_not_a_false_success(client):
    _CALLER["user_id"] = STRANGER
    resp = await client.delete(f"/api/v1/api-keys/{KEY_ID}")
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_unknown_key_delete_is_404(client):
    resp = await client.delete("/api/v1/api-keys/key_live_nope")
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_owner_revokes_own_key(client):
    resp = await client.post(f"/api/v1/api-keys/{KEY_ID}/revoke")
    assert_wire_unchanged(resp, {"success": True, "message": "密钥已撤销"})
    # The body says nothing about the key: no id, prefix or secret.
    assert set(resp.json()) == {"success", "message"}


@pytest.mark.asyncio
async def test_stranger_revoke_is_404(client):
    _CALLER["user_id"] = STRANGER
    resp = await client.post(f"/api/v1/api-keys/{KEY_ID}/revoke")
    _assert_typed_404(resp)
