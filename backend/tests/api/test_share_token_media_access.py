"""A share link opens the media of the resource it shares — and nothing else.

The public share page (``frontend/pages/SharePage.tsx``) loads files with
``?share_token=``. Three things were wrong with that path:

- the album routes (``/media/{id}/slides``, ``/slides/{name}``, ``/audio``)
  demanded a user, so a shared album never loaded;
- the token was the bare share code for every share, so the files behind a
  password-protected share were readable without the password;
- the legacy ``/media/{file_path}`` route let ANY non-empty ``share_token``
  through without looking at it, serving any file under the media root.

``app/api/share_access.py`` now decides what a token opens: a share grant
(handed out after the password check), or the bare code of a share with no
password. The share must be live, and it only opens its own resource's media.
"""

from __future__ import annotations

import datetime as dt
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient

import app.api.media_permissions as media_permissions
import app.api.share_access as share_access
import app.db.scope as scope_mod
import app.db.session as session_mod
from app.api import media_access_guard as guard
from app.core.config import settings
from app.core.deps import get_auth, get_optional_auth
from app.main import app
from app.models import ParsedMedia
from app.repositories._orm_helpers import _orm_obj_to_dict
from app.repositories.media_repository import _PM_NAME_TO_ATTR, MediaRepository
from app.services.library.share_passwords import share_password_columns
from tests.api.wire_parity import sample_orm

pytestmark = pytest.mark.unit

MEDIA = 7300000000000000900
OTHER_MEDIA = 7300000000000000901
RES_SHARED = 7300000000000000001
RES_OTHER = 7300000000000000002
SHARE_ID = 7300000000000000777
CODE = "Open1234"
LOCKED_CODE = "Lock1234"
LOCKED_ID = 7300000000000000778
FUTURE = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
PAST = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)

# resource id -> media id
RESOURCES: Dict[int, int] = {RES_SHARED: MEDIA, RES_OTHER: OTHER_MEDIA}


def _share(**overrides: Any) -> dict:
    row = {
        "id": SHARE_ID,
        "share_code": CODE,
        "share_type": "link",
        "resource_id": RES_SHARED,
        "status": "active",
        "expires_at": FUTURE,
        "max_views": None,
        "view_count": 0,
        **share_password_columns(overrides.pop("password", None)),
    }
    row.update(overrides)
    return row


class _Rows:
    def __init__(self, rows: List[tuple]):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def shares(monkeypatch) -> Dict[int, dict]:
    """A tiny ``shares`` table behind ``share_access.load_share`` and a tiny
    ``resources`` table behind the guard's candidate lookup."""
    table: Dict[int, dict] = {
        SHARE_ID: _share(),
        LOCKED_ID: _share(id=LOCKED_ID, share_code=LOCKED_CODE, password="s3cret"),
    }

    async def _load(*, code=None, share_id=None):
        for s in table.values():
            if (code is not None and s["share_code"] == code) or (
                share_id is not None and s["id"] == share_id
            ):
                return dict(s)
        return None

    class _Session:
        async def execute(self, stmt):
            sql = str(stmt)
            (value,) = [v for v in stmt.compile().params.values()]
            if "resources.media_id =" in sql:
                return _Rows([(rid,) for rid, mid in RESOURCES.items() if mid == value])
            if "resources.id =" in sql:
                return _Rows([(value,)] if value in RESOURCES else [])
            raise AssertionError(f"unexpected query: {sql}")

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(share_access, "load_share", _load)
    monkeypatch.setattr(session_mod, "read_scope", _scope)
    monkeypatch.setattr(scope_mod, "is_enforced", lambda table: False)
    monkeypatch.setattr(settings, "MEDIA_TOKEN_SECRET", "test-media-secret")
    return table


def _grant(share: dict, **kw: Any) -> str:
    return share_access.sign_share_grant(share, **kw)


# --------------------------------------------------------------------------- #
# What a token opens
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_bare_code_opens_its_own_resource_only(shares) -> None:
    check = media_permissions._validate_share_token
    assert await check(CODE, str(RES_SHARED)) is True
    assert await check(CODE, str(RES_OTHER)) is False


@pytest.mark.asyncio
async def test_bare_code_never_opens_a_password_share(shares) -> None:
    assert (
        await media_permissions._validate_share_token(LOCKED_CODE, str(RES_SHARED))
        is False
    )


@pytest.mark.asyncio
async def test_grant_opens_a_password_share(shares) -> None:
    grant = _grant(shares[LOCKED_ID])
    assert await media_permissions._validate_share_token(grant, str(RES_SHARED)) is True
    assert await media_permissions._validate_share_token(grant, str(RES_OTHER)) is False


@pytest.mark.asyncio
async def test_grant_dies_with_a_password_change(shares) -> None:
    grant = _grant(shares[LOCKED_ID])
    shares[LOCKED_ID].update(share_password_columns("rotated"))
    assert (
        await media_permissions._validate_share_token(grant, str(RES_SHARED)) is False
    )
    # removing the password also revokes it
    shares[LOCKED_ID].update(share_password_columns(None))
    assert (
        await media_permissions._validate_share_token(grant, str(RES_SHARED)) is False
    )


@pytest.mark.asyncio
async def test_tampered_or_stale_grant_is_refused(shares) -> None:
    grant = _grant(shares[LOCKED_ID])
    prefix, sid, exp, sig = grant.split(".")
    forged = f"{prefix}.{SHARE_ID}.{exp}.{sig}"  # re-pointed at another share
    stale = _grant(shares[LOCKED_ID], now=int(time.time()) - 13 * 3600)
    for token in (forged, stale, f"{prefix}.{sid}.{exp}.{'0' * len(sig)}"):
        assert await share_access.resolve_share_token(token) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "inactive"},
        {"status": "cancelled"},
        {"status": "expired"},
        {"expires_at": PAST},
        {"expires_at": "not-a-date"},
    ],
)
async def test_closed_share_opens_nothing(shares, overrides) -> None:
    shares[SHARE_ID].update(overrides)
    shares[LOCKED_ID].update(overrides)
    assert await share_access.resolve_share_token(CODE) is None
    assert await share_access.resolve_share_token(_grant(shares[LOCKED_ID])) is None


@pytest.mark.asyncio
async def test_deleted_share_opens_nothing(shares) -> None:
    grant = _grant(shares[LOCKED_ID])
    shares.clear()
    assert await share_access.resolve_share_token(CODE) is None
    assert await share_access.resolve_share_token(grant) is None


@pytest.mark.asyncio
async def test_view_limit_binds_the_code_not_the_grant(shares) -> None:
    """The visitor who spent the last view still loads the file."""
    shares[SHARE_ID].update(max_views=1, view_count=1)
    assert await share_access.resolve_share_token(CODE) is None
    assert await share_access.resolve_share_token(_grant(shares[SHARE_ID])) is not None


def test_grant_carries_no_password_or_code(shares) -> None:
    grant = _grant(shares[LOCKED_ID])
    assert "s3cret" not in grant and LOCKED_CODE not in grant


# --------------------------------------------------------------------------- #
# /media/{id} and /media/{id}/cover (main.py::_check_permissions)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_media_file_route_share_reads_its_media(shares) -> None:
    await guard.require_media_file_access(str(MEDIA), None, CODE, None, ())
    grant = _grant(shares[LOCKED_ID])
    await guard.require_media_file_access(str(MEDIA), None, grant, None, ())


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [CODE, LOCKED_CODE, "nope"])
async def test_media_file_route_share_cannot_read_other_media(shares, token) -> None:
    with pytest.raises(HTTPException) as exc:
        await guard.require_media_file_access(str(OTHER_MEDIA), None, token, None, ())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_media_file_route_password_share_needs_the_grant(shares) -> None:
    with pytest.raises(HTTPException):
        await guard.require_media_file_access(str(MEDIA), None, LOCKED_CODE, None, ())


def test_legacy_path_route_ignores_share_token() -> None:
    """Read from source: the ``/media/*`` block only registers with a writable
    DOWNLOAD_PATH (see test_media_access_multi_holder)."""
    source = (Path(__file__).parents[2] / "app" / "main.py").read_text()
    body = source[source.index("async def serve_media_by_path(") :]
    body = body[: body.index("return _serve_file(file_path)")]
    assert "request, token, None, review_token" in body


# --------------------------------------------------------------------------- #
# Album routes over HTTP, anonymous visitor
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def anon_client(monkeypatch, tmp_path) -> AsyncClient:
    async def _none():
        return None

    app.dependency_overrides[get_optional_auth] = _none
    app.dependency_overrides.pop(get_auth, None)
    slides = tmp_path / "album" / "slides"
    slides.mkdir(parents=True)
    (slides / "001.jpg").write_bytes(b"ONE")
    (tmp_path / "album" / "audio.mp3").write_bytes(b"AUDIO")
    monkeypatch.setattr(
        "app.core.utils.Utils.get_download_base_path", lambda: str(tmp_path)
    )
    row = _orm_obj_to_dict(sample_orm(ParsedMedia), _PM_NAME_TO_ATTR)
    row.update(
        id=MEDIA,
        download_path="album",
        music_download_path=None,
        extract_audio_path=None,
    )
    monkeypatch.setattr(MediaRepository, "get_by_id", AsyncMock(return_value=row))
    monkeypatch.setattr(
        "app.api.media_slides_router._resolve_album_location",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.library.media_serving.serve_stored_file",
        AsyncMock(return_value=Response(content=b"MP3", media_type="audio/mpeg")),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_optional_auth, None)


ALBUM_URLS = [
    "/api/v1/media/{m}/slides",
    "/api/v1/media/{m}/slides/001.jpg",
    "/api/v1/media/{m}/audio",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ALBUM_URLS)
async def test_album_opens_with_the_share(shares, anon_client, url) -> None:
    grant = _grant(shares[LOCKED_ID])
    for token in (CODE, grant):
        resp = await anon_client.get(url.format(m=MEDIA), params={"share_token": token})
        assert resp.status_code == 200, (token, resp.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ALBUM_URLS)
async def test_album_of_other_media_stays_shut(shares, anon_client, url) -> None:
    resp = await anon_client.get(
        url.format(m=OTHER_MEDIA), params={"share_token": CODE}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ALBUM_URLS)
async def test_album_password_share_refuses_the_code(shares, anon_client, url) -> None:
    resp = await anon_client.get(
        url.format(m=MEDIA), params={"share_token": LOCKED_CODE}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ALBUM_URLS)
async def test_album_expired_share_is_shut(shares, anon_client, url) -> None:
    shares[SHARE_ID]["expires_at"] = PAST
    resp = await anon_client.get(url.format(m=MEDIA), params={"share_token": CODE})
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ALBUM_URLS)
async def test_album_without_any_credential_is_401(shares, anon_client, url) -> None:
    resp = await anon_client.get(url.format(m=MEDIA))
    assert resp.status_code == 401
