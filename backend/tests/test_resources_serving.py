"""Resources read endpoints through the shared reader (storage unification).

Task 2.3 (spec 2026-07-12): `/resources/{id}/file`, `/cover` and the version
file endpoint route through `serve_stored_file`, so `sb://` rows written by
the dual-track uploads are servable while legacy filesystem rows keep
byte-identical behavior. (The P3 nginx direct-serve 302 for legacy fs
rows was retired 2026-09-07 — no row has a local path any more.)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import Response
from starlette.requests import Request

from app.api.resources_crud_router import serve_resource_file
from app.api.resources_versions_router import serve_version_file

SB_PATH = "sb://library/t42/ab/cd/abcdef1234.png"
FS_PATH = "2026/07/06/u1/video.mp4"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _resource(**over):
    base = {
        "id": "324520385049690113",
        "media_id": None,
        "file_path": FS_PATH,
        "mime_type": "video/mp4",
    }
    base.update(over)
    return base


def _patch_repo(resource):
    repo = MagicMock()
    repo.get_resource_by_id = AsyncMock(return_value=resource)
    return patch("app.api.resources_crud_router.ResourcesRepository", return_value=repo)


def _patch_access():
    return patch(
        "app.api.media_permissions.check_media_access",
        new=AsyncMock(return_value=True),
    )


@pytest.mark.asyncio
async def test_sb_row_served_via_serve_stored_file():
    """sb:// file_path → the shared reader's response is returned verbatim."""
    res = _resource(file_path=SB_PATH, mime_type="image/png")
    sentinel = Response(content=b"sb-bytes", media_type="image/png")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(res),
        _patch_access(),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_resource_file(
            res["id"],
            _request(),
            authorization=None,
            x_api_key=None,
            token=None,
            share_token=None,
        )

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == SB_PATH
    assert kwargs["mime"] == "image/png"


@pytest.mark.asyncio
async def test_legacy_row_falls_through_to_serve_stored_file():
    """Legacy fs row → the shared reader (FileResponse path lives in
    serve_stored_file now)."""
    res = _resource()
    sentinel = Response(content=b"fs-bytes")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_repo(res),
        _patch_access(),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_resource_file(
            res["id"],
            _request(),
            authorization=None,
            x_api_key=None,
            token=None,
            share_token=None,
        )

    assert resp is sentinel
    args, kwargs = serve.await_args
    assert args[0] == FS_PATH
    assert kwargs["mime"] == "video/mp4"


# ─── Version file endpoint (resources_versions_router) ───────────────────────


def _patch_version_repo(version):
    repo = MagicMock()
    repo.get_version_by_id = AsyncMock(return_value=version)
    return patch(
        "app.api.resources_versions_router.ResourcesRepository", return_value=repo
    )


def _version(**over):
    base = {
        "id": "555",
        "resource_id": "324520385049690113",
        "file_path": SB_PATH,
        "filename": "v2.png",
        "mime_type": "image/png",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_version_sb_row_served_via_serve_stored_file():
    """Version originals route through the shared reader too, and the
    download filename survives."""
    ver = _version()
    sentinel = Response(content=b"v-bytes")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_version_repo(ver),
        _patch_access(),
        patch(
            "app.api.media_auth.validate_media_cookie",
            new=AsyncMock(return_value="u1"),
        ),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_version_file(
            ver["resource_id"],
            ver["id"],
            _request(),
            authorization=None,
            x_api_key=None,
            token="tok",
        )

    assert resp is sentinel
    args, kwargs = serve.await_args
    assert args[0] == SB_PATH
    assert kwargs["mime"] == "image/png"
    assert kwargs["disposition"] == 'attachment; filename="v2.png"'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_path,mime",
    [(FS_PATH, "video/mp4"), (SB_PATH, "image/png")],
    ids=["legacy-fs-row", "sb-row"],
)
async def test_version_download_serves_both_shapes_as_attachment(file_path, mime):
    """Every row shape goes through serve_stored_file with
    `Content-Disposition: attachment` — the version download must never
    open inline."""
    ver = _version(file_path=file_path, mime_type=mime)
    sentinel = Response(content=b"v-bytes")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_version_repo(ver),
        _patch_access(),
        patch(
            "app.api.media_auth.validate_media_cookie",
            new=AsyncMock(return_value="u1"),
        ),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await serve_version_file(
            ver["resource_id"],
            ver["id"],
            _request(),
            authorization=None,
            x_api_key=None,
            token="tok",
        )

    assert resp is sentinel
    args, kwargs = serve.await_args
    assert args[0] == file_path
    assert kwargs["disposition"].startswith("attachment;")
