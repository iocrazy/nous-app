"""serve_media_cover_by_id (`/media/{id}/cover`) — object-storage-aware
dispatch (Task PR5-A), mirroring the video counterpart `serve_media_by_id`.

`parsed_media.cover_download_path` is about to be migrated to `sb://` rows.
`_serve_file` is filesystem-only and 404s on those. The endpoint must branch
on `resolve_media_source(file_path).is_object_store` the same way
`serve_media_by_id` already does for the video route: `sb://` goes through
`serve_stored_file`, filesystem keeps the original `_serve_file(...,
cache_immutable=True)` call byte-for-byte (zero regression for un-migrated
covers).

Testing note: the `/media/*` routes in `app.main` are defined inside a
`try`/`except` block gated on `Utils.get_download_base_path()` resolving to
a writable directory at import time (see the "媒体文件服务" comment in
`app/main.py`). In this sandbox `DOWNLOAD_PATH` points at a path that
doesn't exist here, so the block's `except` swallows the failure and
`serve_media_cover_by_id` never lands on the module — reproducibly, in any
environment where that path isn't writable. Depending on ambient
`DOWNLOAD_PATH` (and on which test file the interpreter happens to import
`app.main` first, since the try/except only runs once per process) would
make this test flaky rather than deterministic. Instead: force a valid,
writable `DOWNLOAD_PATH` and reload the module so the endpoint is
deterministically registered, then reload back afterward so this test
doesn't leak module state (a stale `_media_base_path`) into any test that
runs after it in the same session.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import Response
from starlette.requests import Request

import app.main as main_module
from app.core.config import settings

pytestmark = pytest.mark.unit

MEDIA_ID = "900"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.fixture
def cover_module(tmp_path, monkeypatch):
    original = settings.DOWNLOAD_PATH
    monkeypatch.setattr(settings, "DOWNLOAD_PATH", str(tmp_path))
    importlib.reload(main_module)
    assert hasattr(main_module, "serve_media_cover_by_id"), (
        "media routes did not register with a valid, writable DOWNLOAD_PATH "
        "— the reload guard itself is broken, not the endpoint under test"
    )
    try:
        yield main_module
    finally:
        monkeypatch.setattr(settings, "DOWNLOAD_PATH", original)
        importlib.reload(main_module)


@pytest.mark.asyncio
async def test_sb_cover_delegated_to_serve_stored_file(cover_module, monkeypatch):
    """A migrated sb:// cover_download_path is dispatched to
    serve_stored_file, not the filesystem-only _serve_file — this is the
    regression fix itself."""
    sb_path = "sb://library/331438215859255/ab/cd/abcdef1234567890.jpg"
    sentinel = Response(content=b"cover-bytes", media_type="image/jpeg")
    serve = AsyncMock(return_value=sentinel)

    monkeypatch.setattr(
        cover_module, "_authenticate_media_request", AsyncMock(return_value="u1")
    )
    monkeypatch.setattr(
        cover_module,
        "_resolve_file_path",
        AsyncMock(return_value=(sb_path, "u1", ())),
    )
    monkeypatch.setattr(cover_module, "_check_permissions", AsyncMock())
    serve_file = MagicMock(side_effect=AssertionError("must not hit _serve_file"))
    monkeypatch.setattr(cover_module, "_serve_file", serve_file)

    with patch("app.services.library.media_serving.serve_stored_file", new=serve):
        resp = await cover_module.serve_media_cover_by_id(MEDIA_ID, _request())

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == sb_path
    assert kwargs["mime"] == "image/jpeg"
    serve_file.assert_not_called()


@pytest.mark.asyncio
async def test_filesystem_cover_uses_serve_file_zero_regression(
    cover_module, monkeypatch
):
    """An un-migrated filesystem cover_download_path still goes through the
    original _serve_file(..., cache_immutable=True) call — no behavior
    change for covers that haven't moved to sb://."""
    fs_path = "global/resources/web/900/cover.jpg"
    sentinel = Response(content=b"cover-bytes", media_type="image/jpeg")

    monkeypatch.setattr(
        cover_module, "_authenticate_media_request", AsyncMock(return_value="u1")
    )
    monkeypatch.setattr(
        cover_module,
        "_resolve_file_path",
        AsyncMock(return_value=(fs_path, "u1", ())),
    )
    monkeypatch.setattr(cover_module, "_check_permissions", AsyncMock())
    serve_file = MagicMock(return_value=sentinel)
    monkeypatch.setattr(cover_module, "_serve_file", serve_file)
    serve = AsyncMock(side_effect=AssertionError("must not hit serve_stored_file"))

    with patch("app.services.library.media_serving.serve_stored_file", new=serve):
        resp = await cover_module.serve_media_cover_by_id(MEDIA_ID, _request())

    assert resp is sentinel
    serve_file.assert_called_once_with(fs_path, cache_immutable=True)
    serve.assert_not_awaited()
