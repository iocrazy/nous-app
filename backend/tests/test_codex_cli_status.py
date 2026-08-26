"""IC「检测 CLI」的服务器侧对等物 — GET /codex-cli/status reports what the
SERVER container has (A-方案 shared runtime): gpt-image-2-skill presence +
version, codex CLI presence, and whether a codex OAuth session exists."""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

r = sys.modules["app.api.jimeng_cli_router"]


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id="00000000-0000-0000-0000-000000000042", auth_type="jwt")


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
async def test_status_reports_all_three_facets(monkeypatch, client):
    async def _fake_probe():
        return {
            "skill": {
                "installed": True,
                "version": "0.7.3",
                "path": "/usr/local/bin/gpt-image-2-skill",
            },
            "codex": {"installed": False, "version": None, "path": None},
            "auth_ok": True,
        }

    monkeypatch.setattr(r, "_probe_codex_cli", _fake_probe)
    resp = await client.get("/api/v1/codex-cli/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["skill"]["installed"] is True
    assert data["skill"]["version"] == "0.7.3"
    assert data["codex"]["installed"] is False
    assert data["auth_ok"] is True
