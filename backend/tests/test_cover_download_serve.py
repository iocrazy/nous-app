"""download_cover_file — object-storage-aware download button (Task PR5-A).

Storage unification: `parsed_media.cover_download_path` is about to be
migrated to `sb://` object-store rows (mirrors the downloader's video path,
Task C2). The endpoint used to treat `cover_download_path` as always
filesystem-relative (`Path(base) / path` + `.exists()` + bare `FileResponse`),
which would 404 on every `sb://` row once the migration lands. This mirrors
`test_media_download_serve.py`'s pattern (Task C2): the router delegates to
`serve_stored_file` unmodified, regardless of the path shape — the fs branch
of `serve_stored_file` still returns a `FileResponse`, so behavior for
existing filesystem covers is unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app.api.media_download_router import download_cover_file
from app.core.deps import AuthContext

pytestmark = pytest.mark.unit

SB_COVER_PATH = "sb://library/331438215859255/ab/cd/abcdef1234567890.jpg"
PLATFORM_ID = "7123456789"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _auth() -> AuthContext:
    return AuthContext(user_id="u1", auth_type="jwt")


def _video(**over):
    base = {
        "id": "900",
        "title": "My Clip",
        "cover_download_path": SB_COVER_PATH,
    }
    base.update(over)
    return base


def _patch_repo(video=None):
    repo = MagicMock()
    repo.get_by_platform_id = AsyncMock(return_value=video)
    return patch("app.api.media_download_router.MediaRepository", return_value=repo)


@pytest.mark.asyncio
async def test_sb_cover_path_delegated_to_serve_stored_file():
    """sb:// cover_download_path is passed through to serve_stored_file,
    not treated as a filesystem path — this is the regression fix itself."""
    video = _video()
    sentinel = Response(content=b"cover-bytes", media_type="image/jpeg")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(video),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await download_cover_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == SB_COVER_PATH
    assert kwargs["mime"] == "image/jpeg"
    assert "attachment" in kwargs["disposition"]
    assert "My Clip_cover.jpg" in kwargs["disposition"]


@pytest.mark.asyncio
async def test_filesystem_cover_path_zero_regression():
    """A plain filesystem-relative cover_download_path still resolves to a
    FileResponse via serve_stored_file's filesystem branch — no behavior
    change for covers that haven't been migrated to sb://."""
    rel = "global/resources/web/900/cover.jpg"
    video = _video(cover_download_path=rel)
    sentinel = Response(content=b"cover-bytes", media_type="image/jpeg")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(video),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await download_cover_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == rel
    assert kwargs["mime"] == "image/jpeg"


@pytest.mark.asyncio
async def test_chinese_title_disposition_is_latin1_safe():
    """Regression (C2 counterpart): a Chinese title survives the safe_title
    alnum filter untouched (`.isalnum()` is True for CJK) and must not blow
    up latin-1 encoding when handed to serve_stored_file's disposition."""
    video = _video(title="我的封面")
    sentinel = Response(content=b"cover-bytes", media_type="image/jpeg")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(video),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await download_cover_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    args, kwargs = serve.await_args
    disposition = kwargs["disposition"]
    disposition.encode("latin-1")
    assert "filename*=UTF-8''" in disposition


@pytest.mark.asyncio
async def test_missing_cover_path_returns_404():
    """No cover_download_path on the record is still a 404 (existing
    behavior, preserved even though the filesystem-exists check is gone)."""
    video = _video(cover_download_path=None)
    serve = AsyncMock()

    with (
        _patch_repo(video),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await download_cover_file(PLATFORM_ID, _request(), _auth())

    assert exc_info.value.status_code == 404
    serve.assert_not_awaited()


@pytest.mark.asyncio
async def test_video_not_found_returns_404():
    serve = AsyncMock()

    with (
        _patch_repo(None),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await download_cover_file(PLATFORM_ID, _request(), _auth())

    assert exc_info.value.status_code == 404
    serve.assert_not_awaited()
