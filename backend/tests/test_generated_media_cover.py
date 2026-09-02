"""/cover serves the preview; ?full=1 serves the original; failure degrades.

Exercised through the real ASGI app (httpx ASGITransport), the same way
tests/test_generated_media_router.py drives this router — so the query
parameter, the response media type and the cache header are all pinned as the
browser actually sees them, not as the function signature promises.

/cover takes no auth (it is a bare <img src>), so no auth override is
installed here; ?full=1 does not change that posture, it only makes the
already-public original explicit.
"""

from __future__ import annotations

import sys
import types

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app  # noqa: F401  — imported for its side effect: registers routes

# app/api/__init__.py rebinds the attribute name to the APIRouter instance, so
# the module itself has to come from sys.modules (same reason as in
# tests/test_generated_media_router.py).
r = sys.modules["app.api.generated_media_router"]
serving = sys.modules["app.services.library.media_serving"]

ORIGINAL_BYTES = b"\x89PNG\r\n\x1a\n original cover bytes"
PREVIEW_BYTES = b"RIFF\x00\x00\x00\x00WEBPVP8 preview bytes"
IMMUTABLE = "public, max-age=604800, immutable"


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def image_row(monkeypatch, tmp_path):
    """A filesystem-backed image row whose ORIGINAL bytes are really on disk.

    Serving the original therefore goes through the production
    filesystem_response path rather than a stub — the fallback branch is only
    worth testing if it really serves something.
    """
    (tmp_path / "orig.png").write_bytes(ORIGINAL_BYTES)

    async def _fake_get_by_id(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": "orig.png",
            "mime": "image/png",
            "media_kind": "image",
        }

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _fake_get_by_id)
    monkeypatch.setattr(
        serving, "settings", types.SimpleNamespace(DOWNLOAD_PATH=str(tmp_path))
    )
    return tmp_path


def _spy_preview(monkeypatch, result):
    """Replace the router's ensure_preview and record every call."""
    calls: list[dict] = []

    async def _fake(row, **kwargs):
        calls.append(row)
        return result

    monkeypatch.setattr(r, "ensure_preview", _fake)
    return calls


@pytest.mark.asyncio
async def test_cover_serves_webp_preview_when_available(monkeypatch, image_row, client):
    """Default /cover hands back the preview tier, not the original."""
    calls = _spy_preview(monkeypatch, PREVIEW_BYTES)

    resp = await client.get("/api/v1/generated-media/30/cover")

    assert resp.status_code == 200, resp.text
    assert resp.content == PREVIEW_BYTES
    assert resp.content != ORIGINAL_BYTES
    assert "image/webp" in resp.headers.get("content-type", "")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cover_full_flag_serves_original_bytes(monkeypatch, image_row, client):
    """?full=1 must not even build a preview — the lightbox wants the original."""
    calls = _spy_preview(monkeypatch, PREVIEW_BYTES)

    resp = await client.get("/api/v1/generated-media/30/cover?full=1")

    assert resp.status_code == 200, resp.text
    assert resp.content == ORIGINAL_BYTES
    assert "image/png" in resp.headers.get("content-type", "")
    assert calls == [], "ensure_preview must not be called on the ?full=1 branch"


@pytest.mark.asyncio
async def test_cover_falls_back_to_original_when_preview_is_none(
    monkeypatch, image_row, client
):
    """A preview that could not be produced degrades to today's behaviour."""
    calls = _spy_preview(monkeypatch, None)

    resp = await client.get("/api/v1/generated-media/30/cover")

    assert resp.status_code == 200, resp.text
    assert resp.content == ORIGINAL_BYTES
    assert "image/png" in resp.headers.get("content-type", "")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cover_keeps_immutable_cache_header_on_both_branches(
    monkeypatch, image_row, client
):
    """Both the preview and the original stay cacheable for a week.

    The preview and the original live at DIFFERENT urls (?full=1), so an
    immutable cache entry for one can never be mistaken for the other.
    """
    _spy_preview(monkeypatch, PREVIEW_BYTES)
    preview_resp = await client.get("/api/v1/generated-media/30/cover")
    full_resp = await client.get("/api/v1/generated-media/30/cover?full=1")

    assert preview_resp.headers.get("cache-control") == IMMUTABLE
    assert full_resp.headers.get("cache-control") == IMMUTABLE


@pytest.mark.asyncio
async def test_cover_still_404s_for_a_missing_row_before_touching_the_preview(
    monkeypatch, client
):
    """The 404 gates stay in front of the new branch."""
    calls = _spy_preview(monkeypatch, PREVIEW_BYTES)

    async def _none(self, gen_id: int):
        return None

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _none)

    resp = await client.get("/api/v1/generated-media/99/cover")
    assert resp.status_code == 404
    assert calls == []


@pytest.mark.asyncio
async def test_cover_still_404s_for_non_image_media_kind(monkeypatch, client):
    """Video rows keep 404-ing on /cover; the preview tier is image-only."""
    calls = _spy_preview(monkeypatch, PREVIEW_BYTES)

    async def _video(self, gen_id: int):
        return {
            "id": gen_id,
            "file_path": "clip.mp4",
            "mime": "video/mp4",
            "media_kind": "video",
        }

    monkeypatch.setattr(r.GeneratedMediaRepository, "get_by_id", _video)

    resp = await client.get("/api/v1/generated-media/77/cover")
    assert resp.status_code == 404
    assert resp.json()["error"] == "no cover"
    assert calls == []
