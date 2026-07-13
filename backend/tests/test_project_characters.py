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


# ============================================================
# upsert_by_name — extract idempotency contract
# ============================================================


class _UpsertClient:
    def __init__(self):
        self.upsert_args: list = []

    def table(self, *_a):
        return self

    def upsert(self, rows, **kwargs):
        self.upsert_args.append((rows, kwargs))
        return self

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def order(self, *_a):
        return self

    async def execute(self):
        return SimpleNamespace(data=[])


class TestUpsertByName:
    @pytest.mark.asyncio
    async def test_upserts_on_project_name_and_never_clobbers(self, monkeypatch):
        repo = ProjectCharacterRepository()
        client = _UpsertClient()
        monkeypatch.setattr(repo, "_client", AsyncMock(return_value=client))

        await repo.upsert_by_name("777", ["Cole", "  ", "", "Mara"])

        rows, kwargs = client.upsert_args[0]
        assert [r["name"] for r in rows] == ["Cole", "Mara"]  # blanks dropped
        assert all(r["source"] == "script" for r in rows)
        assert kwargs["on_conflict"] == "project_id,name"
        # Curated rows must never be clobbered by a re-run.
        assert kwargs["ignore_duplicates"] is True

    @pytest.mark.asyncio
    async def test_all_blank_names_short_circuit(self, monkeypatch):
        repo = ProjectCharacterRepository()
        client = _UpsertClient()
        monkeypatch.setattr(repo, "_client", AsyncMock(return_value=client))
        out = await repo.upsert_by_name("777", ["", "   "])
        assert out == []
        assert client.upsert_args == []


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
