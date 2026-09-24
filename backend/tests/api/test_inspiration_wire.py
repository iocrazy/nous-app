"""Inspiration read routes: wire parity after they gained response models (P6).

``GET /inspiration/notes/activity`` and ``/notes/tags`` run over real HTTP
through the real router, service and repository; only the session is
scripted. The rows are the columns of the two SQL functions
(``inspiration_activity`` → ``day date, cnt bigint``;
``inspiration_tag_counts`` → ``tag text, cnt bigint``, mig 478), carried as
the native types asyncpg returns, so the repository's ``_serialize`` runs for
real. The body must equal what FastAPI sent for the repository's list with no
model.

``GET /inspiration/attachments/{id}`` returns bytes (declared with
``binary_response``). Its ownership check is pinned here over HTTP: another
user's attachment is a 404 whichever credential channel names the caller.
"""

from __future__ import annotations

import datetime as dt
import sys
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from fastapi import Response
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_current_user
from app.main import app
from app.models import InspirationAttachments
from app.schemas.inspiration import (
    InspirationNoteActivityDay,
    InspirationNoteTagCount,
)
from tests.api.wire_parity import assert_wire_unchanged, sample_orm

# ``app/api/__init__.py`` rebinds the name to the APIRouter; patch the module.
ir = sys.modules["app.api.inspiration_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OTHER_USER = "00000000-0000-0000-0000-000000000099"

ACTIVITY_ROWS = [
    {"day": dt.date(2026, 9, 1), "cnt": 3},
    {"day": dt.date(2026, 9, 24), "cnt": 12},
]
TAG_ROWS = [{"tag": "idea", "cnt": 7}, {"tag": "hook", "cnt": 1}]


class _Db:
    rows: List[dict] = []
    params: List[dict] = []


class _Result:
    def __init__(self, rows: List[dict]):
        self._rows = rows

    def mappings(self):
        rows = self._rows

        class _M:
            def all(self):
                return list(rows)

        return _M()


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _user() -> dict:
        return {"id": USER}

    app.dependency_overrides[get_current_user] = _user
    _Db.rows = []
    _Db.params = []

    class _Session:
        async def execute(self, stmt, params=None, *a, **kw):
            _Db.params.append(params or {})
            return _Result(_Db.rows)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.repositories.inspiration_repository.read_scope", _scope)
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_models_declare_exactly_the_function_columns() -> None:
    assert set(InspirationNoteActivityDay.model_fields) == {"day", "cnt"}
    assert set(InspirationNoteTagCount.model_fields) == {"tag", "cnt"}


# --------------------------------------------------------------------------- #
# JSON routes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_activity_wire_unchanged(client) -> None:
    from app.repositories.inspiration_repository import _serialize

    _Db.rows = ACTIVITY_ROWS
    resp = await client.get(
        "/api/v1/inspiration/notes/activity",
        params={"date_from": "2026-09-01", "date_to": "2026-09-30"},
    )
    assert_wire_unchanged(resp, [_serialize(r) for r in ACTIVITY_ROWS])
    assert resp.json()[0] == {"day": "2026-09-01", "cnt": 3}
    # Bound to the caller, whatever the query string says.
    assert _Db.params[0]["p_user_id"] == USER


@pytest.mark.asyncio
async def test_activity_ignores_a_user_id_in_the_query(client) -> None:
    _Db.rows = []
    resp = await client.get(
        "/api/v1/inspiration/notes/activity",
        params={
            "date_from": "2026-09-01",
            "date_to": "2026-09-30",
            "user_id": OTHER_USER,
        },
    )
    assert_wire_unchanged(resp, [])
    assert [p["p_user_id"] for p in _Db.params] == [USER]


@pytest.mark.asyncio
async def test_tags_wire_unchanged(client) -> None:
    from app.repositories.inspiration_repository import _serialize

    _Db.rows = TAG_ROWS
    resp = await client.get("/api/v1/inspiration/notes/tags")
    assert_wire_unchanged(resp, [_serialize(r) for r in TAG_ROWS])
    assert [p["p_user_id"] for p in _Db.params] == [USER]


@pytest.mark.asyncio
async def test_tags_empty_is_an_empty_list(client) -> None:
    resp = await client.get("/api/v1/inspiration/notes/tags")
    assert_wire_unchanged(resp, [])


# --------------------------------------------------------------------------- #
# Attachment bytes
# --------------------------------------------------------------------------- #


def _attachment(owner: str) -> dict[str, Any]:
    from app.repositories.inspiration_attachments_repository import (
        _row_dict,
        _serialize,
    )

    obj = sample_orm(
        InspirationAttachments, user_id=owner, bucket="inspiration", mime="image/png"
    )
    return _serialize(_row_dict(obj))


@pytest.fixture
def attachment_io(monkeypatch):
    """Scripts the attachment lookup and the byte stream; records what was
    served."""
    state: dict[str, Any] = {"row": None, "served": []}

    class _Repo:
        async def get_by_id(self, attachment_id):
            return state["row"]

    async def _serve(uri, *, mime, request, extra_headers):
        state["served"].append(uri)
        return Response(content=b"PNGBYTES", media_type=mime, headers=extra_headers)

    async def _cookie(token):
        return {"tok-owner": USER, "tok-other": OTHER_USER}.get(token)

    monkeypatch.setattr(ir, "get_inspiration_attachments_repository", lambda: _Repo())
    monkeypatch.setattr("app.services.library.media_serving.serve_stored_file", _serve)
    monkeypatch.setattr("app.api.media_auth.validate_media_cookie", _cookie)
    return state


@pytest.mark.asyncio
async def test_owner_gets_the_bytes_via_media_token(client, attachment_io) -> None:
    row = _attachment(USER)
    attachment_io["row"] = row
    resp = await client.get(
        f"/api/v1/inspiration/attachments/{row['id']}", params={"token": "tok-owner"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"PNGBYTES"
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "private, no-store"
    assert attachment_io["served"] == [f"sb://inspiration/{row['path']}"]


@pytest.mark.asyncio
async def test_other_users_attachment_is_404_via_media_token(
    client, attachment_io
) -> None:
    row = _attachment(USER)
    attachment_io["row"] = row
    resp = await client.get(
        f"/api/v1/inspiration/attachments/{row['id']}", params={"token": "tok-other"}
    )
    assert resp.status_code == 404, resp.text
    assert attachment_io["served"] == []


@pytest.mark.asyncio
async def test_other_users_attachment_is_404_via_bearer(
    client, attachment_io, monkeypatch
) -> None:
    async def _jwt_user(authorization):
        assert authorization == "Bearer other-jwt"
        return {"id": OTHER_USER}

    monkeypatch.setattr(ir, "get_current_user", _jwt_user)
    row = _attachment(USER)
    attachment_io["row"] = row
    resp = await client.get(
        f"/api/v1/inspiration/attachments/{row['id']}",
        headers={"Authorization": "Bearer other-jwt"},
    )
    assert resp.status_code == 404, resp.text
    assert attachment_io["served"] == []


@pytest.mark.asyncio
async def test_unknown_attachment_is_404(client, attachment_io) -> None:
    resp = await client.get(
        "/api/v1/inspiration/attachments/123", params={"token": "tok-owner"}
    )
    assert resp.status_code == 404, resp.text


def test_attachment_route_advertises_bytes_not_json() -> None:
    op = app.openapi()["paths"]["/api/v1/inspiration/attachments/{attachment_id}"][
        "get"
    ]
    content = op["responses"]["200"]["content"]
    assert "application/json" not in content
    assert content["*/*"]["schema"] == {"type": "string", "format": "binary"}
