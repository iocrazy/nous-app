"""Response-boundary id stringify tests for shots/scenes routers (P0 repro fix).

2026-08 production incident: `script_shots_router` / `script_scenes_router`
serialized Snowflake BIGINT ids as JSON *numbers*. Above 2^53 that's silent
precision loss; even below it, the frontend's canvas shot-node reconciliation
(`shotSync.ts` `readId`) string-keys ids, so a bare int response mismatched
and caused duplicate/hidden shot nodes on every reconcile
(`6a239ac3 fix(canvas): 生产画布空白+对账重复入驻`).

These tests pin the fix at the router response boundary: every id/FK field
in a shot or scene response body must be a `str`, even when the repository
(which deliberately keeps bigint ids as native `int` for internal use —
strategy-C parity) hands back a Python `int` — including one so large it
would lose precision as a JS `number`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import (
    verify_scene_access,
    verify_scene_read_access,
    verify_script_access,
    verify_script_read_access,
    verify_shot_access,
    verify_shot_read_access,
)
from app.main import app

pytestmark = pytest.mark.unit

FAKE_USER_ID = str(uuid4())

# > 2^53 (9007199254740992) — a JS `number` would silently lose precision if
# this ever leaked through as a JSON number instead of a string.
BIG_ID = 9007199254740993
BIG_SCENE_ID = 9007199254740995
BIG_SCRIPT_ID = 9007199254740997
BIG_CHAPTER_ID = 9007199254740999
BIG_LOCATION_ID = 9007199254741001
BIG_AGENT_RUN_ID = 9007199254741003


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest.fixture(autouse=True)
def _bypass_guards():
    """These tests target the response serialization contract, not authz —
    pass every scope guard unconditionally (mirrors the pattern in
    test_episodes_scenes_authz_wiring.py's `_bypass_scene_guard`). Includes
    the *_read_access variants (2026-08-12 gate split) since the GET routes
    under test now declare those instead of the write-semantics guards."""
    app.dependency_overrides[verify_shot_access] = lambda: None
    app.dependency_overrides[verify_shot_read_access] = lambda: None
    app.dependency_overrides[verify_scene_access] = lambda: None
    app.dependency_overrides[verify_scene_read_access] = lambda: None
    app.dependency_overrides[verify_script_access] = lambda: None
    app.dependency_overrides[verify_script_read_access] = lambda: None
    yield
    app.dependency_overrides.pop(verify_shot_access, None)
    app.dependency_overrides.pop(verify_shot_read_access, None)
    app.dependency_overrides.pop(verify_scene_access, None)
    app.dependency_overrides.pop(verify_scene_read_access, None)
    app.dependency_overrides.pop(verify_script_access, None)
    app.dependency_overrides.pop(verify_script_read_access, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _big_shot_row() -> dict:
    return {
        "id": BIG_ID,
        "scene_id": BIG_SCENE_ID,
        "shot_number": 1,
        "status": "empty",
        "created_by_agent_run_id": BIG_AGENT_RUN_ID,
    }


def _big_scene_row() -> dict:
    return {
        "id": BIG_ID,
        "script_id": BIG_SCRIPT_ID,
        "chapter_id": BIG_CHAPTER_ID,
        "location_id": BIG_LOCATION_ID,
        "content_version": 0,
    }


# --------------------------------------------------------------------------- #
# Shots
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_shots_ids_are_strings(client, monkeypatch):
    from app.repositories.script_shot_repository import ScriptShotRepository

    async def fake_list_by_scene(self, scene_id):
        return [_big_shot_row()]

    monkeypatch.setattr(ScriptShotRepository, "list_by_scene", fake_list_by_scene)

    resp = await client.get(f"/api/v1/scenes/{BIG_SCENE_ID}/shots")
    assert resp.status_code == 200
    shot = resp.json()["data"][0]
    assert isinstance(shot["id"], str)
    assert shot["id"] == str(BIG_ID)
    assert isinstance(shot["scene_id"], str)
    assert shot["scene_id"] == str(BIG_SCENE_ID)
    assert isinstance(shot["created_by_agent_run_id"], str)
    assert shot["created_by_agent_run_id"] == str(BIG_AGENT_RUN_ID)


@pytest.mark.asyncio
async def test_create_shot_ids_are_strings(client, monkeypatch):
    from app.repositories.script_shot_repository import ScriptShotRepository

    async def fake_create(self, data):
        return _big_shot_row()

    monkeypatch.setattr(ScriptShotRepository, "create", fake_create)

    resp = await client.post(f"/api/v1/scenes/{BIG_SCENE_ID}/shots", json={})
    assert resp.status_code == 200
    shot = resp.json()["data"]
    assert isinstance(shot["id"], str)
    assert isinstance(shot["scene_id"], str)


@pytest.mark.asyncio
async def test_get_shot_id_is_string(client, monkeypatch):
    from app.repositories.script_shot_repository import ScriptShotRepository

    async def fake_get_by_id(self, shot_id):
        return _big_shot_row()

    monkeypatch.setattr(ScriptShotRepository, "get_by_id", fake_get_by_id)

    resp = await client.get(f"/api/v1/shots/{BIG_ID}")
    assert resp.status_code == 200
    shot = resp.json()["data"]
    assert isinstance(shot["id"], str)
    assert shot["id"] == str(BIG_ID)


@pytest.mark.asyncio
async def test_update_shot_id_is_string(client, monkeypatch):
    from app.repositories.script_shot_repository import ScriptShotRepository

    async def fake_update(self, shot_id, data):
        return _big_shot_row()

    monkeypatch.setattr(ScriptShotRepository, "update", fake_update)

    resp = await client.patch(f"/api/v1/shots/{BIG_ID}", json={"description": "x"})
    assert resp.status_code == 200
    shot = resp.json()["data"]
    assert isinstance(shot["id"], str)


@pytest.mark.asyncio
async def test_move_shot_id_is_string(client, monkeypatch):
    from app.repositories.script_shot_repository import ScriptShotRepository

    async def fake_move_shot(self, shot_id, *, before_shot_id=None, after_shot_id=None):
        return _big_shot_row()

    monkeypatch.setattr(ScriptShotRepository, "move_shot", fake_move_shot)

    resp = await client.post(f"/api/v1/shots/{BIG_ID}/move", json={})
    assert resp.status_code == 200
    shot = resp.json()["data"]
    assert isinstance(shot["id"], str)
    assert isinstance(shot["scene_id"], str)


@pytest.mark.asyncio
async def test_get_shot_404_data_shape_unaffected(client, monkeypatch):
    """A missing shot's 404 path never reaches `_to_response` — no regression
    on the not-found envelope."""
    from app.repositories.script_shot_repository import ScriptShotRepository

    async def fake_get_by_id(self, shot_id):
        return None

    monkeypatch.setattr(ScriptShotRepository, "get_by_id", fake_get_by_id)

    resp = await client.get(f"/api/v1/shots/{BIG_ID}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_scenes_ids_are_strings(client, monkeypatch):
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_list_by_script(self, script_id):
        return [_big_scene_row()]

    monkeypatch.setattr(ScriptSceneRepository, "list_by_script", fake_list_by_script)

    resp = await client.get(f"/api/v1/scripts/{BIG_SCRIPT_ID}/scenes")
    assert resp.status_code == 200
    scene = resp.json()["data"][0]
    assert isinstance(scene["id"], str)
    assert scene["id"] == str(BIG_ID)
    assert isinstance(scene["script_id"], str)
    assert scene["script_id"] == str(BIG_SCRIPT_ID)
    assert isinstance(scene["chapter_id"], str)
    assert scene["chapter_id"] == str(BIG_CHAPTER_ID)
    assert isinstance(scene["location_id"], str)
    assert scene["location_id"] == str(BIG_LOCATION_ID)


@pytest.mark.asyncio
async def test_create_scene_ids_are_strings(client, monkeypatch):
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_create(self, data):
        return _big_scene_row()

    monkeypatch.setattr(ScriptSceneRepository, "create", fake_create)

    resp = await client.post(f"/api/v1/scripts/{BIG_SCRIPT_ID}/scenes", json={})
    assert resp.status_code == 200
    scene = resp.json()["data"]
    assert isinstance(scene["id"], str)
    assert isinstance(scene["script_id"], str)


@pytest.mark.asyncio
async def test_get_scene_id_is_string(client, monkeypatch):
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_get_by_id(self, scene_id):
        return _big_scene_row()

    monkeypatch.setattr(ScriptSceneRepository, "get_by_id", fake_get_by_id)

    resp = await client.get(f"/api/v1/scenes/{BIG_ID}")
    assert resp.status_code == 200
    scene = resp.json()["data"]
    assert isinstance(scene["id"], str)
    assert scene["id"] == str(BIG_ID)


@pytest.mark.asyncio
async def test_update_scene_id_is_string(client, monkeypatch):
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_update_meta(self, scene_id, data):
        return _big_scene_row()

    monkeypatch.setattr(ScriptSceneRepository, "update_meta", fake_update_meta)

    resp = await client.patch(
        f"/api/v1/scenes/{BIG_ID}", json={"heading_int_ext": "INT"}
    )
    assert resp.status_code == 200
    scene = resp.json()["data"]
    assert isinstance(scene["id"], str)


@pytest.mark.asyncio
async def test_move_scene_id_is_string(client, monkeypatch):
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_move_scene(
        self, scene_id, *, chapter_id=None, before_scene_id=None, after_scene_id=None
    ):
        return _big_scene_row()

    monkeypatch.setattr(ScriptSceneRepository, "move_scene", fake_move_scene)

    resp = await client.post(f"/api/v1/scenes/{BIG_ID}/move", json={})
    assert resp.status_code == 200
    scene = resp.json()["data"]
    assert isinstance(scene["id"], str)
    assert isinstance(scene["script_id"], str)


@pytest.mark.asyncio
async def test_delete_scene_omitted_nested_id_is_string(client, monkeypatch):
    """delete() returns {"deleted","omitted","scene"} — the nested `scene`
    dict (the OMITTED-in-place row, post-lock) must also be stringified."""
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_delete(self, scene_id):
        return {"deleted": False, "omitted": True, "scene": _big_scene_row()}

    monkeypatch.setattr(ScriptSceneRepository, "delete", fake_delete)

    resp = await client.delete(f"/api/v1/scenes/{BIG_ID}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["omitted"] is True
    assert isinstance(data["scene"]["id"], str)
    assert data["scene"]["id"] == str(BIG_ID)


@pytest.mark.asyncio
async def test_delete_scene_hard_delete_null_scene_unaffected(client, monkeypatch):
    """Pre-lock hard-delete returns scene=None — `_to_response(None)` must
    stay None, not blow up or coerce to a truthy placeholder."""
    from app.repositories.script_scene_repository import ScriptSceneRepository

    async def fake_delete(self, scene_id):
        return {"deleted": True, "omitted": False, "scene": None}

    monkeypatch.setattr(ScriptSceneRepository, "delete", fake_delete)

    resp = await client.delete(f"/api/v1/scenes/{BIG_ID}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["deleted"] is True
    assert data["scene"] is None
