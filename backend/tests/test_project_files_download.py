"""Project file download endpoint — dedicated route (spec: project_files
download dead-end).

`GET /projects/{project_id}/files/{file_id}/download` routes through
`serve_stored_file`, so `project_files.file_path` rows written as either a
legacy filesystem-relative path or an `sb://` object-store row (post storage
unification) are servable — mirroring
`resources_versions_router.py::serve_version_file`'s pattern (same P3 nginx
redirect exemption: this endpoint forces `Content-Disposition: attachment`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from starlette.requests import Request

from app.api.projects_router import download_file, router
from app.core.deps import AuthContext
from app.core.scope_guards import verify_project_read_access

pytestmark = pytest.mark.unit

SB_PATH = "sb://library/t42/ab/cd/abcdef1234.png"
FS_PATH = "2026/07/06/u1/project_asset.pdf"


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _auth() -> AuthContext:
    return AuthContext(user_id="u1", auth_type="jwt")


def _file_record(**over):
    base = {
        "id": "900",
        "project_id": "42",
        "filename": "shotlist.pdf",
        "file_path": FS_PATH,
        "mime_type": "application/pdf",
    }
    base.update(over)
    return base


def _patch_service(file_record=None, error: Exception | None = None):
    svc = MagicMock()
    if error is not None:
        svc.get_file_info = AsyncMock(side_effect=error)
    else:
        svc.get_file_info = AsyncMock(return_value=file_record)
    return patch("app.api.projects_router.ProjectsService", return_value=svc)


@pytest.mark.asyncio
async def test_legacy_fs_row_served_as_attachment():
    """Legacy filesystem row → serve_stored_file is called with the fs path
    and a Content-Disposition: attachment header carrying the filename."""
    rec = _file_record()
    sentinel = Response(content=b"pdf-bytes")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_service(rec),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await download_file(rec["project_id"], rec["id"], _request(), _auth())

    assert resp is sentinel
    serve.assert_awaited_once()
    args, kwargs = serve.await_args
    assert args[0] == FS_PATH
    assert kwargs["mime"] == "application/pdf"
    assert kwargs["disposition"] == 'attachment; filename="shotlist.pdf"'


@pytest.mark.asyncio
async def test_sb_row_served_via_shared_reader():
    """sb:// object-store row → routes through the same serve_stored_file
    call, unmodified except for the file_path shape."""
    rec = _file_record(file_path=SB_PATH, mime_type="image/png", filename="ref.png")
    sentinel = Response(content=b"sb-bytes", media_type="image/png")
    serve = AsyncMock(return_value=sentinel)

    with (
        _patch_service(rec),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        resp = await download_file(rec["project_id"], rec["id"], _request(), _auth())

    assert resp is sentinel
    args, kwargs = serve.await_args
    assert args[0] == SB_PATH
    assert kwargs["mime"] == "image/png"
    assert kwargs["disposition"] == 'attachment; filename="ref.png"'


@pytest.mark.asyncio
async def test_wrong_project_or_missing_file_returns_404():
    """ProjectsService.get_file_info raises ValueError for both a missing
    file and a file that belongs to a different project (service-layer
    membership check) — the router must translate that into 404."""
    serve = AsyncMock()

    with (
        _patch_service(error=ValueError("File not found in this project")),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await download_file("42", "900", _request(), _auth())

    assert exc_info.value.status_code == 404
    serve.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_file_path_returns_404():
    """A file row with no file_path (should not happen, but defensive) is
    also a 404 rather than a 500 from serve_stored_file."""
    rec = _file_record(file_path=None)
    serve = AsyncMock()

    with (
        _patch_service(rec),
        patch("app.services.library.media_serving.serve_stored_file", new=serve),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await download_file(rec["project_id"], rec["id"], _request(), _auth())

    assert exc_info.value.status_code == 404
    serve.assert_not_awaited()


def _flat_dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _flat_dependency_calls(dep)


def test_download_route_wired_with_read_access_guard():
    """Structural check (mirrors test_projects_authz_wiring.py): the new
    route must declare verify_project_read_access, same guard as the
    sibling GET /files and GET /files/{file_id} routes — not a bespoke or
    missing guard."""
    route = next(
        (
            r
            for r in router.routes
            if getattr(r, "path", None)
            == "/projects/{project_id}/files/{file_id}/download"
            and "GET" in r.methods
        ),
        None,
    )
    assert route is not None, "download route not registered"
    calls = set(_flat_dependency_calls(route.dependant))
    assert verify_project_read_access in calls
