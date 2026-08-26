"""Local CLI tab — server-side dreamina account management (IC 即梦 CLI 卡
的 nous 版:扫码登录/查积分,登录态在服务器容器,页面替代对话喊登录)."""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

r = sys.modules["app.api.jimeng_cli_router"]

FAKE_USER = "00000000-0000-0000-0000-000000000042"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER, auth_type="jwt")


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
async def test_status_logged_in(monkeypatch, client):
    async def _fake_run(args, timeout_s=30):
        return 0, '{"total_credit": 66, "user_id": 1, "vip_level": ""}', ""

    monkeypatch.setattr(r, "_run_dreamina", _fake_run)
    resp = await client.get("/api/v1/jimeng-cli/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["logged_in"] is True
    assert data["total_credit"] == 66


@pytest.mark.asyncio
async def test_status_not_logged_in(monkeypatch, client):
    async def _fake_run(args, timeout_s=30):
        return 1, "", "尚未登录，请先执行 dreamina login"

    monkeypatch.setattr(r, "_run_dreamina", _fake_run)
    resp = await client.get("/api/v1/jimeng-cli/status")
    assert resp.status_code == 200
    assert resp.json()["data"]["logged_in"] is False


@pytest.mark.asyncio
async def test_login_start_returns_verification_link(monkeypatch, client):
    async def _fake_start():
        return {
            "verification_uri": "https://auth.example/device",
            "user_code": "ABCD-1234",
        }

    monkeypatch.setattr(r, "_start_login_flow", _fake_start)
    resp = await client.post("/api/v1/jimeng-cli/login")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["verification_uri"].startswith("https://")
    assert data["user_code"] == "ABCD-1234"
