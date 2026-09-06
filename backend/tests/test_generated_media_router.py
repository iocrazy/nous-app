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
serving = sys.modules["app.services.library.media_serving"]

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


@pytest_asyncio.fixture
async def public_client() -> AsyncClient:
    """A client with NO auth override — for the routes that must answer a bare
    ``<img>`` / ``<video>`` / ``<audio>`` carrying no Bearer header.

    These tests used to pop ``app.dependency_overrides[get_auth]`` inline and
    never put it back. Nothing has caught fire because the autouse
    ``_override_auth`` teardown pops it again anyway, but that means each test
    was relying on another fixture to undo its own mutation of a global. The
    restore here is unconditional and belongs to whoever did the popping.
    """
    saved = app.dependency_overrides.pop(get_auth, None)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        if saved is not None:
            app.dependency_overrides[get_auth] = saved


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

    async def _fake_list(scope_id: int, *, kind=None, cursor=None, limit=30, **_):
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
async def test_list_passes_entity_filter(monkeypatch, client):
    """CC5: entity_kind/entity_id query params are forwarded to the repo."""
    captured: dict = {}

    async def _fake_scope(uid: str) -> str:
        return "7"

    async def _fake_list(scope_id: int, **k):
        captured.update(k)
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr(r, "_resolve_personal_team_id", _fake_scope)
    monkeypatch.setattr(
        r.GeneratedMediaRepository,
        "list_for_scope",
        lambda self, sid, **k: _fake_list(sid, **k),
    )

    resp = await client.get(
        "/api/v1/generated-media?entity_kind=character&entity_id=123456789"
    )
    assert resp.status_code == 200, resp.text
    assert captured["entity_kind"] == "character"
    assert captured["entity_id"] == "123456789"


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
    monkeypatch.setattr(serving, "settings", fake_settings)

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
    monkeypatch.setattr(serving, "settings", fake_settings)

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
    monkeypatch.setattr(serving, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/11/cover")
    assert resp.status_code == 404
    assert resp.json()["error"] == "file missing"


@pytest.mark.asyncio
async def test_cover_happy_path_no_auth(monkeypatch, tmp_path, public_client):
    """GET /{id}/cover returns 200 + file bytes with NO auth header required.

    ``public_client`` deliberately removes the auth override, and no
    Authorization header is sent — verifying the endpoint is public.
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

    # `public_client` has no auth override — cover must be public.
    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(serving, "settings", fake_settings)

    resp = await public_client.get("/api/v1/generated-media/30/cover")

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
    monkeypatch.setattr(serving, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/77/cover")
    assert resp.status_code == 404
    assert resp.json()["error"] == "no cover"


# ---------------------------------------------------------------------------
# /stream endpoint tests (no-auth public TIMED-MEDIA serving — <video>/<audio>)
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
async def test_stream_404_for_non_timed_media_kind(monkeypatch, tmp_path, client):
    """GET /{id}/stream returns 404 for a kind that isn't video or audio.

    Images are served by /cover. Widening /stream to audio must not widen it
    to everything — this is the half of the pair that keeps the audio test
    below meaning something.
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
    monkeypatch.setattr(serving, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/77/stream")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not streamable"


@pytest.mark.asyncio
async def test_stream_happy_path_no_auth(monkeypatch, tmp_path, public_client):
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

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(serving, "settings", fake_settings)

    resp = await public_client.get("/api/v1/generated-media/30/stream")

    assert resp.status_code == 200, resp.text
    assert resp.content == file_content
    assert "video/mp4" in resp.headers.get("content-type", "")
    assert "public" in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_stream_serves_audio_rows(monkeypatch, tmp_path, public_client):
    """GET /{id}/stream serves an audio row, with the row's own mime.

    The inbox lightbox paints audio with a bare <audio src>, which can carry
    no Bearer header — exactly the constraint that made /stream public for
    video. There were no audio rows in production when this shipped, so this
    test is the only thing exercising the branch.
    """
    file_name = "voice.mp3"
    file_content = b"ID3\x03\x00\x00\x00 fake mp3 bytes"
    (tmp_path / file_name).write_bytes(file_content)

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": file_name,
            "mime": "audio/mpeg",
            "media_kind": "audio",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(serving, "settings", fake_settings)

    resp = await public_client.get("/api/v1/generated-media/31/stream")

    assert resp.status_code == 200, resp.text
    assert resp.content == file_content
    # The row's own mime, not a hardcoded video/* — /stream never guessed.
    assert "audio/mpeg" in resp.headers.get("content-type", "")
    assert "public" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# M7 — realpath traversal guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /stream object-store branch — ranged, streamed serving (no full-file buffer)
# ---------------------------------------------------------------------------

# Deterministic 500-byte "video" blob served by the fake object store.
_OBJ_VIDEO = bytes(i % 251 for i in range(500))


class _FakeStore:
    """Stand-in for ObjectStore backed by an in-memory blob.

    Mirrors the real contract the router relies on: ``get_size`` returns the
    total, ``get_stream`` yields the requested inclusive ``[start, end]`` slice
    in small chunks. Class-level blob so the router's fresh instance shares it.
    """

    blob = _OBJ_VIDEO

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    async def get_size(self, key: str) -> int:
        return len(self.blob)

    async def get_stream(self, key: str, *, start=None, end=None, chunk_size=64):
        if start is None:
            data = self.blob
        else:
            data = self.blob[start : (end + 1) if end is not None else None]
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]


class _MissingStore(_FakeStore):
    async def get_size(self, key: str) -> int:
        raise RuntimeError("object missing")


def _object_row(gen_id: int) -> dict:
    return {
        "id": gen_id,
        "file_path": "sb://chat-media/t7/ab/cd/deadbeef.mp4",
        "mime": "video/mp4",
        "media_kind": "video",
    }


@pytest.mark.asyncio
async def test_stream_object_store_no_range_streams_full_200(monkeypatch, client):
    """No Range header → 200, whole blob streamed, Accept-Ranges advertised."""

    async def _fake_get_by_id(self, gen_id: int):
        return _object_row(gen_id)

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "ObjectStore", _FakeStore)

    resp = await client.get("/api/v1/generated-media/40/stream")
    assert resp.status_code == 200, resp.text
    assert resp.content == _OBJ_VIDEO
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["content-length"] == str(len(_OBJ_VIDEO))
    assert "video/mp4" in resp.headers.get("content-type", "")
    assert "public" in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_stream_object_store_closed_range_206(monkeypatch, client):
    """bytes=0-99 → 206 with correct Content-Range/Content-Length and slice."""

    async def _fake_get_by_id(self, gen_id: int):
        return _object_row(gen_id)

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "ObjectStore", _FakeStore)

    resp = await client.get(
        "/api/v1/generated-media/40/stream", headers={"Range": "bytes=0-99"}
    )
    assert resp.status_code == 206, resp.text
    assert resp.content == _OBJ_VIDEO[0:100]
    assert resp.headers["content-range"] == f"bytes 0-99/{len(_OBJ_VIDEO)}"
    assert resp.headers["content-length"] == "100"
    assert resp.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_stream_object_store_open_range_206(monkeypatch, client):
    """bytes=100- (open end) → 206 covering [100, size-1]."""

    async def _fake_get_by_id(self, gen_id: int):
        return _object_row(gen_id)

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "ObjectStore", _FakeStore)

    size = len(_OBJ_VIDEO)
    resp = await client.get(
        "/api/v1/generated-media/40/stream", headers={"Range": "bytes=100-"}
    )
    assert resp.status_code == 206, resp.text
    assert resp.content == _OBJ_VIDEO[100:]
    assert resp.headers["content-range"] == f"bytes 100-{size - 1}/{size}"
    assert resp.headers["content-length"] == str(size - 100)


@pytest.mark.asyncio
async def test_stream_object_store_unsatisfiable_range_416(monkeypatch, client):
    """A well-formed range past the end → 416 with Content-Range bytes */size."""

    async def _fake_get_by_id(self, gen_id: int):
        return _object_row(gen_id)

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "ObjectStore", _FakeStore)

    size = len(_OBJ_VIDEO)
    resp = await client.get(
        "/api/v1/generated-media/40/stream",
        headers={"Range": f"bytes={size + 10}-{size + 20}"},
    )
    assert resp.status_code == 416
    assert resp.headers["content-range"] == f"bytes */{size}"
    assert resp.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_stream_object_store_malformed_range_serves_full_200(monkeypatch, client):
    """A malformed Range unit is ignored per RFC 7233 → full 200."""

    async def _fake_get_by_id(self, gen_id: int):
        return _object_row(gen_id)

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "ObjectStore", _FakeStore)

    resp = await client.get(
        "/api/v1/generated-media/40/stream", headers={"Range": "items=0-9"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == _OBJ_VIDEO


@pytest.mark.asyncio
async def test_stream_object_store_missing_object_404(monkeypatch, client):
    """get_size raising (object gone) → 404, no half-open stream."""

    async def _fake_get_by_id(self, gen_id: int):
        return _object_row(gen_id)

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(r, "ObjectStore", _MissingStore)

    resp = await client.get("/api/v1/generated-media/40/stream")
    assert resp.status_code == 404
    assert resp.json()["error"] == "file missing"


@pytest.mark.asyncio
async def test_stream_filesystem_range_206_no_regression(monkeypatch, tmp_path, client):
    """Filesystem branch still answers Range with 206 via FileResponse."""
    file_name = "clip.mp4"
    file_content = bytes(i % 251 for i in range(300))
    (tmp_path / file_name).write_bytes(file_content)

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": file_name,
            "mime": "video/mp4",
            "media_kind": "video",
        }

    fake_settings = types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(serving, "settings", fake_settings)

    resp = await client.get(
        "/api/v1/generated-media/40/stream", headers={"Range": "bytes=0-49"}
    )
    assert resp.status_code == 206, resp.text
    assert resp.content == file_content[0:50]
    assert resp.headers["content-range"] == f"bytes 0-49/{len(file_content)}"


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
    monkeypatch.setattr(serving, "settings", fake_settings)

    resp = await client.get("/api/v1/generated-media/88/cover")
    assert resp.status_code == 404
