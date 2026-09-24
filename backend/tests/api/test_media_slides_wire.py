"""Media slides / audio / lyrics routes: wire parity, byte contracts, access.

``GET /media/{id}/slides``, ``GET /media/{id}/lyrics`` and
``POST /media/{id}/lyrics/fetch`` gained response models (P5); each body must
equal ``jsonable_encoder`` of the dict the handler builds
(``tests/api/wire_parity.py``). Lyrics come from the one writer of
``metadata.lyrics`` (``lyrics_payload_from_track``) run on a real Soda timed
lyric, so every key the model declares is one production stores.

``/slides/{filename}`` and ``/audio`` return bytes (``binary_response``).

Every route asks ``media_access_guard`` first (see
``test_media_download_wire.py`` for the guard's own rules); the ``?token=``
file routes now check the user the signed media token was issued to, where
before any valid token opened any media.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, List
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient

import app.db.scope as scope_mod
import app.db.session as session_mod
from app.core.deps import AuthContext, get_auth, get_optional_auth
from app.main import app
from app.models import ParsedMedia
from app.repositories._orm_helpers import _orm_obj_to_dict
from app.repositories.media_repository import _PM_NAME_TO_ATTR, MediaRepository
from app.services.media.parsers.soda_music.lyrics import lyrics_payload_from_track
from tests.api.wire_parity import assert_wire_unchanged, sample_orm

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OTHER = "00000000-0000-0000-0000-000000000099"
MEDIA_ID = "7300000000000000555"

SODA_LYRIC = "\n".join(
    ["[1000,2000]<0,500,0>Hello<500,1500,0> world", "[3000,1000]<0,1000,0>Again"]
)


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


async def _no_auth() -> None:
    return None


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
    owners: List[tuple] = []


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[get_optional_auth] = _fake_auth
    _Db.owners = [(7300000000000000001, USER)]

    class _Session:
        calls = 0

        async def execute(self, stmt):
            _Session.calls += 1
            if _Session.calls % 2 == 1:
                return _Result(_Db.owners)
            return _Result([])

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(session_mod, "read_scope", _scope)
    monkeypatch.setattr(scope_mod, "is_enforced", lambda table: False)
    monkeypatch.setattr(
        "app.api.media_slides_router._resolve_album_location",
        AsyncMock(return_value=None),
    )
    yield
    app.dependency_overrides.pop(get_auth, None)
    app.dependency_overrides.pop(get_optional_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _album(tmp_path, monkeypatch) -> dict:
    slides = tmp_path / "album" / "slides"
    slides.mkdir(parents=True)
    for name, data in (("001.jpg", b"ONE"), ("002.mp4", b"TWO"), ("x.txt", b"no")):
        (slides / name).write_bytes(data)
    (slides / "sub").mkdir()
    monkeypatch.setattr(
        "app.core.utils.Utils.get_download_base_path", lambda: str(tmp_path)
    )
    row = _media_row(id=int(MEDIA_ID), download_path="album")
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))
    return row


# --------------------------------------------------------------------------- #
# GET /media/{id}/slides
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_slides_filesystem_wire(client, monkeypatch, tmp_path) -> None:
    _album(tmp_path, monkeypatch)
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/slides")
    slides = [
        {
            "name": "001.jpg",
            "type": "image",
            "media_type": "image/jpg",
            "url": f"/api/v1/media/{MEDIA_ID}/slides/001.jpg",
        },
        {
            "name": "002.mp4",
            "type": "video",
            "media_type": "video/mp4",
            "url": f"/api/v1/media/{MEDIA_ID}/slides/002.mp4",
        },
    ]
    assert_wire_unchanged(resp, {"slides": slides, "count": 2})


@pytest.mark.asyncio
async def test_list_slides_object_store_wire(client, monkeypatch) -> None:
    from app.services.library.media_storage import ObjectStore, resolve_media_source

    loc = resolve_media_source("sb://library/t5/album/123/")
    monkeypatch.setattr(
        "app.api.media_slides_router._resolve_album_location",
        AsyncMock(return_value=loc),
    )
    prefix = "t5/album/123/"
    monkeypatch.setattr(
        ObjectStore,
        "list_prefix",
        AsyncMock(
            return_value=[
                f"{prefix}cover.jpg",
                f"{prefix}slides/002.webp",
                f"{prefix}slides/001.png",
            ]
        ),
    )
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/slides")
    raw = {
        "slides": [
            {
                "name": n,
                "type": "image",
                "media_type": f"image/{n.split('.')[-1]}",
                "url": f"/api/v1/media/{MEDIA_ID}/slides/{n}",
            }
            for n in ("001.png", "002.webp")
        ],
        "count": 2,
    }
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_list_slides_foreign_media_is_404(client, monkeypatch, tmp_path) -> None:
    _album(tmp_path, monkeypatch)
    _Db.owners = [(1, OTHER)]
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/slides")
    assert resp.status_code == 404
    assert "001.jpg" not in resp.text


# --------------------------------------------------------------------------- #
# GET /media/{id}/slides/{filename} and /audio (bytes)
# --------------------------------------------------------------------------- #


def test_file_routes_are_declared_binary() -> None:
    paths = app.openapi()["paths"]
    slide = paths["/api/v1/media/{media_id}/slides/{filename}"]["get"]
    audio = paths["/api/v1/media/{media_id}/audio"]["get"]
    assert set(slide["responses"]["200"]["content"]) == {
        "image/*",
        "video/*",
        "application/octet-stream",
    }
    assert set(audio["responses"]["200"]["content"]) == {"audio/*"}
    for op in (slide, audio):
        for body in op["responses"]["200"]["content"].values():
            assert body["schema"] == {"type": "string", "format": "binary"}


@pytest.mark.asyncio
async def test_serve_slide_bytes(client, monkeypatch, tmp_path) -> None:
    _album(tmp_path, monkeypatch)
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/slides/001.jpg")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"ONE"
    assert resp.headers["content-type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_serve_audio_bytes(client, monkeypatch, tmp_path) -> None:
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "a.mp3").write_bytes(b"x")
    monkeypatch.setattr(
        "app.core.utils.Utils.get_download_base_path", lambda: str(tmp_path)
    )
    row = _media_row(music_download_path="m/a.mp3")
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))
    serve = AsyncMock(return_value=Response(content=b"MP3", media_type="audio/mpeg"))
    monkeypatch.setattr("app.services.library.media_serving.serve_stored_file", serve)

    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/audio")

    assert resp.status_code == 200, resp.text
    assert resp.content == b"MP3"
    assert serve.await_args.kwargs["mime"] == "audio/mpeg"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["slides/001.jpg", "audio"])
async def test_token_caller_is_checked_against_the_media(
    client, monkeypatch, tmp_path, path
) -> None:
    """``?token=`` resolves to the user it was issued to, and that user must
    be able to read the media — a valid token alone used to open any media."""
    _album(tmp_path, monkeypatch)
    app.dependency_overrides[get_optional_auth] = _no_auth
    monkeypatch.setattr(
        "app.api.media_auth.validate_media_cookie", AsyncMock(return_value=OTHER)
    )
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/{path}?token=signed")
    assert resp.status_code == 404
    assert b"ONE" not in resp.content


@pytest.mark.asyncio
async def test_token_owner_gets_the_slide(client, monkeypatch, tmp_path) -> None:
    _album(tmp_path, monkeypatch)
    app.dependency_overrides[get_optional_auth] = _no_auth
    monkeypatch.setattr(
        "app.api.media_auth.validate_media_cookie", AsyncMock(return_value=USER)
    )
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/slides/001.jpg?token=signed")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"ONE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token,verified,status",
    [(None, None, 401), ("bad", None, 401)],
)
async def test_file_routes_still_need_a_caller(
    client, monkeypatch, token, verified, status
) -> None:
    app.dependency_overrides[get_optional_auth] = _no_auth
    monkeypatch.setattr(
        "app.api.media_auth.validate_media_cookie", AsyncMock(return_value=verified)
    )
    query = f"?token={token}" if token else ""
    for path in ("slides/001.jpg", "audio"):
        resp = await client.get(f"/api/v1/media/{MEDIA_ID}/{path}{query}")
        assert resp.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["slides/001.jpg", "audio"])
async def test_foreign_media_file_is_404(client, monkeypatch, tmp_path, path) -> None:
    _album(tmp_path, monkeypatch)
    _Db.owners = [(1, OTHER)]
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/{path}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Lyrics
# --------------------------------------------------------------------------- #


def _soda_payload() -> dict:
    return lyrics_payload_from_track({"lyric": {"content": SODA_LYRIC}})


@pytest.mark.asyncio
async def test_get_lyrics_wire(client, monkeypatch) -> None:
    payload = _soda_payload()
    assert payload["lines"] and payload["lines"][0]["tokens"]
    row = _media_row(metadata={"lyrics": payload, "other": 1})
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))

    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/lyrics")

    assert_wire_unchanged(resp, payload)


@pytest.mark.asyncio
async def test_get_lyrics_empty_wire(client, monkeypatch) -> None:
    row = _media_row(metadata={})
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/lyrics")
    assert_wire_unchanged(resp, {"lrc": "", "lines": []})


@pytest.mark.asyncio
async def test_get_lyrics_foreign_is_404(client, monkeypatch) -> None:
    row = _media_row(metadata={"lyrics": _soda_payload()})
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))
    _Db.owners = [(1, OTHER)]
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/lyrics")
    assert resp.status_code == 404
    assert "Hello" not in resp.text


def _fetch_wiring(monkeypatch) -> tuple[AsyncMock, AsyncMock]:
    row = _media_row(
        platform_id="soda-1", source_platform="qishui", metadata={"keep": True}
    )
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))
    update = AsyncMock(return_value=row)
    monkeypatch.setattr(MediaRepository, "update", update)
    fetch = AsyncMock(return_value=_soda_payload())
    monkeypatch.setattr(
        "app.services.media.lyrics_fetch_service._fetch_qishui_lyrics", fetch
    )
    return fetch, update


@pytest.mark.asyncio
async def test_fetch_lyrics_wire(client, monkeypatch) -> None:
    fetch, update = _fetch_wiring(monkeypatch)
    resp = await client.post(f"/api/v1/media/{MEDIA_ID}/lyrics/fetch")
    assert_wire_unchanged(resp, _soda_payload())
    assert fetch.await_args.args == ("soda-1", USER)
    assert update.await_args.args[1]["metadata"]["keep"] is True


@pytest.mark.asyncio
async def test_fetch_lyrics_foreign_is_404_and_not_fetched(client, monkeypatch) -> None:
    fetch, update = _fetch_wiring(monkeypatch)
    _Db.owners = [(1, OTHER)]
    resp = await client.post(f"/api/v1/media/{MEDIA_ID}/lyrics/fetch")
    assert resp.status_code == 404
    fetch.assert_not_awaited()
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_hand_edited_historical_lyrics_still_read(client, monkeypatch) -> None:
    """Same ruling as ``resources.lyrics_json``: a row whose lines lack keys,
    carry extra keys, or hold junk must read back, not 500. Missing keys stay
    absent (not ``null``); extra keys pass through; non-object lines drop."""
    lines = [
        {"text": "only text"},
        {"text": "b", "line_start_ms": 5, "note": "hand-edited"},
        {"line_start_ms": 9, "tokens": [{"text": "w"}]},
        "junk",
    ]
    row = _media_row(metadata={"lyrics": {"lrc": None, "lines": lines}})
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))

    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/lyrics")

    assert_wire_unchanged(resp, {"lrc": "", "lines": lines[:3]})


@pytest.mark.asyncio
@pytest.mark.parametrize("lyrics", [None, "not an object", {"lines": None}])
async def test_malformed_lyrics_read_as_empty(client, monkeypatch, lyrics) -> None:
    row = _media_row(metadata={"lyrics": lyrics})
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))
    resp = await client.get(f"/api/v1/media/{MEDIA_ID}/lyrics")
    assert_wire_unchanged(resp, {"lrc": "", "lines": []})
