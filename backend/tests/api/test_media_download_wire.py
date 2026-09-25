"""Media download routes: wire parity, byte contracts, and the access guard.

``/media/pending`` and ``/media/retry/{platform_id}`` gained response models
(P5). Each runs over real HTTP; the repository returns a ``parsed_media`` row
built from the ORM mapper with every column set and pushed through the
repository's own ``_orm_obj_to_dict``, so the body must equal what FastAPI
sent for that dict with no model (``tests/api/wire_parity.py``).

The four ``/media/download/{platform_id}[/cover|/music|/gallery]`` routes
return bytes and are declared ``binary_response``.

Also pinned here: every route in this router and in ``media_slides_router``
now asks ``media_access_guard`` first. Before, any signed-in caller could
fetch any user's downloaded files by ``platform_id`` — a public value that
sits in every share URL.
"""

from __future__ import annotations

import io
import zipfile
from contextlib import asynccontextmanager
from typing import Any, List
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient

import app.api.media_access_guard as guard
import app.api.media_fetch_helpers as fetch_helpers
import app.db.scope as scope_mod
import app.db.session as session_mod
from app.core.cache import module_gate_cache
from app.core.deps import AuthContext, get_auth, get_optional_auth
from app.main import app
from app.models import ParsedMedia
from app.repositories._orm_helpers import _orm_obj_to_dict
from app.repositories.media_repository import _PM_NAME_TO_ATTR, MediaRepository
from app.repositories.resources_repository import ResourcesRepository
from app.schemas.parsed_media_row import ParsedMediaRow
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OTHER = "00000000-0000-0000-0000-000000000099"
PLATFORM_ID = "7300000000000000555"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


def _media_row(**overrides: Any) -> dict:
    row = _orm_obj_to_dict(sample_orm(ParsedMedia), _PM_NAME_TO_ATTR)
    row.update(overrides)
    return row


class _Result:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _Db:
    """What the guard's two queries see: resources on the media, then a team
    hit (or not)."""

    owners: List[tuple] = []
    team_hit: bool = False
    calls: int = 0


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[get_optional_auth] = _fake_auth
    _Db.owners = [(7300000000000000001, USER)]
    _Db.team_hit = False
    _Db.calls = 0

    class _Session:
        async def execute(self, stmt):
            _Db.calls += 1
            if _Db.calls % 2 == 1:
                return _Result(_Db.owners)
            return _Result([(1,)] if _Db.team_hit else [])

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(session_mod, "read_scope", _scope)
    monkeypatch.setattr(scope_mod, "is_enforced", lambda table: False)
    module_gate_cache.clear()
    monkeypatch.setattr(
        "app.services.modules.registry._read_raw",
        AsyncMock(return_value={"enabled": True, "visible": True}),
    )
    yield
    module_gate_cache.clear()
    app.dependency_overrides.pop(get_auth, None)
    app.dependency_overrides.pop(get_optional_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------------- #
# Model ↔ source pin
# --------------------------------------------------------------------------- #


def test_parsed_media_row_declares_every_orm_column() -> None:
    assert set(ParsedMediaRow.model_fields) == column_names(ParsedMedia)


# --------------------------------------------------------------------------- #
# GET /media/pending
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pending_wire_unchanged(client, monkeypatch) -> None:
    rows = [_media_row(), _media_row(id=7300000000000000777, published_at=None)]
    fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr(MediaRepository, "get_pending_downloads", fetch)

    resp = await client.get("/api/v1/media/pending?limit=5")

    assert_wire_unchanged(resp, {"success": True, "count": 2, "videos": rows})
    assert fetch.await_args.kwargs["user_id"] == USER
    # Snowflake id stays a JSON number, datetimes keep ``+00:00``.
    assert resp.json()["videos"][0]["id"] == rows[0]["id"]
    assert resp.json()["videos"][0]["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_pending_empty(client, monkeypatch) -> None:
    monkeypatch.setattr(
        MediaRepository, "get_pending_downloads", AsyncMock(return_value=[])
    )
    resp = await client.get("/api/v1/media/pending")
    assert_wire_unchanged(resp, {"success": True, "count": 0, "videos": []})


# --------------------------------------------------------------------------- #
# POST /media/retry/{platform_id}
# --------------------------------------------------------------------------- #


def _retry_wiring(monkeypatch, *, dispatch_task_id: str | None) -> AsyncMock:
    video = _media_row(platform_id=PLATFORM_ID, media_type="4")
    monkeypatch.setattr(
        MediaRepository, "get_by_platform_id", AsyncMock(return_value=video)
    )
    monkeypatch.setattr(MediaRepository, "update", AsyncMock(return_value=video))
    monkeypatch.setattr(
        ResourcesRepository,
        "get_resource_by_media_id_and_creator",
        AsyncMock(return_value={"id": 7300000000000000001}),
    )
    dispatch = AsyncMock(
        return_value={
            "task_id": dispatch_task_id,
            "types_submitted": ["video"],
            "types_skipped": [],
            "types_subscribed": [],
            "already_in_library": False,
        }
    )
    monkeypatch.setattr(fetch_helpers, "dedup_and_dispatch", dispatch)
    monkeypatch.setattr(
        "app.api.media_download_router.log_user_action", AsyncMock(return_value=None)
    )
    return dispatch


@pytest.mark.asyncio
async def test_retry_wire_and_task_id(client, monkeypatch) -> None:
    """``task_id`` is the dispatched task — it used to read a key the
    dispatch result never had and was always ``null``."""
    task_id = "0b7c1f7e-5d0e-4d4a-9e55-1a2b3c4d5e6f"
    dispatch = _retry_wiring(monkeypatch, dispatch_task_id=task_id)

    resp = await client.post(f"/api/v1/media/retry/{PLATFORM_ID}", json={})

    assert_wire_unchanged(
        resp,
        {"success": True, "message": "Download task resubmitted", "task_id": task_id},
    )
    assert dispatch.await_args.kwargs["user_id"] == USER


@pytest.mark.asyncio
async def test_retry_nothing_dispatched_is_null_task(client, monkeypatch) -> None:
    _retry_wiring(monkeypatch, dispatch_task_id=None)
    resp = await client.post(f"/api/v1/media/retry/{PLATFORM_ID}", json={})
    assert_wire_unchanged(
        resp,
        {"success": True, "message": "Download task resubmitted", "task_id": None},
    )


@pytest.mark.asyncio
async def test_retry_foreign_media_is_404_and_not_dispatched(
    client, monkeypatch
) -> None:
    dispatch = _retry_wiring(monkeypatch, dispatch_task_id="t")
    _Db.owners = [(7300000000000000001, OTHER)]

    resp = await client.post(f"/api/v1/media/retry/{PLATFORM_ID}", json={})

    assert resp.status_code == 404
    dispatch.assert_not_awaited()
    MediaRepository.update.assert_not_awaited()


# --------------------------------------------------------------------------- #
# File routes (bytes)
# --------------------------------------------------------------------------- #


FILE_ROUTES = {
    "/api/v1/media/download/{platform_id}": {"video/mp4"},
    "/api/v1/media/download/{platform_id}/cover": {"image/jpeg"},
    "/api/v1/media/download/{platform_id}/music": {"audio/*"},
    "/api/v1/media/download/{platform_id}/gallery": {"application/zip"},
}


@pytest.mark.parametrize("path,media_types", sorted(FILE_ROUTES.items()))
def test_file_routes_are_declared_binary(path, media_types) -> None:
    op = app.openapi()["paths"][path]["get"]
    content = op["responses"]["200"]["content"]
    assert set(content) == media_types
    for body in content.values():
        assert body["schema"] == {"type": "string", "format": "binary"}


def _serve(monkeypatch, payload: bytes, mime: str) -> AsyncMock:
    serve = AsyncMock(return_value=Response(content=payload, media_type=mime))
    monkeypatch.setattr("app.services.library.media_serving.serve_stored_file", serve)
    return serve


@pytest.mark.asyncio
async def test_video_download_serves_bytes(client, monkeypatch) -> None:
    video = _media_row(platform_id=PLATFORM_ID, title="My Clip")
    monkeypatch.setattr(
        MediaRepository, "get_by_platform_id", AsyncMock(return_value=video)
    )
    serve = _serve(monkeypatch, b"MP4DATA", "video/mp4")

    resp = await client.get(f"/api/v1/media/download/{PLATFORM_ID}")

    assert resp.status_code == 200, resp.text
    assert resp.content == b"MP4DATA"
    assert serve.await_args.args[0] == video["download_path"]


@pytest.mark.asyncio
async def test_cover_download_serves_bytes(client, monkeypatch) -> None:
    video = _media_row(platform_id=PLATFORM_ID)
    monkeypatch.setattr(
        MediaRepository, "get_by_platform_id", AsyncMock(return_value=video)
    )
    _serve(monkeypatch, b"JPEGDATA", "image/jpeg")
    resp = await client.get(f"/api/v1/media/download/{PLATFORM_ID}/cover")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"JPEGDATA"


@pytest.mark.asyncio
async def test_music_download_serves_bytes(client, monkeypatch, tmp_path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "audio.m4a").write_bytes(b"x")
    video = _media_row(
        platform_id=PLATFORM_ID, extract_audio_path="a/audio.m4a", title="Song"
    )
    monkeypatch.setattr(
        MediaRepository, "get_by_platform_id", AsyncMock(return_value=video)
    )
    monkeypatch.setattr(
        "app.core.utils.Utils.get_download_base_path", lambda: str(tmp_path)
    )
    serve = _serve(monkeypatch, b"M4ADATA", "audio/mp4")

    resp = await client.get(f"/api/v1/media/download/{PLATFORM_ID}/music")

    assert resp.status_code == 200, resp.text
    assert resp.content == b"M4ADATA"
    assert serve.await_args.kwargs["mime"] == "audio/mp4"


@pytest.mark.asyncio
async def test_gallery_zip_serves_archive(client, monkeypatch, tmp_path) -> None:
    slides = tmp_path / "g" / "slides"
    slides.mkdir(parents=True)
    (slides / "001.jpg").write_bytes(b"ONE")
    (slides / "002.mp4").write_bytes(b"TWO")
    (slides / "notes.txt").write_bytes(b"skip")
    video = _media_row(platform_id=PLATFORM_ID, download_path="g", title="Album")
    monkeypatch.setattr(
        MediaRepository, "get_by_platform_id", AsyncMock(return_value=video)
    )
    monkeypatch.setattr(
        "app.core.utils.Utils.get_download_base_path", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        "app.api.media_slides_router._resolve_album_location",
        AsyncMock(return_value=None),
    )

    resp = await client.get(f"/api/v1/media/download/{PLATFORM_ID}/gallery")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        assert sorted(archive.namelist()) == ["001.jpg", "002.mp4"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "/cover", "/music", "/gallery"])
async def test_foreign_media_file_is_404(client, monkeypatch, suffix) -> None:
    """Another user's media: 404 like a missing row, nothing served."""
    video = _media_row(platform_id=PLATFORM_ID, extract_audio_path="a/audio.m4a")
    monkeypatch.setattr(
        MediaRepository, "get_by_platform_id", AsyncMock(return_value=video)
    )
    serve = _serve(monkeypatch, b"SECRET", "video/mp4")
    _Db.owners = [(7300000000000000001, OTHER)]

    resp = await client.get(f"/api/v1/media/download/{PLATFORM_ID}{suffix}")

    assert resp.status_code == 404
    assert b"SECRET" not in resp.content
    serve.assert_not_awaited()


# --------------------------------------------------------------------------- #
# The guard itself
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_guard_allows_creator() -> None:
    _Db.owners = [(1, OTHER), (2, USER)]
    assert await guard.caller_can_read_media("5", USER) is True


@pytest.mark.asyncio
async def test_guard_allows_team_member_of_a_filing_team() -> None:
    _Db.owners = [(1, OTHER)]
    _Db.team_hit = True
    assert await guard.caller_can_read_media("5", USER) is True


@pytest.mark.asyncio
async def test_guard_denies_outsider() -> None:
    _Db.owners = [(1, OTHER)]
    _Db.team_hit = False
    assert await guard.caller_can_read_media("5", USER) is False


@pytest.mark.asyncio
async def test_guard_allows_media_without_any_resource() -> None:
    """Legacy / system rows: the same allowance ``/media/{id}`` makes."""
    _Db.owners = []
    assert await guard.caller_can_read_media("5", USER) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("media_id,user_id", [("abc", USER), ("5", None), (None, USER)])
async def test_guard_denies_bad_input(media_id, user_id) -> None:
    _Db.owners = []
    assert await guard.caller_can_read_media(media_id, user_id) is False
    assert _Db.calls == 0
