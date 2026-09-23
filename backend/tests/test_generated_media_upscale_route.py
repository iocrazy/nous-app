"""POST /generated-media/{id}/upscale — IC 放大 via the resolved upscaler
(nous-engine first, jimeng CLI fallback; resolution itself is pinned in
tests/test_upscale_provider_resolution.py).

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


def _patch_upscaler(monkeypatch, provider, row_name="nous-studio-upscale", seen=None):
    async def _resolve(user_id):
        if seen is not None:
            seen["resolved_for"] = user_id
        return provider, "studio-upscale", row_name

    monkeypatch.setattr(r, "_resolve_upscaler", _resolve)


class _FakeMaterialized:
    def __init__(self, path):
        self.path = path

    async def __aenter__(self):
        return self.path

    async def __aexit__(self, *a):
        return False


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
        async def upscale_image(self, *, image_path, resolution, model):
            seen["cli"] = (image_path, resolution, model)
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
    _patch_upscaler(monkeypatch, _FakeProvider(), seen=seen)
    monkeypatch.setattr(r, "_materialize_gen_file", lambda gen_id: _FakeMaterialized())
    monkeypatch.setattr(r, "_register_upscale_result", _fake_register)

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "4k"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["url"] == "/api/v1/generated-media/991/cover"
    assert seen["cli"] == (str(src), "4k", "studio-upscale")
    assert seen["register"]["scope_id"] == 99
    assert seen["register"]["origin_params"]["provider"] == "nous-studio-upscale"
    assert seen["resolved_for"] == FAKE_USER_ID


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
    async def _must_not_resolve(user_id):
        raise AssertionError("backend resolved for an unreadable source")

    _patch_source(monkeypatch, rows=rows, teams=())
    monkeypatch.setattr(r, "_resolve_upscaler", _must_not_resolve)

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "2k"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "generation not found"


@pytest.mark.asyncio
async def test_upstream_failure_is_502_naming_provider_and_code(
    monkeypatch, client, tmp_path
):
    """A refusal from nous-engine (key not authorised for the service) must
    reach the caller with the backend's name and the engine's code — "key not
    authorised" and "engine busy" need different fixes."""
    from app.services.media.parsers.video_providers.nous_images import (
        NousEngineImageError,
    )

    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG src")

    class _Refusing:
        async def upscale_image(self, **kwargs):
            raise NousEngineImageError(
                "model studio-upscale not found", status=404, code="model_not_found"
            )

    async def _must_not_register(**kwargs):
        raise AssertionError("registered a failed upscale")

    _patch_source(
        monkeypatch,
        rows={7: {"id": "7", "scope_id": "99", "media_kind": "image"}},
        teams={99},
    )
    _patch_upscaler(monkeypatch, _Refusing())
    monkeypatch.setattr(
        r, "_materialize_gen_file", lambda gen_id: _FakeMaterialized(src)
    )
    monkeypatch.setattr(r, "_register_upscale_result", _must_not_register)

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "2k"}
    )
    assert resp.status_code == 502
    body = resp.json()
    # Production envelope, typed code passed through (TYPED_5XX_CODES).
    assert body["success"] is False
    assert body["details"] == {
        "code": "upscale_backend_failed",
        "provider": "nous-studio-upscale",
        "upstream_status": 404,
        "upstream_code": "model_not_found",
    }
    # The raw exception text stays in the log, never in the body.
    assert "studio-upscale not found" not in resp.text


@pytest.mark.asyncio
async def test_prose_shaped_upstream_code_is_not_echoed(monkeypatch, client, tmp_path):
    """upstream_code reaches the browser, so only token-shaped codes pass."""
    from app.services.media.parsers.video_providers.nous_images import (
        NousEngineImageError,
    )

    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG src")

    class _Weird:
        async def upscale_image(self, **kwargs):
            raise NousEngineImageError(
                "boom", status=500, code="Traceback at /app/secret.py line 3"
            )

    _patch_source(
        monkeypatch,
        rows={7: {"id": "7", "scope_id": "99", "media_kind": "image"}},
        teams={99},
    )
    _patch_upscaler(monkeypatch, _Weird(), row_name="jimeng-cli-image")
    monkeypatch.setattr(
        r, "_materialize_gen_file", lambda gen_id: _FakeMaterialized(src)
    )

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "2k"}
    )
    assert resp.status_code == 502
    assert resp.json()["details"]["upstream_code"] is None
    assert "secret.py" not in resp.text


@pytest.mark.asyncio
async def test_no_upscale_backend_is_503(monkeypatch, client):
    async def _none(user_id):
        raise RuntimeError("no upscale-capable image model enabled")

    _patch_source(
        monkeypatch,
        rows={7: {"id": "7", "scope_id": "99", "media_kind": "image"}},
        teams={99},
    )
    monkeypatch.setattr(r, "_resolve_upscaler", _none)

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "2k"}
    )
    assert resp.status_code == 503
    assert resp.json()["details"] == {
        "code": "upscale_unavailable",
        "reason": "no_backend",
    }
    assert "no upscale-capable" not in resp.text


def test_failure_detail_without_upstream_code_still_names_the_backend():
    detail = r._upscale_failure_detail("jimeng-cli-image", RuntimeError("cli died"))
    assert detail == "upscale failed via jimeng-cli-image: cli died"


@pytest.mark.asyncio
async def test_provider_scratch_dir_is_reaped_after_registration(
    monkeypatch, client, tmp_path
):
    """The provider leaves its output in a ``nousimg_`` scratch dir; once the
    file is registered that dir is dead weight on the worker."""
    import tempfile

    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG src")
    scratch = tempfile.mkdtemp(prefix="nousimg_")
    produced = f"{scratch}/upscaled.png"
    with open(produced, "wb") as fh:
        fh.write(b"\x89PNG up")

    class _Provider:
        async def upscale_image(self, **kwargs):
            return SimpleNamespace(local_path=produced, mime="image/png")

    async def _register(**kwargs):
        import os

        assert os.path.exists(kwargs["source_path"])  # still there while copying
        return {"id": 5}

    _patch_source(
        monkeypatch,
        rows={7: {"id": "7", "scope_id": "99", "media_kind": "image"}},
        teams={99},
    )
    _patch_upscaler(monkeypatch, _Provider())
    monkeypatch.setattr(
        r, "_materialize_gen_file", lambda gen_id: _FakeMaterialized(src)
    )
    monkeypatch.setattr(r, "_register_upscale_result", _register)

    resp = await client.post(
        "/api/v1/generated-media/7/upscale", json={"resolution": "2k"}
    )
    assert resp.status_code == 200, resp.text
    import os

    assert not os.path.exists(scratch)
