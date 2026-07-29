"""serve_audio_file — object-storage-aware /audio endpoint (PR-1 终审 fix #2,
对齐 C6 download_music_file).

Storage unification: C3 made ``extract_audio_path`` an ``sb://`` object-store
value. ``_resolve_audio_file`` (now ``_resolve_audio_source``) still did
``Path(base)/rel`` + ``.exists()`` for every candidate — always False for an
sb:// value, so the endpoint 404s once ``extract_audio_path`` (or, later,
``music_download_path``) is migrated. This mirrors
``tests/test_music_download_serve.py`` (C6): existence check dispatches by
backend (``ObjectStore.exists`` vs ``Path.exists``), and the chosen path is
handed to ``serve_stored_file`` unmodified via ``request: Request``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app.core.deps import AuthContext

pytestmark = pytest.mark.unit

SB_AUDIO_PATH = "sb://library/900/ab/cd/abcdef1234567890.m4a"
MEDIA_ID = "900"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _auth() -> AuthContext:
    return AuthContext(user_id="u1", auth_type="jwt")


def _media(**over):
    base = {
        "id": MEDIA_ID,
        "music_download_path": "",
        "extract_audio_path": "",
        "download_path": "",
    }
    base.update(over)
    return base


def _patch_repo(media):
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=media)
    return patch(
        "app.repositories.media_repository.MediaRepository", return_value=repo
    )


def _patch_base_path(path="/tmp/fake-base"):
    return patch(
        "app.api.media_slides_router.Utils.get_download_base_path",
        return_value=path,
    )


@pytest.mark.asyncio
async def test_sb_extract_audio_path_delegated_to_serve_stored_file():
    """extract_audio_path is sb:// and the object exists — ObjectStore.exists
    is queried (not Path.exists) and serve_stored_file is called with the
    sb:// value, proving the endpoint no longer 404s."""
    from app.api.media_slides_router import serve_audio_file

    media = _media(extract_audio_path=SB_AUDIO_PATH)
    sentinel = Response(content=b"audio-bytes", media_type="audio/mp4")
    serve = AsyncMock(return_value=sentinel)
    exists = AsyncMock(return_value=True)

    with (
        _patch_repo(media),
        _patch_base_path(),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
        patch("app.services.library.media_storage.ObjectStore.exists", new=exists),
    ):
        resp = await serve_audio_file(MEDIA_ID, _request(), auth=_auth(), token=None)

    assert resp is sentinel
    exists.assert_awaited_once()
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == SB_AUDIO_PATH
    assert kwargs["mime"] == "audio/mp4"


@pytest.mark.asyncio
async def test_filesystem_music_download_path_selected(tmp_path):
    """A plain filesystem-relative music_download_path that exists on disk
    is still selected and handed to serve_stored_file (zero regression)."""
    from app.api.media_slides_router import serve_audio_file

    rel = "global/resources/web/qishui/9/audio.m4a"
    real_file = tmp_path / rel
    real_file.parent.mkdir(parents=True, exist_ok=True)
    real_file.write_bytes(b"fake-audio")

    media = _media(music_download_path=rel)
    sentinel = Response(content=b"audio-bytes", media_type="audio/mp4")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(media),
        _patch_base_path(str(tmp_path)),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_audio_file(MEDIA_ID, _request(), auth=_auth(), token=None)

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == rel
    assert kwargs["mime"] == "audio/mp4"


@pytest.mark.asyncio
async def test_no_audio_source_returns_404():
    """Both candidates empty and nothing on disk — explicit 404, not a
    silent pass-through."""
    from app.api.media_slides_router import serve_audio_file

    media = _media()
    serve = AsyncMock()

    with (
        _patch_repo(media),
        _patch_base_path(),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await serve_audio_file(MEDIA_ID, _request(), auth=_auth(), token=None)

    assert exc_info.value.status_code == 404
    serve.assert_not_awaited()
