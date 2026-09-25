"""GET /projects/{pid}/files/{fid}/stream — the review page's player source.

``project_files`` / ``file_versions`` carry no ``resource_id``, so the review
page's old ``/media/{resource_id}`` URL was always empty: no uploaded project
video could be played. A ``<video src>`` also cannot send a Bearer header, so
this route accepts the signed media token in ``?token=`` (same transport as
``serve_version_file``) and still applies the project read check.
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient

import app.api.media_auth as media_auth
import app.core.scope_guards as scope_guards
import app.services.library.media_serving as media_serving
from app.main import app

r = sys.modules["app.api.projects_router"]
USER = "00000000-0000-0000-0000-000000000042"
URL = "/api/v1/projects/11/files/22/stream"


@pytest.fixture(autouse=True)
def _module_gate_open():
    gate = r.router.dependencies[0].dependency

    async def _allow() -> None:
        return None

    app.dependency_overrides[gate] = _allow
    yield
    app.dependency_overrides.pop(gate, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def wired(monkeypatch):
    seen: dict = {}

    async def _token(value):
        return USER if value == "good" else None

    async def _access(project_id, user_id, *, write):
        seen["access"] = (project_id, user_id, write)
        if user_id != USER:
            raise HTTPException(status_code=403, detail="nope")

    async def _serve(file_path, *, mime, request, disposition="inline", **_):
        seen["served"] = (file_path, mime, disposition)
        return Response(content=b"bytes", media_type=mime)

    class _Svc:
        async def get_file_info(self, project_id, file_id):
            return {"id": 22, "file_path": "p/current.mp4", "mime_type": "video/mp4"}

        async def get_file_versions(self, project_id, file_id):
            return [
                {"id": 31, "file_path": "p/v1.mp4", "mime_type": "video/mp4"},
                {"id": 32, "file_path": "p/v2.mov", "mime_type": "video/quicktime"},
            ]

    monkeypatch.setattr(media_auth, "validate_media_cookie", _token)
    monkeypatch.setattr(scope_guards, "_check_project_access", _access)
    monkeypatch.setattr(media_serving, "serve_stored_file", _serve)
    monkeypatch.setattr(r, "ProjectsService", _Svc)
    return seen


@pytest.mark.asyncio
async def test_media_token_plays_the_current_file_inline(client, wired) -> None:
    resp = await client.get(URL, params={"token": "good"})
    assert resp.status_code == 200, resp.text
    assert wired["access"] == ("11", USER, False)
    assert wired["served"] == ("p/current.mp4", "video/mp4", "inline")


@pytest.mark.asyncio
async def test_version_id_plays_that_version(client, wired) -> None:
    resp = await client.get(URL, params={"token": "good", "version_id": "32"})
    assert resp.status_code == 200, resp.text
    assert wired["served"] == ("p/v2.mov", "video/quicktime", "inline")


@pytest.mark.asyncio
async def test_unknown_version_is_404(client, wired) -> None:
    resp = await client.get(URL, params={"token": "good", "version_id": "99"})
    assert resp.status_code == 404
    assert "served" not in wired


@pytest.mark.asyncio
async def test_no_credentials_is_401(client, wired) -> None:
    resp = await client.get(URL)
    assert resp.status_code == 401
    assert "served" not in wired


@pytest.mark.asyncio
async def test_project_read_check_still_applies(client, wired, monkeypatch) -> None:
    async def _other(value):
        return "11111111-1111-1111-1111-111111111111"

    monkeypatch.setattr(media_auth, "validate_media_cookie", _other)
    resp = await client.get(URL, params={"token": "someone-else"})
    assert resp.status_code == 403
    assert "served" not in wired


@pytest.mark.asyncio
async def test_header_credentials_win_over_the_query_token(
    client, wired, monkeypatch
) -> None:
    from app.core.deps import AuthContext

    async def _never(value):
        raise AssertionError("the media token must not be consulted")

    async def _header_auth(request, authorization, x_api_key):
        assert authorization == "Bearer jwt"
        return AuthContext(user_id=USER, auth_type="jwt")

    monkeypatch.setattr(media_auth, "validate_media_cookie", _never)
    monkeypatch.setattr(scope_guards, "get_auth", _header_auth)
    resp = await client.get(
        URL, params={"token": "good"}, headers={"Authorization": "Bearer jwt"}
    )
    assert resp.status_code == 200, resp.text
    assert wired["access"] == ("11", USER, False)
