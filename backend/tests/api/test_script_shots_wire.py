"""Shot routes (``script_shots_router``): wire parity after they gained
response models (OpenAPI P6), and the five routes removed for having no
caller.

Rows come from the repository's own ``_row`` over an ORM object with every
column set (``sample_orm``); the handler's ``_to_response`` stringifies the
bigint ids. The body must equal what FastAPI sent for that dict with no model
— see ``tests/api/wire_parity.py``.
"""

from __future__ import annotations

import importlib
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_scene_access,
    verify_scene_read_access,
    verify_shot_access,
)
from app.main import app
from app.models import ScriptShots
from app.repositories.script_shot_repository import _row as shot_row
from app.schemas import script_shot_responses as sr
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    assert_wire_unchanged,
    column_names,
    sample_orm,
)

r = importlib.import_module("app.api.script_shots_router")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
SHOT_ID = SAMPLE_BIGINT + 31
SCENE_ID = SAMPLE_BIGINT + 32
GUARDS = (verify_scene_access, verify_scene_read_access, verify_shot_access)


def _nullable_overrides() -> dict[str, Any]:
    return {
        prop.key: None
        for prop in inspect(ScriptShots).column_attrs
        if prop.columns[0].nullable and not prop.columns[0].primary_key
    }


def shot(**over: Any) -> dict:
    return shot_row(sample_orm(ScriptShots, **over))


class FakeRepo:
    result: Any = None

    def __getattr__(self, name):
        async def _method(*args, **kwargs):
            return FakeRepo.result

        return _method


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    for guard in GUARDS:
        app.dependency_overrides[guard] = lambda: None
    FakeRepo.result = None
    monkeypatch.setattr(r, "get_script_shot_repository", FakeRepo)
    yield
    for dep in (get_auth, *GUARDS):
        app.dependency_overrides.pop(dep, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _env(data: Any) -> dict:
    return {"success": True, "data": data}


def test_shot_model_declares_exactly_the_columns() -> None:
    assert set(sr.StoryboardShotResponse.model_fields) == column_names(ScriptShots)


@pytest.mark.asyncio
async def test_list_wire_unchanged(client) -> None:
    rows = [shot(), shot(**_nullable_overrides())]
    FakeRepo.result = rows
    resp = await client.get(f"/api/v1/scenes/{SCENE_ID}/shots")
    assert_wire_unchanged(resp, _env(r._to_response_list(rows)))
    first = resp.json()["data"][0]
    assert isinstance(first["id"], str) and int(first["id"]) > 2**53
    assert isinstance(first["created_by_agent_run_id"], str)


@pytest.mark.asyncio
async def test_create_wire_unchanged(client) -> None:
    FakeRepo.result = shot()
    resp = await client.post(f"/api/v1/scenes/{SCENE_ID}/shots", json={})
    assert_wire_unchanged(resp, _env(r._to_response(FakeRepo.result)))


@pytest.mark.asyncio
async def test_update_wire_unchanged(client) -> None:
    FakeRepo.result = shot(**_nullable_overrides())
    resp = await client.patch(f"/api/v1/shots/{SHOT_ID}", json={"description": "x"})
    assert_wire_unchanged(resp, _env(r._to_response(FakeRepo.result)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, path, body",
    [
        ("POST", f"/api/v1/scenes/{SCENE_ID}/shots", {}),
        ("PATCH", f"/api/v1/shots/{SHOT_ID}", {"description": "x"}),
    ],
)
@pytest.mark.parametrize("empty", [None, {}])
async def test_missing_row_is_typed_404(client, method, path, body, empty) -> None:
    FakeRepo.result = empty
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


@pytest.mark.asyncio
async def test_auto_storyboard_wire_unchanged(monkeypatch, client) -> None:
    task_id = str(uuid.uuid4())
    mgr = AsyncMock()
    mgr.create = AsyncMock(return_value=task_id)
    monkeypatch.setattr(r, "get_task_manager", lambda: mgr)
    monkeypatch.setattr(
        "app.services.infra.dbos_orchestrator.start_workflow_routed",
        AsyncMock(return_value={"mode": "dbos"}),
    )
    resp = await client.post(f"/api/v1/scenes/{SCENE_ID}/auto-storyboard")
    assert_wire_unchanged(resp, {"success": True, "task_id": task_id})


# --------------------------------------------------------------------------- #
# Removed: no caller since the editor's storyboard view was retired (#1797)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/api/v1/shots/{shot_id}"),
        ("DELETE", "/api/v1/shots/{shot_id}"),
        ("POST", "/api/v1/shots/{shot_id}/move"),
        ("POST", "/api/v1/shots/{shot_id}/generate"),
        ("POST", "/api/v1/scripts/{script_id}/scenes/after-lock"),
        # OpenAPI P7: the shot video workflow retired with its only dispatch
        # site; the flag was never on in production.
        ("POST", "/api/v1/shots/{shot_id}/generate-video"),
    ],
)
def test_removed_route_is_gone(method, path) -> None:
    paths = app.openapi()["paths"]
    assert method.lower() not in paths.get(path, {})
