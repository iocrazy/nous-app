"""Scene routes (``script_scenes_router``): wire parity after they gained
response models (OpenAPI P6).

Every JSON route runs over real HTTP against the real app. The repository is
stubbed to hand back rows built by its own ``_row`` from an ORM object with
every column set (``sample_orm``), so each row carries every column in its
real native type, and the handler's own ``_to_response`` then stringifies the
bigint ids. The body must equal what FastAPI sent for the dict the handler
builds with no model — see ``tests/api/wire_parity.py``.

Ids here are JSON **strings** (#1809 stringifies them at this router's
boundary), unlike ``/scripts/projects`` which keeps the same columns numeric.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect

from app.core.config import settings
from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_scene_access,
    verify_scene_read_access,
    verify_script_access,
    verify_script_read_access,
)
from app.main import app
from app.models import ScriptScenes
from app.repositories.script_scene_repository import VersionConflict
from app.repositories.script_scene_repository import _row as scene_row
from app.schemas import script_scene_responses as sr
from tests.api.wire_parity import (
    SAMPLE_BIGINT,
    assert_wire_unchanged,
    column_names,
    sample_orm,
)

r = importlib.import_module("app.api.script_scenes_router")

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
SCENE_ID = SAMPLE_BIGINT + 21
SCRIPT_ID = SAMPLE_BIGINT + 22
GUARDS = (
    verify_scene_access,
    verify_scene_read_access,
    verify_script_access,
    verify_script_read_access,
)


def _nullable_overrides() -> dict[str, Any]:
    return {
        prop.key: None
        for prop in inspect(ScriptScenes).column_attrs
        if prop.columns[0].nullable and not prop.columns[0].primary_key
    }


def scene(**over: Any) -> dict:
    """What the repository hands the router: ``_row`` of a full ORM row."""
    elements = [{"id": "el_1", "type": "action", "text": "Rain."}]
    return scene_row(sample_orm(ScriptScenes, content_json=elements, **over))


def numbered(number: Any = "3", **over: Any) -> dict:
    row = scene(**over)
    row["scene_no_in_episode"] = number
    return row


class FakeRepo:
    """Every method returns ``FakeRepo.result`` (or raises it)."""

    result: Any = None
    calls: list = []

    def __getattr__(self, name):
        async def _method(*args, **kwargs):
            FakeRepo.calls.append(name)
            if isinstance(FakeRepo.result, Exception):
                raise FakeRepo.result
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
    FakeRepo.calls = []
    monkeypatch.setattr(r, "get_script_scene_repository", FakeRepo)
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


# --------------------------------------------------------------------------- #
# The row model is pinned to its table
# --------------------------------------------------------------------------- #


def test_scene_model_declares_exactly_the_columns() -> None:
    assert set(sr.ScriptSceneResponse.model_fields) == column_names(ScriptScenes)
    assert set(sr.ScriptSceneNumbered.model_fields) == column_names(ScriptScenes) | {
        "scene_no_in_episode"
    }


def test_bigint_ids_are_declared_as_strings() -> None:
    for field in ("id", "script_id", "chapter_id", "location_id"):
        assert "str" in str(sr.ScriptSceneResponse.model_fields[field].annotation)


# --------------------------------------------------------------------------- #
# Row routes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_wire_unchanged(client) -> None:
    rows = [numbered("1"), numbered(None, **_nullable_overrides())]
    FakeRepo.result = rows
    resp = await client.get(f"/api/v1/scripts/{SCRIPT_ID}/scenes")
    assert_wire_unchanged(resp, _env(r._to_response_list(rows)))
    first = resp.json()["data"][0]
    assert isinstance(first["id"], str) and int(first["id"]) > 2**53
    assert first["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_create_wire_unchanged(client) -> None:
    FakeRepo.result = scene()
    resp = await client.post(f"/api/v1/scripts/{SCRIPT_ID}/scenes", json={})
    assert_wire_unchanged(resp, _env(r._to_response(FakeRepo.result)))
    assert "scene_no_in_episode" not in resp.json()["data"]


@pytest.mark.asyncio
async def test_get_wire_unchanged(client) -> None:
    FakeRepo.result = numbered("12A")
    resp = await client.get(f"/api/v1/scenes/{SCENE_ID}")
    assert_wire_unchanged(resp, _env(r._to_response(FakeRepo.result)))


@pytest.mark.asyncio
async def test_get_of_nullable_row_wire_unchanged(client) -> None:
    FakeRepo.result = numbered(None, **_nullable_overrides())
    resp = await client.get(f"/api/v1/scenes/{SCENE_ID}")
    assert_wire_unchanged(resp, _env(r._to_response(FakeRepo.result)))


@pytest.mark.asyncio
async def test_patch_that_writes_returns_the_bare_row(client) -> None:
    FakeRepo.result = scene()
    resp = await client.patch(
        f"/api/v1/scenes/{SCENE_ID}", json={"heading_int_ext": "INT"}
    )
    assert_wire_unchanged(resp, _env(r._to_response(FakeRepo.result)))
    assert "scene_no_in_episode" not in resp.json()["data"]


@pytest.mark.asyncio
async def test_patch_with_nothing_to_write_keeps_the_number(client) -> None:
    """An empty update re-reads through ``get_by_id``, which carries the
    derived number — including a null one, which must stay on the wire."""
    for number in ("4", None):
        FakeRepo.result = numbered(number)
        resp = await client.patch(f"/api/v1/scenes/{SCENE_ID}", json={})
        assert_wire_unchanged(resp, _env(r._to_response(FakeRepo.result)))
        assert resp.json()["data"]["scene_no_in_episode"] == number


@pytest.mark.asyncio
async def test_move_wire_unchanged(client) -> None:
    FakeRepo.result = scene()
    resp = await client.post(
        f"/api/v1/scenes/{SCENE_ID}/move", json={"chapter_id": None}
    )
    assert_wire_unchanged(resp, _env(r._to_response(FakeRepo.result)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", f"/api/v1/scenes/{SCENE_ID}", None),
        ("PATCH", f"/api/v1/scenes/{SCENE_ID}", {"heading_int_ext": "INT"}),
        ("POST", f"/api/v1/scenes/{SCENE_ID}/move", {}),
        ("POST", f"/api/v1/scripts/{SCRIPT_ID}/scenes", {}),
    ],
)
@pytest.mark.parametrize("empty", [None, {}])
async def test_missing_row_is_typed_404(client, method, path, body, empty) -> None:
    """The repository answers ``None`` / ``{}`` when the row vanished after the
    guard's existence check; that is a typed 404, not a 200 with an empty body
    (``{}``) or a 500 from the response model."""
    FakeRepo.result = empty
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 404
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# --------------------------------------------------------------------------- #
# Delete
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"deleted": True, "omitted": False, "scene": None},
        {"deleted": False, "omitted": False, "scene": None},
        "omitted",
    ],
)
async def test_delete_wire_unchanged(client, result) -> None:
    if result == "omitted":
        result = {"deleted": False, "omitted": True, "scene": scene()}
    FakeRepo.result = result
    resp = await client.delete(f"/api/v1/scenes/{SCENE_ID}")
    expected = dict(result)
    expected["scene"] = r._to_response(result["scene"])
    assert_wire_unchanged(resp, _env(expected))


# --------------------------------------------------------------------------- #
# Element ops
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ops_wire_unchanged(client) -> None:
    FakeRepo.result = {
        "content_version": 8,
        "elements": [{"id": "el_1", "type": "action", "text": "Rain."}],
    }
    resp = await client.post(
        f"/api/v1/scenes/{SCENE_ID}/elements/ops",
        json={"ops": []},
        headers={"If-Match": "7"},
    )
    assert_wire_unchanged(resp, _env(FakeRepo.result))


@pytest.mark.asyncio
async def test_ops_conflict_body_unchanged(client) -> None:
    elements = [{"id": "el_2", "type": "dialogue", "text": "No."}]
    FakeRepo.result = VersionConflict(9, elements)
    resp = await client.post(
        f"/api/v1/scenes/{SCENE_ID}/elements/ops",
        json={"ops": []},
        headers={"If-Match": "7"},
    )
    assert_wire_unchanged(
        resp,
        {
            "success": False,
            "code": "version_conflict",
            "current_version": 9,
            "elements": elements,
        },
        status=409,
    )
    body = resp.json()
    assert sr.ScriptSceneVersionConflict(**body).model_dump() == body


def test_ops_documents_its_409_body() -> None:
    op = app.openapi()["paths"]["/api/v1/scenes/{scene_id}/elements/ops"]["post"]
    ref = op["responses"]["409"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/ScriptSceneVersionConflict")


# --------------------------------------------------------------------------- #
# Copilot ops
# --------------------------------------------------------------------------- #


def _copilot(monkeypatch, *, version: int, ops: list, summary: str = "Tighten."):
    from app.services.ai.providers import ai_provider_helpers
    from app.services.storyboard.script import script_ai_service

    monkeypatch.setattr(settings, "FEATURE_COPILOT_OPS", True)
    FakeRepo.result = {"content_json": [], "content_version": version}

    async def _resolve(user_id):
        class _Cfg:
            agent_slug = "script_ai"
            provider_key = "doubao"
            provider_config: dict = {}
            origin = "platform"

        return _Cfg()

    class _Service:
        def __init__(self, **kwargs):
            pass

        async def instruction_to_element_ops(self, elements, instruction, **kw):
            return {"ops": ops, "summary": summary}

    monkeypatch.setattr(ai_provider_helpers, "resolve_script_ai_config", _resolve)
    monkeypatch.setattr(script_ai_service, "ScriptAIService", _Service)


_INSERT = {
    "op": "insert",
    "element_id": "el_keep",
    "payload": {"type": "action", "text": "Thunder."},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("read_version, proposal", [(5, False), (3, True)])
async def test_copilot_wire_unchanged(monkeypatch, client, read_version, proposal):
    _copilot(monkeypatch, version=5, ops=[_INSERT])
    resp = await client.post(
        f"/api/v1/scenes/{SCENE_ID}/copilot-ops",
        json={"instruction": "Add thunder", "read_version": read_version},
    )
    expected = {"ops": [_INSERT], "base_version": 5, "summary": "Tighten."}
    if proposal:
        expected["proposal"] = True
    assert_wire_unchanged(resp, _env(expected))
    assert ("proposal" in resp.json()["data"]) is proposal
