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


class _FakeGenRepo:
    def __init__(self, rows):
        self.rows = rows

    async def get_by_id(self, gen_id):
        return self.rows.get(int(gen_id))


class _FakeMembership:
    def __init__(self, teams):
        self.teams = teams

    async def is_team_member(self, *, team_id, user_id):
        return team_id in self.teams


def _patch_source(monkeypatch, *, rows, teams=()):
    async def _fake_scope(auth):
        return 42

    monkeypatch.setattr(r, "_scope", _fake_scope)
    monkeypatch.setattr(r, "GeneratedMediaRepository", lambda: _FakeGenRepo(rows))
    monkeypatch.setattr(r, "_membership", lambda: _FakeMembership(set(teams)))


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

    _patch_source(
        monkeypatch,
        rows={7: {"id": "7", "scope_id": "99", "media_kind": "image"}},
        teams={99},
    )
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
    assert seen["register"]["scope_id"] == 99


@pytest.mark.asyncio
async def test_upscale_result_is_stamped_upscale_result(monkeypatch):
    """The third canvas_upload writer names itself.

    Asserted on ``_register_upscale_result`` directly because the route test
    above patches that seam out — patching the thing under test is how a
    write site ends up with no coverage at all.

    ``upscale_result`` is NOT an intermediate: the user asked for it, so it
    stays in the inbox. The role exists so the row can say which writer made
    it, which is what lets the other two be hidden without hiding this one.
    """
    import app.services.library.generated_media_service as gm
    from app.services.library.generated_roles import INTERMEDIATE_ROLES

    seen: dict = {}

    async def _fake_register(**kwargs):
        seen.update(kwargs)
        return {"id": 991}

    monkeypatch.setattr(gm, "register_generated_media", _fake_register)

    await r._register_upscale_result(
        user_id="u",
        scope_id=42,
        source_path="/tmp/up.png",
        mime="image/png",
        origin_params={"upscale": {"resolution": "4k"}},
    )
    origin = seen["origin"]
    assert origin.kind == "canvas_upload"
    assert origin.params["role"] == "upscale_result"
    # the caller's own params survive the merge
    assert origin.params["upscale"] == {"resolution": "4k"}
    assert origin.params["role"] not in INTERMEDIATE_ROLES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        {},
        {7: {"id": "7", "scope_id": "99", "media_kind": "image"}},
    ],
    ids=["missing", "not-a-member"],
)
async def test_upscale_refuses_a_source_the_caller_cannot_read(
    monkeypatch, client, rows
):
    class _MustNotRun:
        async def upscale_image(self, **kwargs):
            raise AssertionError("provider ran for an unreadable source")

    _patch_source(monkeypatch, rows=rows, teams=())
    monkeypatch.setattr(r, "_upscale_provider", lambda: _MustNotRun())

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "2k"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "generation not found"
