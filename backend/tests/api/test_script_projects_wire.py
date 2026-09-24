"""``/scripts/projects`` wire parity after the routers gained response models
(P3), plus the access guards added alongside.

Every JSON route is driven over real HTTP against the real app. The service
below the handler is stubbed to return rows built from the ORM mapper
(``sample_orm`` → the repository's own ``_to_dict`` / ``_row``), so each row
carries every column in its real native type. The body must equal what
FastAPI sent for the bare dict — see ``tests/api/wire_parity.py``.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_script_access, verify_script_read_access
from app.main import app
from app.models import ScriptAssets, ScriptChapters, ScriptProjects, ScriptScenes
from app.repositories.script_scene_repository import _row as scene_row
from app.schemas import script_project_responses as sr
from tests.api.script_wire_rows import script_asset_row as asset_row
from tests.api.script_wire_rows import script_chapter_row as chapter_row
from tests.api.script_wire_rows import script_project_row as project_row
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    assert_wire_unchanged,
    column_names,
    sample_orm,
)

projects_mod = importlib.import_module("app.api.script_projects_router")
assets_mod = importlib.import_module("app.api.script_assets_router")
canvas_mod = importlib.import_module("app.api.script_canvas_router")

USER = "00000000-0000-0000-0000-000000000042"
SID = SAMPLE_BIGINT + 11
BASE = "/api/v1/scripts/projects"


def _nullable_overrides(model: Any) -> dict[str, Any]:
    return {
        prop.key: None
        for prop in inspect(model).column_attrs
        if prop.columns[0].nullable and not prop.columns[0].primary_key
    }


def scene(number: Any = "3", **over: Any) -> dict:
    row = scene_row(sample_orm(ScriptScenes, **over))
    row["scene_no_in_episode"] = number
    return row


class FakeChapterRepo:
    def __init__(self, owned: list[dict]) -> None:
        self.owned = owned

    async def get_by_id(self, chapter_id):
        return {"id": chapter_id, "script_id": SID}

    async def get_by_script(self, script_id):
        return self.owned


class FakeAssetRepo:
    def __init__(self, found: dict | None) -> None:
        self.found = found

    async def get_by_id(self, asset_id):
        return self.found


class FakeService:
    """Stands in for ``ScriptService``; each method returns ``self.result``."""

    result: Any = None
    chapter_repo: Any = FakeChapterRepo([])
    asset_repo: Any = FakeAssetRepo({"id": 1, "script_id": SID})
    calls: list = []

    def __init__(self) -> None:
        pass

    def __getattr__(self, name):
        async def _method(*args, **kwargs):
            FakeService.calls.append(name)
            return FakeService.result

        return _method


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


async def _allow(*args, **kwargs) -> None:
    return None


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[verify_script_access] = lambda: None
    app.dependency_overrides[verify_script_read_access] = lambda: None
    FakeService.result = None
    FakeService.chapter_repo = FakeChapterRepo([])
    FakeService.asset_repo = FakeAssetRepo({"id": 1, "script_id": SID})
    FakeService.calls = []
    for mod in (projects_mod, assets_mod, canvas_mod):
        monkeypatch.setattr(mod, "ScriptService", FakeService)
    for mod in (assets_mod, canvas_mod):
        monkeypatch.setattr(mod, "verify_script_access", _allow)
    monkeypatch.setattr(assets_mod, "verify_script_read_access", _allow)
    monkeypatch.setattr(projects_mod, "verify_project_read_access", _allow)
    monkeypatch.setattr(projects_mod, "verify_project_write_access", _allow)

    async def _team(_user_id):
        return "7"

    monkeypatch.setattr(projects_mod, "require_team_id", _team)
    yield
    for dep in (get_auth, verify_script_access, verify_script_read_access):
        app.dependency_overrides.pop(dep, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------------- #
# Row models are pinned to their tables
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "model, orm",
    [
        (sr.ScriptProjectRow, ScriptProjects),
        (sr.ScriptChapterRow, ScriptChapters),
        (sr.ScriptAssetRow, ScriptAssets),
    ],
)
def test_row_model_declares_exactly_the_columns(model, orm) -> None:
    assert set(model.model_fields) == column_names(orm)


def test_scene_row_declares_the_columns_plus_derived_number() -> None:
    assert set(sr.ScriptSceneRow.model_fields) == column_names(ScriptScenes) | {
        "scene_no_in_episode"
    }


def test_ids_stay_json_numbers() -> None:
    """Strategy-C parity keeps bigint ids native; the contract says so."""
    row = project_row()
    assert isinstance(row["id"], int) and row["id"] > 2**53
    assert sr.ScriptProjectRow.model_fields["id"].annotation is int


# --------------------------------------------------------------------------- #
# script_projects_router
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_wire_unchanged(client) -> None:
    FakeService.result = project_row()
    resp = await client.post(BASE, json={"project_id": 5, "name": "Pilot"})
    assert_wire_unchanged(resp, {"success": True, "data": FakeService.result})


@pytest.mark.asyncio
async def test_list_wire_unchanged(client) -> None:
    page = {
        "items": [project_row(), project_row(**_nullable_overrides(ScriptProjects))],
        "total": 2,
        "page": 1,
        "limit": 20,
    }
    FakeService.result = page
    resp = await client.get(BASE, params={"project_id": 5})
    assert_wire_unchanged(resp, {"success": True, "data": page})
    assert resp.json()["data"]["items"][0]["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_get_full_wire_unchanged(client) -> None:
    full = {
        "project": project_row(),
        "chapters": [chapter_row(), chapter_row(**_nullable_overrides(ScriptChapters))],
    }
    FakeService.result = full
    resp = await client.get(f"{BASE}/{SID}")
    assert_wire_unchanged(resp, {"success": True, "data": full})


@pytest.mark.asyncio
async def test_update_wire_unchanged(client) -> None:
    FakeService.result = project_row()
    resp = await client.put(f"{BASE}/{SID}", json={"name": "Renamed"})
    assert_wire_unchanged(resp, {"success": True, "data": FakeService.result})


@pytest.mark.asyncio
async def test_update_of_vanished_row_is_typed_404(client) -> None:
    FakeService.result = {}
    resp = await client.put(f"{BASE}/{SID}", json={"name": "Renamed"})
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, path, body",
    [
        ("DELETE", f"{BASE}/{SID}", None),
        ("PATCH", f"{BASE}/{SID}/viewport", {"x": 1, "y": 2, "zoom": 1}),
    ],
)
async def test_ack_wire_unchanged(client, method, path, body) -> None:
    resp = await client.request(method, path, json=body)
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("already", [True, False])
async def test_lock_numbering_wire_unchanged(monkeypatch, client, already) -> None:
    result = {
        "already_locked": already,
        "scenes": [scene("3"), scene(None, **_nullable_overrides(ScriptScenes))],
    }

    class _Scenes:
        async def lock_numbering(self, script_id):
            return result

    monkeypatch.setattr(projects_mod, "get_script_scene_repository", _Scenes)
    resp = await client.post(f"{BASE}/{SID}/lock-numbering")
    assert_wire_unchanged(resp, {"success": True, "data": result})


# --------------------------------------------------------------------------- #
# script_assets_router
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_asset_create_wire_unchanged(client) -> None:
    FakeService.result = asset_row()
    resp = await client.post(
        f"{BASE}/{SID}/assets",
        json={"script_id": str(SID), "asset_type": "character", "name": "Ana"},
    )
    assert_wire_unchanged(resp, {"success": True, "data": FakeService.result})


@pytest.mark.asyncio
async def test_asset_list_wire_unchanged(client) -> None:
    FakeService.result = [asset_row(), asset_row(**_nullable_overrides(ScriptAssets))]
    resp = await client.get(f"{BASE}/{SID}/assets")
    assert_wire_unchanged(resp, {"success": True, "data": FakeService.result})


@pytest.mark.asyncio
async def test_asset_update_wire_unchanged(client) -> None:
    FakeService.result = asset_row()
    resp = await client.put(f"{BASE}/assets/9", json={"name": "Bea"})
    assert_wire_unchanged(resp, {"success": True, "data": FakeService.result})


@pytest.mark.asyncio
async def test_asset_update_of_vanished_row_is_typed_404(client) -> None:
    FakeService.result = {}
    resp = await client.put(f"{BASE}/assets/9", json={"name": "Bea"})
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_asset_delete_wire_unchanged(client) -> None:
    resp = await client.delete(f"{BASE}/assets/9")
    assert_wire_unchanged(resp, {"success": True})


# --------------------------------------------------------------------------- #
# script_canvas_router
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chapter_create_wire_unchanged(client) -> None:
    FakeService.result = chapter_row()
    resp = await client.post(f"{BASE}/{SID}/chapters", json={"title": "One"})
    assert_wire_unchanged(resp, {"success": True, "data": FakeService.result})


@pytest.mark.asyncio
async def test_chapter_update_wire_unchanged(client) -> None:
    FakeService.result = chapter_row()
    resp = await client.put(f"{BASE}/chapters/3", json={"title": "Two"})
    assert_wire_unchanged(resp, {"success": True, "data": FakeService.result})


@pytest.mark.asyncio
async def test_chapter_update_of_vanished_row_is_typed_404(client) -> None:
    FakeService.result = {}
    resp = await client.put(f"{BASE}/chapters/3", json={"title": "Two"})
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_chapter_delete_wire_unchanged(client) -> None:
    resp = await client.delete(f"{BASE}/chapters/3")
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
async def test_canvas_sync_wire_unchanged(client) -> None:
    FakeService.result = {"chapters": [chapter_row()]}
    resp = await client.post(f"{BASE}/{SID}/canvas/sync", json={})
    assert_wire_unchanged(resp, {"success": True, "data": FakeService.result})


# --------------------------------------------------------------------------- #
# Guards added in P3 (each was an IDOR or a 403 reported as 500)
# --------------------------------------------------------------------------- #


async def _deny(*args, **kwargs) -> None:
    raise HTTPException(status_code=403, detail="Access denied")


@pytest.mark.asyncio
async def test_list_checks_the_query_project(monkeypatch, client) -> None:
    monkeypatch.setattr(projects_mod, "verify_project_read_access", _deny)
    resp = await client.get(BASE, params={"project_id": 5})
    assert resp.status_code == 403
    assert FakeService.calls == []


@pytest.mark.asyncio
async def test_create_checks_the_body_project(monkeypatch, client) -> None:
    monkeypatch.setattr(projects_mod, "verify_project_write_access", _deny)
    resp = await client.post(BASE, json={"project_id": 5, "name": "Pilot"})
    assert resp.status_code == 403
    assert FakeService.calls == []


@pytest.mark.asyncio
async def test_create_rejects_an_episode_of_another_project(
    monkeypatch, client
) -> None:
    class _Episodes:
        async def get_by_id(self, episode_id):
            return {"id": 900, "project_id": 6}

    monkeypatch.setattr(projects_mod, "get_episode_repository", _Episodes)
    resp = await client.post(
        BASE, json={"project_id": 5, "name": "Pilot", "episode_id": "900"}
    )
    assert resp.status_code == 404
    assert FakeService.calls == []


@pytest.mark.asyncio
async def test_create_accepts_an_episode_of_the_same_project(
    monkeypatch, client
) -> None:
    class _Episodes:
        async def get_by_id(self, episode_id):
            return {"id": 900, "project_id": 5}

    monkeypatch.setattr(projects_mod, "get_episode_repository", _Episodes)
    FakeService.result = project_row()
    resp = await client.post(
        BASE, json={"project_id": 5, "name": "Pilot", "episode_id": "900"}
    )
    assert resp.status_code == 200
    assert FakeService.calls == ["create_project"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, body",
    [("PUT", {"name": "Bea"}), ("DELETE", None)],
)
async def test_asset_writes_check_the_owning_script(
    monkeypatch, client, method, body
) -> None:
    seen: list = []

    async def _deny_script(script_id, auth):
        seen.append(script_id)
        raise HTTPException(status_code=403, detail="Access denied")

    monkeypatch.setattr(assets_mod, "verify_script_access", _deny_script)
    resp = await client.request(method, f"{BASE}/assets/9", json=body)
    assert resp.status_code == 403
    assert seen == [str(SID)]
    assert FakeService.calls == []


@pytest.mark.asyncio
async def test_asset_write_on_missing_asset_is_404(client) -> None:
    FakeService.asset_repo = FakeAssetRepo(None)
    resp = await client.delete(f"{BASE}/assets/9")
    assert resp.status_code == 404
    assert FakeService.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, body",
    [
        (f"{BASE}/{SID}/chapters", {"title": "One"}),
        (f"{BASE}/{SID}/canvas/sync", {}),
    ],
)
async def test_denied_chapter_writes_are_403_not_500(
    monkeypatch, client, path, body
) -> None:
    monkeypatch.setattr(canvas_mod, "verify_script_access", _deny)
    resp = await client.post(path, json=body)
    assert resp.status_code == 403
    assert FakeService.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"deleted_chapter_ids": ["777"]},
        {"updated_chapters": [{"id": "777", "title": "stolen"}]},
    ],
)
async def test_canvas_sync_rejects_chapters_of_another_script(client, body) -> None:
    FakeService.chapter_repo = FakeChapterRepo([{"id": 1}, {"id": 2}])
    resp = await client.post(f"{BASE}/{SID}/canvas/sync", json=body)
    assert resp.status_code == 404
    assert FakeService.calls == []


@pytest.mark.asyncio
async def test_canvas_sync_accepts_its_own_chapters(client) -> None:
    FakeService.chapter_repo = FakeChapterRepo([{"id": 1}, {"id": 2}])
    FakeService.result = {"chapters": []}
    resp = await client.post(
        f"{BASE}/{SID}/canvas/sync",
        json={"deleted_chapter_ids": ["1"], "updated_chapters": [{"id": 2}]},
    )
    assert resp.status_code == 200
    assert FakeService.calls == ["sync_canvas"]
