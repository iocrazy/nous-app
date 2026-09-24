"""``/media`` CRUD routes: wire parity after they gained response models (P5).

Each route runs over real HTTP. The list / search / statistics / logs routes
go through the real repository code with only the database session scripted:
the session hands back ORM objects with every column set (``sample_orm``), so
the dict the handler builds is the one production builds, and the body must
equal what FastAPI sent for that dict with no model
(``tests/api/wire_parity.py`` explains why).

Also pinned here:

- ``GET /media/statistics`` and ``GET /media/logs`` are reachable. They were
  declared after ``GET /media/{platform_id}``, which matched them first and
  answered 404 "Video not found" (the dashboard's recent activity was always
  empty).
- ``DELETE /media/{platform_id}`` only deletes a row the caller solely owns.
  The row is global; before, any signed-in caller could delete any of them by
  public platform id, nulling ``media_id`` on every other user's resource.
- ``GET /media/{platform_id}`` applies the same read rule as the download
  routes.
- search ``%`` / ``_`` match themselves.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any, List
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import ParsedMedia, UserLogs
from app.repositories._orm_helpers import _orm_obj_to_dict
from app.schemas.media_responses import MediaCard, MediaUserLog
from app.schemas.parsed_media_row import ParsedMediaRow
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    assert_wire_unchanged,
    column_names,
    sample_orm,
)

r = sys.modules["app.api.media_router"]
media_repo_mod = sys.modules["app.repositories.media_repository"]
logs_repo_mod = sys.modules["app.repositories.user_logs_repository"]
guard_mod = sys.modules["app.api.media_access_guard"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OTHER = "00000000-0000-0000-0000-000000000099"
RESOURCE_ID = SAMPLE_BIGINT + 500


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _Result:
    def __init__(
        self,
        objs: List[Any] | None = None,
        rows: List[Any] | None = None,
        mapping: dict | None = None,
    ):
        self._objs = objs or []
        self._rows = rows or []
        self._mapping = mapping

    def scalars(self):
        objs = self._objs

        class _S:
            def all(self):
                return list(objs)

            def first(self):
                return objs[0] if objs else None

        return _S()

    def all(self):
        return list(self._rows)

    def mappings(self):
        mapping = self._mapping

        class _M:
            def first(self):
                return mapping

        return _M()


class _Db:
    """Scripted session: each ``execute`` pops the next result."""

    results: List[_Result] = []
    scalars: List[Any] = []
    statements: List[Any] = []


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    _Db.results = []
    _Db.scalars = []
    _Db.statements = []

    class _Session:
        async def execute(self, stmt):
            _Db.statements.append(stmt)
            return _Db.results.pop(0)

        async def scalar(self, stmt):
            _Db.statements.append(stmt)
            return _Db.scalars.pop(0)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr(media_repo_mod, "read_scope", _scope)
    monkeypatch.setattr(logs_repo_mod, "read_scope", _scope)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _media() -> ParsedMedia:
    return sample_orm(ParsedMedia)


def _card_rows() -> list[tuple]:
    return [(RESOURCE_ID, True, _media()), (RESOURCE_ID + 1, False, _media())]


# ── field sets follow the tables ─────────────────────────────────────────


def test_card_declares_the_card_projection_plus_overlays():
    assert set(MediaCard.model_fields) == set(media_repo_mod._CARD_COLS) | {
        "resource_id",
        "has_prompt",
    }


def test_row_declares_every_parsed_media_column():
    assert set(ParsedMediaRow.model_fields) == column_names(ParsedMedia)


def test_user_log_declares_every_user_logs_column():
    assert set(MediaUserLog.model_fields) == column_names(UserLogs)


# ── cleanup ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_stale_downloads_wire(client, monkeypatch):
    monkeypatch.setattr(
        media_repo_mod.MediaRepository,
        "mark_stale_downloads_failed",
        AsyncMock(return_value=3),
    )
    resp = await client.post("/api/v1/media/cleanup-stale-downloads")
    assert_wire_unchanged(resp, {"success": True, "cleaned": 3})


# ── list / search ────────────────────────────────────────────────────────


def _expected_cards(rows: list[tuple]) -> list[dict]:
    return [media_repo_mod._card_row(*row) for row in rows]


@pytest.mark.asyncio
async def test_list_wire(client):
    rows = _card_rows()
    _Db.results = [_Result(rows=rows)]
    resp = await client.get("/api/v1/media")
    cards = _expected_cards(rows)
    assert_wire_unchanged(resp, {"success": True, "count": len(cards), "videos": cards})


@pytest.mark.asyncio
async def test_search_wire(client):
    rows = _card_rows()
    _Db.results = [_Result(rows=rows)]
    resp = await client.post("/api/v1/media/search", json={"keyword": "dance"})
    cards = _expected_cards(rows)
    assert_wire_unchanged(resp, {"success": True, "count": len(cards), "videos": cards})


@pytest.mark.asyncio
async def test_search_escapes_like_wildcards(client):
    _Db.results = [_Result(rows=[])]
    resp = await client.post(
        "/api/v1/media/search", json={"keyword": "100%", "author": "a_b"}
    )
    assert resp.status_code == 200, resp.text
    params = _Db.statements[0].compile().params
    values = set(params.values())
    assert "%100\\%%" in values
    assert "%a\\_b%" in values


# ── statistics / logs (reachable again) ──────────────────────────────────


@pytest.mark.asyncio
async def test_statistics_wire(client):
    _Db.results = [
        _Result(
            mapping={
                "total": 10,
                "pending": 2,
                "completed": 5,
                "failed": 1,
                "total_storage_bytes": SAMPLE_BIGINT,
                "unique_authors": 4,
            }
        )
    ]
    resp = await client.get("/api/v1/media/statistics")
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "statistics": {
                "total": 10,
                "pending": 2,
                "completed": 5,
                "failed": 1,
                "skipped": 2,
                "total_storage_bytes": SAMPLE_BIGINT,
                "unique_authors": 4,
            },
        },
    )


@pytest.mark.asyncio
async def test_logs_wire(client):
    logs = [sample_orm(UserLogs), sample_orm(UserLogs, details=None, status=None)]
    _Db.scalars = [120]
    _Db.results = [_Result(objs=logs)]
    resp = await client.get("/api/v1/media/logs?page=2&page_size=50")
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "logs": [logs_repo_mod._log_to_dict(obj) for obj in logs],
            "total": 120,
            "page": 2,
            "page_size": 50,
            "total_pages": 3,
        },
    )


@pytest.mark.asyncio
async def test_logs_search_escapes_like_wildcards(client):
    _Db.scalars = [0]
    _Db.results = [_Result(objs=[])]
    resp = await client.get("/api/v1/media/logs?search=50%25_off")
    assert resp.status_code == 200, resp.text
    assert "%50\\%\\_off%" in set(_Db.statements[0].compile().params.values())


# ── detail ───────────────────────────────────────────────────────────────


def _row_dict() -> dict:
    return _orm_obj_to_dict(_media(), media_repo_mod._PM_NAME_TO_ATTR)


def _stub_detail(monkeypatch, row: dict, resource: dict | None, allowed: bool):
    monkeypatch.setattr(
        media_repo_mod.MediaRepository,
        "get_by_platform_id",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(
        guard_mod, "caller_can_read_media", AsyncMock(return_value=allowed)
    )
    from app.repositories import resources_repository as res_mod

    monkeypatch.setattr(
        res_mod.ResourcesRepository,
        "get_resource_by_media_id_and_creator",
        AsyncMock(return_value=resource),
    )


@pytest.mark.asyncio
async def test_detail_wire_with_own_resource(client, monkeypatch):
    row = _row_dict()
    _stub_detail(monkeypatch, row, {"id": RESOURCE_ID}, allowed=True)
    resp = await client.get("/api/v1/media/7300000000000000001")
    assert_wire_unchanged(
        resp, {"success": True, "video": {**row, "resource_id": RESOURCE_ID}}
    )


@pytest.mark.asyncio
async def test_detail_wire_without_resource_omits_resource_id(client, monkeypatch):
    row = _row_dict()
    _stub_detail(monkeypatch, row, None, allowed=True)
    resp = await client.get("/api/v1/media/7300000000000000001")
    assert_wire_unchanged(resp, {"success": True, "video": row})
    assert "resource_id" not in resp.json()["video"]


@pytest.mark.asyncio
async def test_detail_of_media_the_caller_cannot_read_is_404(client, monkeypatch):
    _stub_detail(monkeypatch, _row_dict(), None, allowed=False)
    resp = await client.get("/api/v1/media/7300000000000000001")
    assert resp.status_code == 404


# ── delete ───────────────────────────────────────────────────────────────


def _stub_delete(monkeypatch, creators: set[str]) -> AsyncMock:
    row = {"id": SAMPLE_BIGINT, "platform_id": "p-1", "title": "Clip"}
    monkeypatch.setattr(
        media_repo_mod.MediaRepository,
        "get_by_platform_id",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(
        media_repo_mod.MediaRepository,
        "get_media_creator_ids",
        AsyncMock(return_value=creators),
    )
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(media_repo_mod.MediaRepository, "delete", delete)
    monkeypatch.setattr(r, "log_user_action", AsyncMock())
    return delete


@pytest.mark.asyncio
async def test_delete_wire_for_sole_owner(client, monkeypatch):
    delete = _stub_delete(monkeypatch, {USER})
    resp = await client.delete("/api/v1/media/p-1")
    assert_wire_unchanged(
        resp, {"success": True, "message": "Video deleted", "files_deleted": []}
    )
    delete.assert_awaited_once_with("p-1")


@pytest.mark.asyncio
async def test_delete_of_foreign_media_is_404_and_deletes_nothing(client, monkeypatch):
    delete = _stub_delete(monkeypatch, {OTHER})
    resp = await client.delete("/api/v1/media/p-1?delete_files=true")
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_of_unowned_media_is_404(client, monkeypatch):
    delete = _stub_delete(monkeypatch, set())
    resp = await client.delete("/api/v1/media/p-1")
    assert resp.status_code == 404
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_of_media_others_also_own_is_409(client, monkeypatch):
    delete = _stub_delete(monkeypatch, {USER, OTHER})
    resp = await client.delete("/api/v1/media/p-1")
    assert resp.status_code == 409
    assert resp.json()["details"]["code"] == "media_shared"
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_creator_ids_reads_every_creator(monkeypatch):
    _Db.results = [_Result(rows=[(USER,), (OTHER,), (None,)])]
    ids = await media_repo_mod.MediaRepository().get_media_creator_ids(
        str(SAMPLE_BIGINT)
    )
    assert ids == {USER, OTHER}
    assert await media_repo_mod.MediaRepository().get_media_creator_ids("x") == set()
