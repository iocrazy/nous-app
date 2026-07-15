"""project_characters base (mig 357, character canvas epic PR-CC1).

Repo serialization + upsert idempotency args, and hermetic router tests
(auth + project guards overridden, repo monkeypatched — no DB).
"""

from __future__ import annotations

import sys
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
from app.repositories.project_character_repository import (
    ProjectCharacterRepository,
    _serialize,
)

projects_router = sys.modules["app.api.projects_router"]

FAKE_USER_ID = str(uuid4())


# ============================================================
# _serialize — snowflake ids ride as strings
# ============================================================


class TestSerialize:
    def test_bigint_ids_become_strings(self) -> None:
        out = _serialize({"id": 9007199254740995, "project_id": 777, "name": "Cole"})
        assert out["id"] == "9007199254740995"
        assert out["project_id"] == "777"
        assert out["name"] == "Cole"

    def test_missing_ids_left_alone(self) -> None:
        assert _serialize({"name": "x"}) == {"name": "x"}

    def test_datetime_becomes_iso_string(self) -> None:
        import datetime

        dt = datetime.datetime(2026, 7, 13, tzinfo=datetime.timezone.utc)
        out = _serialize({"created_at": dt, "tags": {"a": [1]}})
        assert out["created_at"] == "2026-07-13T00:00:00+00:00"
        assert out["tags"] == {"a": [1]}  # jsonb dict passes through


# ============================================================
# CRUD — ORM boundary (real statement construction, stub session)
# ============================================================


class _OrmObj:
    """Minimal stand-in for a refreshed ProjectCharacters row."""

    def __init__(self, **vals):
        self._vals = vals
        from app.models import ProjectCharacters

        self.__table__ = ProjectCharacters.__table__

    def __getattr__(self, name):
        try:
            return self._vals[name]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(name) from exc


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalars(self):
        return self

    def all(self):
        return [self._obj] if self._obj is not None else []

    def first(self):
        return self._obj


class _CrudSession:
    def __init__(self, found=None):
        self.statements: list = []
        self.added: list = []
        self.deleted: list = []
        self._found = found

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return _Result(self._found)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        self.deleted.append(obj)


class TestCrudOrmBoundary:
    @pytest.mark.asyncio
    async def test_list_filters_project_and_orders(self, monkeypatch):
        import app.repositories.project_character_repository as repo_mod

        session = _CrudSession()
        monkeypatch.setattr(repo_mod, "read_scope", _cm(session))
        await ProjectCharacterRepository().list_by_project("777")
        sql = str(session.statements[0])
        assert "FROM public.project_characters" in sql
        assert "ORDER BY" in sql and "sort_order" in sql
        assert 777 in session.statements[0].compile().params.values()

    @pytest.mark.asyncio
    async def test_create_builds_row_with_int_project_id(self, monkeypatch):
        import app.repositories.project_character_repository as repo_mod

        session = _CrudSession()
        monkeypatch.setattr(repo_mod, "write_scope", _cm(session))
        await ProjectCharacterRepository().create("777", {"name": "Cole"})
        assert len(session.added) == 1
        obj = session.added[0]
        assert obj.project_id == 777 and obj.name == "Cole"

    @pytest.mark.asyncio
    async def test_delete_true_when_found(self, monkeypatch):
        import app.repositories.project_character_repository as repo_mod

        found = _OrmObj(id=1, project_id=777)
        session = _CrudSession(found=found)
        monkeypatch.setattr(repo_mod, "write_scope", _cm(session))
        ok = await ProjectCharacterRepository().delete("777", "1")
        assert ok is True and session.deleted == [found]

    @pytest.mark.asyncio
    async def test_delete_false_when_missing(self, monkeypatch):
        import app.repositories.project_character_repository as repo_mod

        session = _CrudSession(found=None)
        monkeypatch.setattr(repo_mod, "write_scope", _cm(session))
        assert await ProjectCharacterRepository().delete("777", "9") is False

    @pytest.mark.asyncio
    async def test_no_supabase_client_surface(self):
        import inspect

        import app.repositories.project_character_repository as repo_mod
        import app.repositories.project_lib_entity_repository as lib_mod

        for mod in (repo_mod, lib_mod):
            src = inspect.getsource(mod)
            assert "get_async_supabase_admin" not in src
            assert "client.table" not in src


# ============================================================
# upsert_by_name — extract idempotency contract (ORM boundary)
# ============================================================


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


class TestUpsertByName:
    @pytest.mark.asyncio
    async def test_upserts_on_project_name_and_never_clobbers(self, monkeypatch):
        import app.repositories.project_character_repository as repo_mod

        session = _CaptureSession()
        monkeypatch.setattr(repo_mod, "write_scope", _cm(session))
        # upsert_by_name re-reads via list_by_project (read_scope) at the end.
        monkeypatch.setattr(repo_mod, "read_scope", _cm(_CaptureSession()))

        await ProjectCharacterRepository().upsert_by_name(
            "777", ["Cole", "  ", "", "Mara"]
        )

        stmt = session.statements[0]
        sql = str(stmt).lower()
        assert "insert into public.project_characters" in sql
        # Curated rows must never be clobbered: ON CONFLICT (project_id, name)
        # DO NOTHING.
        assert "on conflict" in sql and "do nothing" in sql
        assert "project_id, name" in sql
        params = stmt.compile().params
        names = [v for k, v in params.items() if k.startswith("name")]
        assert names == ["Cole", "Mara"]  # blanks dropped
        assert all(v == "script" for k, v in params.items() if k.startswith("source"))

    @pytest.mark.asyncio
    async def test_all_blank_names_short_circuit(self, monkeypatch):
        import app.repositories.project_character_repository as repo_mod

        session = _CaptureSession()
        monkeypatch.setattr(repo_mod, "write_scope", _cm(session))
        out = await ProjectCharacterRepository().upsert_by_name("777", ["", "   "])
        assert out == []
        assert session.statements == []  # no write issued


# ============================================================
# Router — hermetic
# ============================================================


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


async def _pass_guard() -> None:
    # Signature matters: FastAPI resolves the override's own signature, so a
    # *args catch-all would surface as required query params (422).
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
    import app.repositories.project_character_repository as repo_mod

    monkeypatch.setattr(repo_mod, "get_project_character_repository", lambda: repo)
    return repo


ROW = {
    "id": "1",
    "project_id": "777",
    "name": "Cole",
    "role_tag": "lead",
    "description": "",
    "tags": {},
    "portrait_url": None,
    "source": "manual",
    "sort_order": 0,
    "created_at": "2026-07-13T00:00:00+00:00",
    "updated_at": "2026-07-13T00:00:00+00:00",
}


class TestCharactersRoutes:
    @pytest.mark.asyncio
    async def test_list(self, client, monkeypatch):
        _patch_repo(monkeypatch, list_by_project=AsyncMock(return_value=[ROW]))
        resp = await client.get("/api/v1/projects/777/characters")
        assert resp.status_code == 200
        assert resp.json()["data"][0]["name"] == "Cole"

    @pytest.mark.asyncio
    async def test_create_passes_payload_fields(self, client, monkeypatch):
        create = AsyncMock(return_value=ROW)
        _patch_repo(monkeypatch, create=create)
        resp = await client.post(
            "/api/v1/projects/777/characters",
            json={"name": "Cole", "role_tag": "lead"},
        )
        assert resp.status_code == 200
        args = create.await_args.args
        assert args[0] == "777"
        assert args[1]["name"] == "Cole"
        assert args[1]["role_tag"] == "lead"

    @pytest.mark.asyncio
    async def test_create_rejects_blank_name(self, client, monkeypatch):
        _patch_repo(monkeypatch, create=AsyncMock())
        resp = await client.post("/api/v1/projects/777/characters", json={"name": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_404_when_not_in_project(self, client, monkeypatch):
        _patch_repo(monkeypatch, update=AsyncMock(return_value=None))
        resp = await client.patch(
            "/api/v1/projects/777/characters/9", json={"description": "x"}
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete(self, client, monkeypatch):
        _patch_repo(monkeypatch, delete=AsyncMock(return_value=True))
        resp = await client.delete("/api/v1/projects/777/characters/1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_extract_materializes_derived_names(self, client, monkeypatch):
        upsert = AsyncMock(return_value=[ROW])
        _patch_repo(monkeypatch, upsert_by_name=upsert)
        entities = AsyncMock(
            return_value={"characters": [{"name": "Cole"}, {"name": "Mara"}]}
        )
        monkeypatch.setattr(
            projects_router.ProjectsService,
            "get_project_entities",
            entities,
        )
        resp = await client.post("/api/v1/projects/777/characters/extract")
        assert resp.status_code == 200
        upsert.assert_awaited_once_with("777", ["Cole", "Mara"])
        assert resp.json()["data"][0]["name"] == "Cole"
