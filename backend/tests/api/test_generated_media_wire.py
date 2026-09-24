"""``/generated-media`` wire parity after it gained response models (P2).

Each JSON route is driven over real HTTP with a row carrying every projected
column in its native type, and the body must equal what FastAPI sent for the
bare dict. See ``tests/api/wire_parity.py`` for why.
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models.generated_media import GeneratedMedia
from app.schemas.generated_media import (
    GeneratedMediaImported,
    GeneratedMediaPromoted,
    GeneratedMediaRow,
    GeneratedMediaUpscaled,
)
from tests.api.wire_parity import assert_wire_unchanged, sample_row

r = sys.modules["app.api.generated_media_router"]
repo_mod = sys.modules["app.repositories.generated_media_repository"]

USER = "00000000-0000-0000-0000-000000000042"
PROJECTED = [col.key for col in repo_mod._GM_COLS]


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth

    async def _scope(_auth):
        return 42

    monkeypatch.setattr(r, "_scope", _scope)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _repo_row() -> dict:
    """What the repository returns: the projection, then ``_normalize``."""
    row = sample_row(GeneratedMedia, only=PROJECTED)
    row["review_state"] = "unreviewed"
    return repo_mod._normalize(row)


def _row_with_nulls() -> dict:
    """The same row with every nullable column null (all Optional fields)."""
    row = _repo_row()
    for name, field in GeneratedMediaRow.model_fields.items():
        if type(None) in getattr(field.annotation, "__args__", ()):
            row[name] = None
    return row


class _Repo:
    def __init__(self, row):
        self.row = row

    async def list_for_scope(self, scope_id, **kwargs):
        return {"items": [self.row, _row_with_nulls()], "next_cursor": "abc"}

    async def get(self, gen_id, scope_id):
        return self.row

    async def delete(self, gen_id, scope_id):
        return False


def test_row_model_declares_exactly_the_projection() -> None:
    assert set(GeneratedMediaRow.model_fields) == set(PROJECTED)


@pytest.mark.asyncio
async def test_list_wire_unchanged(monkeypatch, client) -> None:
    repo = _Repo(_repo_row())
    monkeypatch.setattr(r, "GeneratedMediaRepository", lambda: repo)
    page = await repo.list_for_scope(42)
    resp = await client.get("/api/v1/generated-media")
    assert_wire_unchanged(resp, {"data": page})
    assert resp.json()["data"]["items"][0]["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_list_last_page_wire_unchanged(monkeypatch, client) -> None:
    class _Empty(_Repo):
        async def list_for_scope(self, scope_id, **kwargs):
            return {"items": [], "next_cursor": None}

    monkeypatch.setattr(r, "GeneratedMediaRepository", lambda: _Empty(None))
    resp = await client.get("/api/v1/generated-media")
    assert_wire_unchanged(resp, {"data": {"items": [], "next_cursor": None}})


@pytest.mark.asyncio
async def test_get_wire_unchanged(monkeypatch, client) -> None:
    row = _repo_row()
    monkeypatch.setattr(r, "GeneratedMediaRepository", lambda: _Repo(row))
    resp = await client.get("/api/v1/generated-media/7")
    assert_wire_unchanged(resp, {"data": row})


@pytest.mark.asyncio
async def test_delete_wire_unchanged(monkeypatch, client) -> None:
    monkeypatch.setattr(r, "GeneratedMediaRepository", lambda: _Repo(None))
    resp = await client.delete("/api/v1/generated-media/7")
    assert_wire_unchanged(resp, {"data": {"deleted": False}})


@pytest.mark.asyncio
async def test_import_wire_unchanged(monkeypatch, client) -> None:
    import app.services.library.generated_media_service as gm

    async def _register(**kwargs):
        return {"id": 999}

    monkeypatch.setattr(gm, "register_generated_media", _register)
    resp = await client.post(
        "/api/v1/generated-media/import",
        files={"file": ("pic.png", b"img-bytes", "image/png")},
    )
    expected = {
        "id": "999",
        "url": "/api/v1/generated-media/999/cover",
        "media_kind": "image",
        "mime": "image/png",
    }
    assert set(expected) == set(GeneratedMediaImported.model_fields)
    assert_wire_unchanged(resp, {"data": expected})


def test_small_result_models_declare_every_key_the_handlers_build() -> None:
    """upscale / promote build their dicts inline from strings; the HTTP
    paths are covered in their own route tests. Pin the key sets here."""
    assert set(GeneratedMediaUpscaled.model_fields) == {"id", "url"}
    assert set(GeneratedMediaPromoted.model_fields) == {"promoted_resource_id"}


def test_binary_routes_advertise_no_json_body() -> None:
    paths = app.openapi()["paths"]
    for suffix in ("cover", "stream", "file"):
        op = paths[f"/api/v1/generated-media/{{gen_id}}/{suffix}"]["get"]
        content = op["responses"]["200"]["content"]
        assert "application/json" not in content, suffix
