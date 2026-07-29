"""download_music_file — object-storage-aware download button (Task C6).

Storage unification: Task C3 made audio extraction write `extract_audio_path`
as an `sb://` object-store path (see `_extract_and_finalize_audio` in the
audio extraction workflow). The endpoint still treated both
`extract_audio_path` and `music_download_path` as filesystem-relative paths
(`Path(base) / rel` + `.exists()`), which is always False for an `sb://`
value — so a freshly-extracted, already-uploaded audio file 404s on the
Music download button. This mirrors `test_media_download_serve.py`'s
pattern (Task C2): existence check dispatches by backend, and the chosen
path is handed to `serve_stored_file` unmodified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app.api.media_download_router import download_music_file
from app.core.deps import AuthContext

pytestmark = pytest.mark.unit

SB_AUDIO_PATH = "sb://library/331438215859255/ab/cd/abcdef1234567890.m4a"
PLATFORM_ID = "7123456789"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _auth() -> AuthContext:
    return AuthContext(user_id="u1", auth_type="jwt")


def _video(**over):
    base = {
        "id": "900",
        "title": "My Clip",
        "extract_audio_path": "",
        "music_download_path": "",
        "music_download_status": "",
    }
    base.update(over)
    return base


def _patch_repo(video=None):
    repo = MagicMock()
    repo.get_by_platform_id = AsyncMock(return_value=video)
    return patch("app.api.media_download_router.MediaRepository", return_value=repo)


def _patch_base_path():
    return patch(
        "app.api.media_download_router.Utils.get_download_base_path",
        return_value="/tmp/fake-base",
    )


@pytest.mark.asyncio
async def test_sb_extract_audio_path_delegated_to_serve_stored_file():
    """extract_audio_path is sb:// and the object exists in the store —
    ObjectStore.exists is queried (not Path.exists) and serve_stored_file is
    called with the sb:// value, proving the endpoint no longer 404s."""
    video = _video(extract_audio_path=SB_AUDIO_PATH)
    sentinel = Response(content=b"audio-bytes", media_type="audio/mp4")
    serve = AsyncMock(return_value=sentinel)
    exists = AsyncMock(return_value=True)

    with (
        _patch_repo(video),
        _patch_base_path(),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
        patch(
            "app.services.library.media_storage.ObjectStore.exists",
            new=exists,
        ),
    ):
        resp = await download_music_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    exists.assert_awaited_once()
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == SB_AUDIO_PATH
    assert "attachment" in kwargs["disposition"]
    assert "My Clip_audio.m4a" in kwargs["disposition"]


@pytest.mark.asyncio
async def test_filesystem_extract_audio_path_exists(tmp_path):
    """A plain filesystem-relative extract_audio_path that exists on disk
    still works — the filesystem branch is preserved."""
    rel = "global/resources/web/7123456789/audio.mp3"
    real_file = tmp_path / rel
    real_file.parent.mkdir(parents=True, exist_ok=True)
    real_file.write_bytes(b"fake-mp3")

    video = _video(extract_audio_path=rel)
    sentinel = Response(content=b"audio-bytes", media_type="audio/mpeg")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(video),
        patch(
            "app.api.media_download_router.Utils.get_download_base_path",
            return_value=str(tmp_path),
        ),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await download_music_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == rel
    assert "My Clip_audio.mp3" in kwargs["disposition"]


@pytest.mark.asyncio
async def test_chinese_title_disposition_is_latin1_safe():
    """Regression (Task C6 counterpart of the C2 video fix): a Chinese
    title passes the safe_title alnum filter untouched and used to land
    raw in a bare Content-Disposition header — latin-1 encode failure on
    the response, HTTP 500. Must be RFC 5987 encoded instead."""
    video = _video(extract_audio_path=SB_AUDIO_PATH, title="我的音乐")
    sentinel = Response(content=b"audio-bytes", media_type="audio/mp4")
    serve = AsyncMock(return_value=sentinel)
    exists = AsyncMock(return_value=True)

    with (
        _patch_repo(video),
        _patch_base_path(),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
        patch(
            "app.services.library.media_storage.ObjectStore.exists",
            new=exists,
        ),
    ):
        resp = await download_music_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    args, kwargs = serve.await_args
    disposition = kwargs["disposition"]
    disposition.encode("latin-1")
    assert "filename*=UTF-8''" in disposition


@pytest.mark.asyncio
async def test_no_audio_and_status_not_completed_returns_404():
    """Both sources empty/missing and music_download_status isn't
    completed/skipped — explicit 404, not a silent pass-through."""
    video = _video(music_download_status="pending")
    serve = AsyncMock()

    with (
        _patch_repo(video),
        _patch_base_path(),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await download_music_file(PLATFORM_ID, _request(), _auth())

    assert exc_info.value.status_code == 404
    serve.assert_not_awaited()


@pytest.mark.asyncio
async def test_falls_back_to_sb_music_download_path_when_extract_missing():
    """extract_audio_path empty, music_download_path is sb:// and exists —
    the loop falls back to the second source."""
    video = _video(music_download_path=SB_AUDIO_PATH)
    sentinel = Response(content=b"audio-bytes", media_type="audio/mp4")
    serve = AsyncMock(return_value=sentinel)
    exists = AsyncMock(return_value=True)

    with (
        _patch_repo(video),
        _patch_base_path(),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
        patch(
            "app.services.library.media_storage.ObjectStore.exists",
            new=exists,
        ),
    ):
        resp = await download_music_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    exists.assert_awaited_once()
    serve.assert_awaited_once()
    args, _ = serve.await_args
    assert args[0] == SB_AUDIO_PATH
