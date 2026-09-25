"""Episode routes: wire parity after they gained response models (P3).

Rows come from the repository's own converters over a fully populated ORM
object (``tests/api/episode_wire_rows.py``), so every column reaches the
wire in its real type: bigint ids as JSON numbers here, unlike the canvas
routes' strings.
"""

from __future__ import annotations

import importlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.row_guard import NOT_FOUND_OR_OUT_OF_SCOPE
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_episode_write_access,
    verify_project_read_access,
    verify_project_write_access,
)
from app.main import app
from app.models import Episodes
from app.repositories.episode_repository import EpisodeRepository
from app.schemas.episode_responses import (
    EpisodeListRow,
    EpisodeProgressRow,
    EpisodeRow,
    EpisodeWorkflowRollup,
)
from tests.api.episode_wire_rows import repo_episode_row, repo_progress_row
from tests.api.wire_parity import assert_wire_unchanged, column_names

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
PROJECT_ID = "7300000000000000001"
_GUARDS = (
    verify_project_read_access,
    verify_project_write_access,
    verify_episode_write_access,
)


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _gates(monkeypatch):
    for dep in _GUARDS:
        app.dependency_overrides[dep] = lambda: None
    app.dependency_overrides[get_auth] = _fake_auth

    async def _arrangement(project_id, user_id):
        return None

    async def _no_workflow(project_id, episode_id, *, user_id):
        return None

    monkeypatch.setattr(
        importlib.import_module("app.services.workflow.node_authz"),
        "require_arrangement_role",
        _arrangement,
    )
    monkeypatch.setattr(
        importlib.import_module("app.services.workflow.instantiation"),
        "maybe_instantiate_episode_workflow",
        _no_workflow,
    )
    yield
    for dep in (*_GUARDS, get_auth):
        app.dependency_overrides.pop(dep, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _nulls(row: dict) -> dict:
    return {**row, "current_node_id": None, "owner_id": None}


def test_row_model_declares_every_orm_column() -> None:
    assert set(EpisodeRow.model_fields) == column_names(Episodes)
    assert set(EpisodeListRow.model_fields) == column_names(Episodes) | {"script_count"}


def test_progress_model_declares_every_key_the_repository_builds() -> None:
    row = repo_progress_row()
    assert set(EpisodeProgressRow.model_fields) == set(row)
    assert set(EpisodeWorkflowRollup.model_fields) == set(row["workflow"])


@pytest.mark.asyncio
async def test_list_wire_unchanged(client, monkeypatch) -> None:
    rows = [
        {**repo_episode_row(), "script_count": 2},
        {**_nulls(repo_episode_row()), "script_count": 0},
    ]

    async def _list(self, project_id):
        return rows

    monkeypatch.setattr(EpisodeRepository, "list_by_project", _list)
    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/episodes")
    assert_wire_unchanged(resp, {"success": True, "data": rows})
    assert isinstance(resp.json()["data"][0]["id"], int)


@pytest.mark.asyncio
async def test_progress_wire_unchanged(client, monkeypatch) -> None:
    rows = [
        repo_progress_row(),
        repo_progress_row(
            owner_id=None,
            status="planned",
            workflow={
                "nodes_total": 0,
                "nodes_done": 0,
                "current_node_id": None,
                "needs_input_count": 0,
            },
        ),
    ]

    async def _progress(self, project_id):
        return rows

    monkeypatch.setattr(EpisodeRepository, "progress_by_project", _progress)
    resp = await client.get(f"/api/v1/projects/{PROJECT_ID}/episodes/progress")
    assert_wire_unchanged(resp, {"success": True, "data": rows})


@pytest.mark.asyncio
async def test_create_wire_unchanged(client, monkeypatch) -> None:
    row = repo_episode_row()

    async def _create(self, data):
        return row

    monkeypatch.setattr(EpisodeRepository, "create", _create)
    resp = await client.post(f"/api/v1/projects/{PROJECT_ID}/episodes", json={})
    assert_wire_unchanged(resp, {"success": True, "data": row})


@pytest.mark.asyncio
async def test_create_that_returned_no_row_is_a_typed_404(client, monkeypatch) -> None:
    """``create`` answers ``{}`` when RETURNING came back empty. That used to
    reach ``episode["id"]`` in the workflow hook and 500."""

    async def _create(self, data):
        return {}

    monkeypatch.setattr(EpisodeRepository, "create", _create)
    resp = await client.post(f"/api/v1/projects/{PROJECT_ID}/episodes", json={})
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == NOT_FOUND_OR_OUT_OF_SCOPE


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_patch_wire_unchanged(client, monkeypatch, nulls) -> None:
    row = _nulls(repo_episode_row()) if nulls else repo_episode_row()

    async def _update(self, episode_id, data):
        return row

    monkeypatch.setattr(EpisodeRepository, "update", _update)
    resp = await client.patch("/api/v1/episodes/77", json={"title": "Pilot"})
    assert_wire_unchanged(resp, {"success": True, "data": row})


@pytest.mark.asyncio
async def test_delete_wire_unchanged(client, monkeypatch) -> None:
    async def _get(self, episode_id):
        return repo_episode_row()

    async def _delete(self, episode_id):
        return True

    monkeypatch.setattr(EpisodeRepository, "get_by_id", _get)
    monkeypatch.setattr(EpisodeRepository, "delete", _delete)
    resp = await client.delete("/api/v1/episodes/77")
    assert_wire_unchanged(resp, {"success": True})
