"""C1 — codex daemon pairing API.

Design: docs/superpowers/specs/2026-08-23-codex-per-user-daemon-design.md
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

r = sys.modules["app.api.codex_daemon_router"]

FAKE_USER_ID = "00000000-0000-0000-0000-000000000042"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_pair_code_is_issued_and_stored_one_shot(monkeypatch, client):
    """POST /codex-daemon/pair-code mints a short code bound to the caller."""
    store: dict = {}

    async def _fake_put(code: str, user_id: str) -> None:
        store[code] = user_id

    monkeypatch.setattr(r, "_store_pair_code", _fake_put)
    resp = await client.post("/api/v1/codex-daemon/pair-code")
    assert resp.status_code == 200, resp.text
    code = resp.json()["data"]["code"]
    assert len(code) == 8
    assert store[code] == FAKE_USER_ID


@pytest.mark.asyncio
async def test_pair_consumes_code_and_returns_token_once(monkeypatch, client):
    """POST /codex-daemon/pair swaps the code for a device token; the token
    is returned in plaintext exactly once and stored only as a hash."""
    saved: dict = {}

    async def _fake_consume(code: str):
        return FAKE_USER_ID if code == "ABCD1234" else None

    async def _fake_insert(**kwargs):
        saved.update(kwargs)
        return {"id": 555}

    monkeypatch.setattr(r, "_consume_pair_code", _fake_consume)
    monkeypatch.setattr(r, "_insert_daemon", _fake_insert)

    resp = await client.post(
        "/api/v1/codex-daemon/pair",
        json={"code": "ABCD1234", "device_name": "mac-mini", "platform": "darwin"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["device_id"] == "555"
    token = body["device_token"]
    assert len(token) >= 32
    # only the hash was persisted
    assert "token_hash" in saved and saved["token_hash"] != token
    assert saved["user_id"] == FAKE_USER_ID
    assert saved["device_name"] == "mac-mini"


@pytest.mark.asyncio
async def test_pair_rejects_unknown_or_used_code(monkeypatch, client):
    async def _fake_consume(code: str):
        return None

    monkeypatch.setattr(r, "_consume_pair_code", _fake_consume)
    resp = await client.post(
        "/api/v1/codex-daemon/pair",
        json={"code": "NOPE0000", "device_name": "x", "platform": "linux"},
    )
    assert resp.status_code == 400
