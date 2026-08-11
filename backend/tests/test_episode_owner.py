"""Tests for PATCH /episodes/{episode_id} ``owner_id`` (集负责人, Task 6,
workspace IA redesign spec §5 方案 A).

Mirrors the ASGI-transport + monkeypatch pattern from
``test_episodes_scenes_authz_wiring.py`` (no real DB needed — repository
methods and ``resolve_effective_role`` are monkeypatched). The base write
guard (``verify_episode_write_access``) is overridden to a no-op for every
test here since these tests target the NEW owner_id permission branch
layered on top of it, not the base guard itself (that's already pinned by
the sibling authz-wiring test file).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.core.scope_guards import verify_episode_write_access
from app.main import app

pytestmark = pytest.mark.unit

FAKE_USER_ID = str(uuid4())
MEMBER_UUID = str(uuid4())
PROJECT_ID = "123"
EPISODE_ID = "777"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth_and_guard():
    app.dependency_overrides[get_auth] = _fake_auth
    app.dependency_overrides[verify_episode_write_access] = lambda: None
    yield
    app.dependency_overrides.pop(get_auth, None)
    app.dependency_overrides.pop(verify_episode_write_access, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _stub_episode_repository(monkeypatch, *, update_return=None):
    """Wire ``get_episode_repository()`` calls to fakes and return a
    ``captured`` dict the test can assert on. ``update`` is only invoked by
    the router after any owner_id permission check passes."""
    from app.repositories.episode_repository import EpisodeRepository

    captured: dict = {}

    async def fake_get_by_id(self, episode_id):
        captured["get_by_id_called_with"] = episode_id
        return {"id": episode_id, "project_id": PROJECT_ID, "title": "Ep 1"}

    async def fake_update(self, episode_id, data):
        captured["update_called_with"] = (episode_id, data)
        if update_return is not None:
            return update_return
        return {"id": episode_id, "project_id": PROJECT_ID, **data}

    monkeypatch.setattr(EpisodeRepository, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(EpisodeRepository, "update", fake_update)
    return captured


def _stub_role(monkeypatch, role):
    """Monkeypatch ``resolve_effective_role`` as imported into the router
    module (lazy-imported inside the handler, so patch the source module)."""
    import app.core.workflow_roles as workflow_roles

    captured_calls: list = []

    async def fake_resolve(user_id, *, project_id=None, team_id=None):
        captured_calls.append({"user_id": user_id, "project_id": project_id})
        return role

    monkeypatch.setattr(workflow_roles, "resolve_effective_role", fake_resolve)
    return captured_calls


# --------------------------------------------------------------------------- #
# Manager can set/clear owner_id
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_project_owner_can_set_episode_owner(client, monkeypatch):
    captured = _stub_episode_repository(monkeypatch)
    role_calls = _stub_role(monkeypatch, "manager")

    resp = await client.patch(
        f"/api/v1/episodes/{EPISODE_ID}", json={"owner_id": MEMBER_UUID}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["owner_id"] == MEMBER_UUID
    # Permission was actually checked against the episode's project, not a
    # rubber stamp.
    assert role_calls == [{"user_id": FAKE_USER_ID, "project_id": PROJECT_ID}]
    _, update_data = captured["update_called_with"]
    assert update_data["owner_id"] == MEMBER_UUID


@pytest.mark.asyncio
async def test_manager_can_clear_episode_owner_with_null(client, monkeypatch):
    captured = _stub_episode_repository(monkeypatch)
    _stub_role(monkeypatch, "manager")

    resp = await client.patch(f"/api/v1/episodes/{EPISODE_ID}", json={"owner_id": None})

    assert resp.status_code == 200
    assert resp.json()["data"]["owner_id"] is None
    _, update_data = captured["update_called_with"]
    assert update_data["owner_id"] is None


# --------------------------------------------------------------------------- #
# Non-manager forbidden
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_non_owner_cannot_set_episode_owner(client, monkeypatch):
    captured = _stub_episode_repository(monkeypatch)
    _stub_role(monkeypatch, "editor")

    resp = await client.patch(
        f"/api/v1/episodes/{EPISODE_ID}", json={"owner_id": MEMBER_UUID}
    )

    assert resp.status_code == 403
    # NOTE: the router raises HTTPException(403, detail={"code": ...}) per
    # the brief's ambiguity resolution #2 (and matching the house pattern
    # used by episode_not_empty / BLOCK_DEPS_PENDING elsewhere) — but
    # app.main.app registers a global HTTPException handler
    # (app/core/exceptions.py::_handle_http_exception) that reshapes any
    # dict ``detail`` into ``{"details": {...}, "code": "http_<status>", ...}``.
    # The brief's Step 2 skeleton (`r.json()["detail"]["code"]`) assumed
    # FastAPI's un-customized default envelope, which this app does not use.
    # Verified live: {'success': False, 'error': 'Request failed',
    # 'code': 'http_403', 'request_id': None,
    # 'details': {'code': 'episode_owner_forbidden'}}
    assert resp.json()["details"]["code"] == "episode_owner_forbidden"
    # The write must never have happened.
    assert "update_called_with" not in captured


@pytest.mark.asyncio
async def test_no_role_cannot_set_episode_owner(client, monkeypatch):
    """No membership anywhere resolves to None — must not equal 'manager'
    and must still 403, not 500 (None != MANAGER, no crash)."""
    _stub_episode_repository(monkeypatch)
    _stub_role(monkeypatch, None)

    resp = await client.patch(
        f"/api/v1/episodes/{EPISODE_ID}", json={"owner_id": MEMBER_UUID}
    )

    assert resp.status_code == 403
    # See the wire-shape note in test_non_owner_cannot_set_episode_owner above.
    assert resp.json()["details"]["code"] == "episode_owner_forbidden"


# --------------------------------------------------------------------------- #
# Field-set sentinel: owner_id absent leaves existing field permissions
# untouched (ambiguity resolution #2 — must NOT regress today's behavior)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_patch_without_owner_field_unaffected(client, monkeypatch):
    captured = _stub_episode_repository(monkeypatch)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError(
            "resolve_effective_role must not be called when owner_id is absent"
        )

    import app.core.workflow_roles as workflow_roles

    monkeypatch.setattr(workflow_roles, "resolve_effective_role", _fail_if_called)

    resp = await client.patch(
        f"/api/v1/episodes/{EPISODE_ID}", json={"title": "Renamed"}
    )

    assert resp.status_code == 200  # existing field permission unchanged
    _, update_data = captured["update_called_with"]
    assert update_data == {"title": "Renamed"}
    assert "owner_id" not in update_data
    assert "get_by_id_called_with" not in captured  # no extra lookup either


# --------------------------------------------------------------------------- #
# Validation: owner_id must be a real UUID (Pydantic type, not raw str)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_owner_id_rejects_non_uuid_422(client, monkeypatch):
    _stub_episode_repository(monkeypatch)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("must 422 before reaching the permission check")

    import app.core.workflow_roles as workflow_roles

    monkeypatch.setattr(workflow_roles, "resolve_effective_role", _fail_if_called)

    resp = await client.patch(
        f"/api/v1/episodes/{EPISODE_ID}", json={"owner_id": "not-a-uuid"}
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_episode_owner_schema_field_is_uuid_typed():
    """Schema-level unit check (no DB, no ASGI) — the minimal closed loop
    from the brief's Step 3 when INTEGRATION_DATABASE_URL is absent: pins
    that ``owner_id`` is a real UUID-typed optional field on EpisodeUpdate,
    not a raw str (ambiguity resolution #4)."""
    from uuid import UUID

    from pydantic import ValidationError

    from app.schemas.script import EpisodeUpdate

    ok = EpisodeUpdate(owner_id=MEMBER_UUID)
    assert isinstance(ok.owner_id, UUID)
    assert "owner_id" in ok.model_fields_set

    omitted = EpisodeUpdate(title="X")
    assert "owner_id" not in omitted.model_fields_set

    with pytest.raises(ValidationError):
        EpisodeUpdate(owner_id="not-a-uuid")
