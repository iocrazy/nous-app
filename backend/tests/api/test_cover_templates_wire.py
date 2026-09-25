"""Cover-template routes: wire parity after they gained response models (P9).

``GET /cover-templates`` runs the real ``list_images`` over a scripted
session; the folder lookup (``ensure_folder``) is replaced by a stub that
returns the repository's own dict shape (``{id, name, adopted}``, id already a
string). Bodies must equal what FastAPI sent for the same dict with no model
(``tests/api/wire_parity.py``): in particular ``last_used_at`` is a native
datetime from the usage join and has to keep ``isoformat()``'s ``+00:00``.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from tests.api.wire_parity import SAMPLE_BIGINT, SAMPLE_TS, assert_wire_unchanged

ct = sys.modules["app.api.cover_templates_router"]
repo_mod = sys.modules["app.repositories.cover_templates_repository"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
SCOPE_ID = 7300000000000000009
FOLDER = {"id": str(SAMPLE_BIGINT), "name": "Covers", "adopted": False}


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _Result:
    def __init__(self, rows: List[Any] | None = None, scalar: Any = None):
        self._rows = rows or []
        self._scalar = scalar

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _Db:
    results: List[_Result] = []
    statements: List[Any] = []


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    _Db.results = []
    _Db.statements = []

    class _Session:
        async def execute(self, stmt):
            _Db.statements.append(stmt)
            return _Db.results.pop(0) if _Db.results else _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    async def _scope_id(auth):
        return SCOPE_ID

    async def _ensure(self, scope_id, user_id):
        return dict(FOLDER)

    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    monkeypatch.setattr(repo_mod, "write_scope", _scope)
    monkeypatch.setattr(repo_mod.CoverTemplatesRepository, "ensure_folder", _ensure)
    monkeypatch.setattr(ct, "_scope", _scope_id)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _folder_raw() -> dict:
    return {
        "folder_id": FOLDER["id"],
        "name": FOLDER["name"],
        "adopted": FOLDER["adopted"],
    }


@pytest.mark.asyncio
async def test_folder_wire(client) -> None:
    resp = await client.get("/api/v1/cover-templates/folder")
    assert_wire_unchanged(resp, {"data": _folder_raw()})


@pytest.mark.asyncio
async def test_list_wire(client) -> None:
    rows = [
        {
            "id": SAMPLE_BIGINT + 1,
            "filename": "krea2.avif",
            "mime_type": "image/avif",
            "updated_at": SAMPLE_TS,
            "created_at": SAMPLE_TS,
            "usage_count": 3,
            "last_used_at": SAMPLE_TS,
        },
        # Never used, NULL filename / mime: historic rows must not 500.
        {
            "id": SAMPLE_BIGINT + 2,
            "filename": None,
            "mime_type": None,
            "updated_at": SAMPLE_TS,
            "created_at": SAMPLE_TS,
            "usage_count": None,
            "last_used_at": None,
        },
    ]
    _Db.results = [_Result(scalar=2), _Result(rows)]
    items, _total = await repo_mod.CoverTemplatesRepository().list_images(
        SCOPE_ID, SAMPLE_BIGINT
    )
    raw = {
        "data": {
            "folder": _folder_raw(),
            "items": [ct._present(i) for i in items],
            "total": 2,
            "limit": 48,
            "offset": 0,
        }
    }
    _Db.results = [_Result(scalar=2), _Result(rows)]
    resp = await client.get("/api/v1/cover-templates")
    assert_wire_unchanged(resp, raw)
    assert resp.json()["data"]["items"][0]["last_used_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_list_empty_wire(client) -> None:
    _Db.results = [_Result(scalar=0), _Result([])]
    resp = await client.get("/api/v1/cover-templates?limit=10&offset=20")
    assert_wire_unchanged(
        resp,
        {
            "data": {
                "folder": _folder_raw(),
                "items": [],
                "total": 0,
                "limit": 10,
                "offset": 20,
            }
        },
    )


@pytest.mark.asyncio
async def test_use_wire(client) -> None:
    resp = await client.post(
        "/api/v1/cover-templates/use", json={"resource_ids": ["1", "2"]}
    )
    assert_wire_unchanged(resp, {"data": {"counted": 2}})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("q", "pattern"),
    [("100%", "%100\\%%"), ("a_b", "%a\\_b%"), ("cover", "%cover%")],
)
async def test_search_escapes_like_metacharacters(client, q, pattern) -> None:
    """A search for ``%`` used to be a match-everything pattern."""
    from sqlalchemy.dialects import postgresql

    _Db.results = [_Result(scalar=0), _Result([])]
    resp = await client.get("/api/v1/cover-templates", params={"q": q})
    assert resp.status_code == 200, resp.text
    count_stmt = _Db.statements[0].compile(dialect=postgresql.dialect())
    assert pattern in count_stmt.params.values()
    assert "ESCAPE" in str(count_stmt)
