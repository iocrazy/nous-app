"""A temp token never carries a scope its creator does not hold.

``POST /auth/temp-token`` is reachable with an API key holding only
``tags:read``. The token it minted defaulted to ``tags:read`` + ``tags:write``
whatever the key held, so a read-only key could create tags through
``POST /auth/temp-token/{token}/tags``. An API key now gets the default
narrowed to its own scopes, and an explicit request for more is refused.

Real router over HTTP; Redis is an in-memory stand-in.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000077"


class _Redis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def ttl(self, key: str) -> int:
        return 300 if key in self.store else -2


@pytest.fixture
def redis(monkeypatch) -> _Redis:
    fake = _Redis()

    async def _get() -> _Redis:
        return fake

    # ``app.api`` re-exports the router object under this module's name, so
    # resolve the module itself.
    module = importlib.import_module("app.api.temp_token_router")
    monkeypatch.setattr(module, "get_async_redis", _get)
    return fake


def _auth_as(auth_type: str, scopes: list[str] | None) -> None:
    async def _auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type=auth_type, scopes=scopes)

    app.dependency_overrides[get_auth] = _auth


@pytest.fixture(autouse=True)
def _restore():
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


def _scopes_of(redis: _Redis, token: str) -> list[str]:
    return json.loads(redis.store[f"temp_token:{token}"])["scopes"]


async def _mint(client: AsyncClient, body: Any = None):
    return await client.post("/api/v1/auth/temp-token", json=body)


@pytest.mark.asyncio
async def test_read_only_key_gets_a_read_only_token(client, redis):
    _auth_as("api_key", ["tags:read"])

    resp = await _mint(client)
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    assert _scopes_of(redis, token) == ["tags:read"]

    create = await client.post(
        f"/api/v1/auth/temp-token/{token}/tags", json={"name": "Sneaky"}
    )
    assert create.status_code == 403, create.text


@pytest.mark.asyncio
async def test_read_only_key_cannot_request_write(client, redis):
    _auth_as("api_key", ["tags:read"])

    resp = await _mint(client, {"scopes": ["tags:read", "tags:write"]})

    assert resp.status_code == 403, resp.text
    assert resp.json()["details"]["code"] == "scope_not_held"
    assert redis.store == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("held", [["tags:read", "tags:write"], ["tags:*"], ["*"]])
async def test_key_holding_write_keeps_the_default(client, redis, held):
    _auth_as("api_key", held)

    resp = await _mint(client)

    assert resp.status_code == 200, resp.text
    assert _scopes_of(redis, resp.json()["token"]) == ["tags:read", "tags:write"]


@pytest.mark.asyncio
async def test_jwt_session_keeps_the_default(client, redis):
    _auth_as("jwt", None)

    resp = await _mint(client)

    assert resp.status_code == 200, resp.text
    assert _scopes_of(redis, resp.json()["token"]) == ["tags:read", "tags:write"]
