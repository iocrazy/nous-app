"""download_video_file — object-storage-aware download button (Task C2).

Storage unification: `parsed_media.download_path` is now written as an
`sb://` object-store path by the downloader (see
`downloader.py::_upload_downloaded_video_to_s3`). The endpoint used to
treat `download_path` as a filesystem-relative path (`Path(base) / path`
+ `.exists()`), which 404s on every `sb://` row. This mirrors
`test_project_files_download.py`'s pattern: the router must delegate to
`serve_stored_file` unmodified, regardless of the path shape.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app.api.media_download_router import download_video_file
from app.core.deps import AuthContext

pytestmark = pytest.mark.unit

SB_PATH = "sb://library/331438215859255/ab/cd/abcdef1234567890.mp4"
PLATFORM_ID = "7123456789"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _auth() -> AuthContext:
    return AuthContext(user_id="u1", auth_type="jwt")


def _video(**over):
    base = {
        "id": "900",
        "title": "My Clip",
        "download_path": SB_PATH,
    }
    base.update(over)
    return base


def _patch_repo(video=None):
    repo = MagicMock()
    repo.get_by_platform_id = AsyncMock(return_value=video)
    return patch("app.api.media_download_router.MediaRepository", return_value=repo)


@pytest.mark.asyncio
async def test_sb_row_delegated_to_serve_stored_file():
    """sb:// download_path is passed through to serve_stored_file, not
    treated as a filesystem path — this is the regression fix itself."""
    video = _video()
    sentinel = Response(content=b"video-bytes", media_type="video/mp4")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(video),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await download_video_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == SB_PATH
    assert kwargs["mime"] == "video/mp4"
    assert "attachment" in kwargs["disposition"]
    assert "My Clip.mp4" in kwargs["disposition"]


@pytest.mark.asyncio
async def test_chinese_title_disposition_is_latin1_safe():
    """Regression: a Chinese title used to survive the safe_title alnum
    filter (`.isalnum()` is True for CJK) and land raw in a bare
    Content-Disposition header, which blew up on latin-1 encode when
    Starlette wrote the response — HTTP 500 in production. The disposition
    handed to serve_stored_file must always be latin-1 encodable and use
    RFC 5987 `filename*=UTF-8''...` for the non-ASCII payload."""
    video = _video(title="我的视频")
    sentinel = Response(content=b"video-bytes", media_type="video/mp4")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(video),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await download_video_file(PLATFORM_ID, _request(), _auth())

    assert resp is sentinel
    args, kwargs = serve.await_args
    disposition = kwargs["disposition"]
    # Core regression assertion — this raised UnicodeEncodeError pre-fix.
    disposition.encode("latin-1")
    assert "filename*=UTF-8''" in disposition


@pytest.mark.asyncio
async def test_missing_download_path_returns_404():
    """No download_path on the record is still a 404 (existing behavior,
    preserved even though the filesystem-exists check is gone)."""
    video = _video(download_path=None)
    serve = AsyncMock()

    with (
        _patch_repo(video),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await download_video_file(PLATFORM_ID, _request(), _auth())

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
            await download_video_file(PLATFORM_ID, _request(), _auth())

    assert exc_info.value.status_code == 404
    serve.assert_not_awaited()
