"""POST /generated-media/{id}/upscale — IC 放大 via jimeng CLI.

Same module-aliasing setup as the promote route tests.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

r = sys.modules["app.api.generated_media_router"]

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
async def test_upscale_route_runs_cli_and_registers_result(
    monkeypatch, client, tmp_path
):
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG src")
    up = tmp_path / "up.png"
    up.write_bytes(b"\x89PNG upscaled")

    class _FakeMaterialized:
        async def __aenter__(self):
            return src

        async def __aexit__(self, *a):
            return False

    class _FakeProvider:
        async def upscale_image(self, *, image_path, resolution):
            seen["cli"] = (image_path, resolution)
            return SimpleNamespace(local_path=str(up), mime="image/png")

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen["register"] = kwargs
        return {"id": 991}

    async def _fake_scope(auth):
        return 42

    monkeypatch.setattr(r, "_scope", _fake_scope)
    monkeypatch.setattr(r, "_upscale_provider", lambda: _FakeProvider())
    monkeypatch.setattr(r, "_materialize_gen_file", lambda gen_id: _FakeMaterialized())
    monkeypatch.setattr(r, "_register_upscale_result", _fake_register)

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "4k"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["url"] == "/api/v1/generated-media/991/file"
    assert seen["cli"] == (str(src), "4k")
