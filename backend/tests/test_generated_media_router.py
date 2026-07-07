"""Tests for generated_media_router — list/detail/file/delete endpoints.

Auth override pattern mirrors test_canvas_classic_node_route.py:
  app.dependency_overrides[get_auth] = _fake_auth
using get_auth from app.core.deps (the real dependency that AuthDep resolves).

NOTE: app/api/__init__.py rebinds ``app.api.generated_media_router`` to the
APIRouter instance, so ``import app.api.generated_media_router as r`` yields
the router object. Use sys.modules to get the real module (same pattern as
test_canvas_classic_node_route.py for canvases_router).
"""

from __future__ import annotations

import sys
import types

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Import app.main first so __init__.py runs and populates sys.modules.
from app.core.deps import AuthContext, get_auth
from app.main import app

# Must use sys.modules — __init__.py rebinds the attribute name to the APIRouter.
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
async def test_list_uses_caller_personal_scope(monkeypatch, client):
    """list_generations must resolve the caller's personal team scope_id and
    forward it to the repository — asserted from inside the patched method."""

    async def _fake_scope(uid: str) -> str:
        return "42"

    async def _fake_list(scope_id: int, **k):
        assert scope_id == 42, f"expected scope_id=42, got {scope_id!r}"
        return {"items": [{"id": 1}], "next_cursor": None}

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(
        r.GeneratedMediaRepository,
        "list_for_scope",
        lambda self, sid, **k: _fake_list(sid, **k),
    )

    resp = await client.get("/api/v1/generated-media")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["items"] == [{"id": 1}]
    assert body["data"]["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_passes_kind_filter(monkeypatch, client):
    """kind query param is forwarded to the repository."""
    captured: dict = {}

    async def _fake_scope(uid: str) -> str:
        return "7"

    async def _fake_list(scope_id: int, *, kind=None, cursor=None, limit=30):
        captured["kind"] = kind
        captured["scope_id"] = scope_id
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(
        r.GeneratedMediaRepository,
        "list_for_scope",
        lambda self, sid, **k: _fake_list(sid, **k),
    )

    resp = await client.get("/api/v1/generated-media?kind=image")
    assert resp.status_code == 200, resp.text
    assert captured["kind"] == "image"
    assert captured["scope_id"] == 7


@pytest.mark.asyncio
async def test_get_detail_404_when_not_in_scope(monkeypatch, client):
    """GET /{id} returns 404 when repo.get returns None (out of scope)."""

    async def _fake_scope(uid: str) -> str:
        return "99"

    async def _fake_get(self, gen_id: int, scope_id: int):
        return None

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "get", _fake_get)

    resp = await client.get("/api/v1/generated-media/1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_detail_returns_data_envelope(monkeypatch, client):
    """GET /{id} wraps the row in {data: row} when found."""

    async def _fake_scope(uid: str) -> str:
        return "5"

    async def _fake_get(self, gen_id: int, scope_id: int):
        return {"id": gen_id, "scope_id": scope_id, "media_kind": "image"}

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "get", _fake_get)

    resp = await client.get("/api/v1/generated-media/123")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["id"] == 123
    assert body["data"]["media_kind"] == "image"


@pytest.mark.asyncio
async def test_delete_returns_deleted_bool(monkeypatch, client):
    """DELETE /{id} returns {data: {deleted: bool}}."""

    async def _fake_scope(uid: str) -> str:
        return "3"

    async def _fake_delete(self, gen_id: int, scope_id: int) -> bool:
        return True

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "delete", _fake_delete)

    resp = await client.delete("/api/v1/generated-media/7")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["deleted"] is True


# ---------------------------------------------------------------------------
# /file endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_404_when_not_in_scope(monkeypatch, client):
    """GET /{id}/file returns 404 when repo.get returns None (row not in scope)."""

    async def _fake_scope(uid: str) -> str:
        return "99"

    async def _fake_get(self, gen_id: int, scope_id: int):
        return None

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "get", _fake_get)

    resp = await client.get("/api/v1/generated-media/55/file")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_file_404_when_file_missing_on_disk(monkeypatch, tmp_path, client):
    """GET /{id}/file returns 404 (file missing) when the row exists but the file
    is absent from disk."""

    async def _fake_scope(uid: str) -> str:
        return "5"

    async def _fake_get(self, gen_id: int, scope_id: int):
        return {"id": gen_id, "file_path": "nonexistent_file.mp4", "mime": "video/mp4"}

    # Patch settings so DOWNLOAD_PATH points to the temp dir (no file created).
    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "get", _fake_get)
    monkeypatch.setattr(r, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/10/file")
    assert resp.status_code == 404
    assert resp.json()["error"] == "file missing"


@pytest.mark.asyncio
async def test_get_file_happy_path(monkeypatch, tmp_path, client):
    """GET /{id}/file returns 200 and the file bytes when row is in scope and
    the file exists on disk."""

    file_name = "output.jpg"
    file_content = b"\xff\xd8\xff\xe0JFIF fake jpeg bytes"
    (tmp_path / file_name).write_bytes(file_content)

    async def _fake_scope(uid: str) -> str:
        return "7"

    async def _fake_get(self, gen_id: int, scope_id: int):
        return {"id": gen_id, "file_path": file_name, "mime": "image/jpeg"}

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(r.GeneratedMediaRepository, "get", _fake_get)
    monkeypatch.setattr(r, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/20/file")
    assert resp.status_code == 200, resp.text
    assert resp.content == file_content
    assert "image/jpeg" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# /cover endpoint tests (no-auth public thumbnail)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cover_404_when_row_missing(monkeypatch, client):
    """GET /{id}/cover returns 404 when repo.get_by_id returns None."""

    async def _fake_get_by_id(self, gen_id: int):
        return None

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)

    resp = await client.get("/api/v1/generated-media/99/cover")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cover_404_when_file_missing_on_disk(monkeypatch, tmp_path, client):
    """GET /{id}/cover returns 404 (file missing) when row exists but file is absent."""

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": "ghost.jpg",
            "mime": "image/jpeg",
            "media_kind": "image",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/11/cover")
    assert resp.status_code == 404
    assert resp.json()["error"] == "file missing"


@pytest.mark.asyncio
async def test_cover_happy_path_no_auth(monkeypatch, tmp_path):
    """GET /{id}/cover returns 200 + file bytes with NO auth header required.

    This test deliberately does NOT install the auth override fixture and
    does NOT pass any Authorization header — verifying the endpoint is public.
    """
    file_name = "thumb.jpg"
    file_content = b"\xff\xd8\xff\xe0JFIF cover bytes"
    (tmp_path / file_name).write_bytes(file_content)

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": file_name,
            "mime": "image/jpeg",
            "media_kind": "image",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))

    # Build a fresh client WITHOUT the auth override (cover must be public).
    app.dependency_overrides.pop(get_auth, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
        monkeypatch.setattr(r, "settings", fake_settings)

        resp = await ac.get("/api/v1/generated-media/30/cover")

    assert resp.status_code == 200, resp.text
    assert resp.content == file_content
    assert "image/jpeg" in resp.headers.get("content-type", "")
    assert "public" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# I1 — /cover must reject non-image media (keep full video behind /file)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cover_404_for_non_image_media_kind(monkeypatch, tmp_path, client):
    """GET /{id}/cover returns 404 when media_kind != 'image' (e.g. 'video').

    Full-resolution video must stay behind the auth-gated /file endpoint.
    """
    file_name = "clip.mp4"
    (tmp_path / file_name).write_bytes(b"fake mp4 bytes")

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": file_name,
            "mime": "video/mp4",
            "media_kind": "video",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/77/cover")
    assert resp.status_code == 404
    assert resp.json()["error"] == "no cover"


# ---------------------------------------------------------------------------
# /stream endpoint tests (no-auth public VIDEO serving — <video src>)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_404_when_row_missing(monkeypatch, client):
    """GET /{id}/stream returns 404 when repo.get_by_id returns None."""

    async def _fake_get_by_id(self, gen_id: int):
        return None

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)

    resp = await client.get("/api/v1/generated-media/99/stream")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stream_404_for_non_video_media_kind(monkeypatch, tmp_path, client):
    """GET /{id}/stream returns 404 when media_kind != 'video' (e.g. 'image').

    Images are served by /cover; /stream is video-only.
    """
    file_name = "pic.jpg"
    (tmp_path / file_name).write_bytes(b"fake jpeg bytes")

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": file_name,
            "mime": "image/jpeg",
            "media_kind": "image",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/77/stream")
    assert resp.status_code == 404
    assert resp.json()["error"] == "no video"


@pytest.mark.asyncio
async def test_stream_happy_path_no_auth(monkeypatch, tmp_path):
    """GET /{id}/stream returns 200 + video bytes with NO auth header required.

    Mirrors /cover's public posture — a bare <video src> can't carry a Bearer
    header. This test installs no auth override and sends no Authorization.
    """
    file_name = "clip.mp4"
    file_content = b"\x00\x00\x00 ftypisom fake mp4 bytes"
    (tmp_path / file_name).write_bytes(file_content)

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": file_name,
            "mime": "video/mp4",
            "media_kind": "video",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))

    app.dependency_overrides.pop(get_auth, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
        monkeypatch.setattr(r, "settings", fake_settings)

        resp = await ac.get("/api/v1/generated-media/30/stream")

    assert resp.status_code == 200, resp.text
    assert resp.content == file_content
    assert "video/mp4" in resp.headers.get("content-type", "")
    assert "public" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# M7 — realpath traversal guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cover_404_for_path_traversal(monkeypatch, tmp_path, client):
    """GET /{id}/cover returns 404 when file_path escapes DOWNLOAD_PATH.

    A row with file_path='../../etc/passwd' must never serve the file —
    the realpath guard must reject it with 404.
    """

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": "../../etc/passwd",
            "mime": "text/plain",
            "media_kind": "image",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/88/cover")
    assert resp.status_code == 404
