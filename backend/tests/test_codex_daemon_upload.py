"""C3 — daemon result upload (one-shot ticket, not a user session).

The daemon has no nous JWT: the job's upload ticket is its authority, and it
is single-use so a leaked ticket cannot be replayed.
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

r = sys.modules["app.api.codex_daemon_router"]


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_upload_requires_a_valid_ticket(monkeypatch, client):
    async def _fake_consume(ticket: str):
        return None

    monkeypatch.setattr(r, "_consume_upload_ticket", _fake_consume)
    resp = await client.post(
        "/api/v1/codex-daemon/upload",
        files={"file": ("x.png", b"\x89PNG", "image/png")},
        data={"ticket": "nope"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_upload_registers_the_file_for_the_ticket_owner(monkeypatch, client):
    seen: dict = {}

    async def _fake_consume(ticket: str):
        return {"user_id": "u-1", "scope_id": 42} if ticket == "good" else None

    async def _fake_register(**kwargs):
        seen.update(kwargs)
        return {"id": 991}

    monkeypatch.setattr(r, "_consume_upload_ticket", _fake_consume)
    monkeypatch.setattr(r, "_register_daemon_result", _fake_register)

    resp = await client.post(
        "/api/v1/codex-daemon/upload",
        files={"file": ("out.png", b"\x89PNG data", "image/png")},
        data={"ticket": "good"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["gen_id"] == "991"
    assert seen["user_id"] == "u-1"
    assert seen["scope_id"] == 42
