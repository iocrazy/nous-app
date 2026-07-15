"""Generalized project library — locations + props (mig 358, SP1).

Hermetic router tests + the upsert idempotency contract. Same discipline as
test_project_characters (guards overridden no-arg, repo monkeypatched).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_project_read_access,
    verify_project_write_access,
)
from app.main import app
from app.repositories.project_lib_entity_repository import (
    ProjectLibEntityRepository,
)

FAKE_USER_ID = str(uuid4())


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


async def _pass_guard() -> None:  # no-arg: FastAPI resolves override signatures
    return None


@pytest.fixture(autouse=True)
def _overrides():
    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[verify_project_read_access] = _pass_guard
    app.dependency_overrides[verify_project_write_access] = _pass_guard
    yield
    for dep in (get_auth, verify_project_read_access, verify_project_write_access):
        app.dependency_overrides.pop(dep, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _patch_repo(monkeypatch, **methods):
    repo = SimpleNamespace(**methods)
    import app.repositories.project_lib_entity_repository as repo_mod

    monkeypatch.setattr(repo_mod, "get_project_lib_entity_repository", lambda: repo)
    return repo


ROW = {
    "id": "1",
    "project_id": "777",
    "entity_type": "location",
    "name": "Radio Booth",
    "badge_tag": "interior",
    "description": "",
    "tags": {},
    "cover_url": None,
    "source": "manual",
    "sort_order": 0,
    "created_at": "2026-07-13T00:00:00+00:00",
    "updated_at": "2026-07-13T00:00:00+00:00",
}


class TestLibRoutes:
    @pytest.mark.asyncio
    async def test_list_scopes_by_type(self, client, monkeypatch):
        lister = AsyncMock(return_value=[ROW])
        _patch_repo(monkeypatch, list_by_project=lister)
        resp = await client.get("/api/v1/projects/777/lib/location")
        assert resp.status_code == 200
        lister.assert_awaited_once_with("777", "location")

    @pytest.mark.asyncio
    async def test_unknown_entity_type_is_422(self, client, monkeypatch):
        _patch_repo(monkeypatch, list_by_project=AsyncMock())
        resp = await client.get("/api/v1/projects/777/lib/vehicle")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_prop(self, client, monkeypatch):
        create = AsyncMock(
            return_value={**ROW, "entity_type": "prop", "name": "Revolver"}
        )
        _patch_repo(monkeypatch, create=create)
        resp = await client.post(
            "/api/v1/projects/777/lib/prop",
            json={"name": "Revolver", "badge_tag": "hero"},
        )
        assert resp.status_code == 200
        args = create.await_args.args
        assert args[:2] == ("777", "prop")
        assert args[2]["badge_tag"] == "hero"

    @pytest.mark.asyncio
    async def test_extract_locations_from_scene_headers(self, client, monkeypatch):
        upsert = AsyncMock(return_value=[ROW])
        _patch_repo(monkeypatch, upsert_by_name=upsert)
        import sys

        projects_router = sys.modules["app.api.projects_router"]
        monkeypatch.setattr(
            projects_router.ProjectsService,
            "get_project_entities",
            AsyncMock(return_value={"locations": [{"name": "Radio Booth"}]}),
        )
        resp = await client.post("/api/v1/projects/777/lib/location/extract")
        assert resp.status_code == 200
        upsert.assert_awaited_once_with("777", "location", ["Radio Booth"])

    @pytest.mark.asyncio
    async def test_extract_props_is_rejected(self, client, monkeypatch):
        _patch_repo(monkeypatch, upsert_by_name=AsyncMock())
        resp = await client.post("/api/v1/projects/777/lib/prop/extract")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_404_when_missing(self, client, monkeypatch):
        _patch_repo(monkeypatch, update=AsyncMock(return_value=None))
        resp = await client.patch(
            "/api/v1/projects/777/lib/location/9", json={"description": "x"}
        )
        assert resp.status_code == 404


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _CaptureSession:
    def __init__(self):
        self.statements: list = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _EmptyResult()


def _cm(session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


class TestUpsertContract:
    @pytest.mark.asyncio
    async def test_upsert_keys_and_never_clobbers(self, monkeypatch):
        import app.repositories.project_lib_entity_repository as repo_mod

        session = _CaptureSession()
        monkeypatch.setattr(repo_mod, "write_scope", _cm(session))
        monkeypatch.setattr(repo_mod, "read_scope", _cm(_CaptureSession()))

        await ProjectLibEntityRepository().upsert_by_name(
            "777", "location", ["Booth", " "]
        )

        stmt = session.statements[0]
        sql = str(stmt).lower()
        assert "insert into public.project_lib_entities" in sql
        assert "on conflict" in sql and "do nothing" in sql
        assert "project_id, entity_type, name" in sql
        params = stmt.compile().params
        names = [v for k, v in params.items() if k.startswith("name")]
        assert names == ["Booth"]  # blank dropped
        assert all(
            v == "location" for k, v in params.items() if k.startswith("entity_type")
        )
