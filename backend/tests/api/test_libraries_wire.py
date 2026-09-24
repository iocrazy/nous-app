"""``/api/v1/libraries``: who may do what, and (P7) wire parity.

Access, pinned here. These routes used to check nothing: any signed-in user
could list any team's libraries by id, create one inside a team they were not
in, and read, rename or delete any library by id.

- list / create: the caller must be a member of ``scope_id``'s team (403).
- id routes: a library the caller cannot read answers like a missing one
  (typed 404 ``not_found_or_out_of_scope``) and is never written.
- ``libraries`` has no ``team_id`` column; ownership is ``scope_type`` +
  ``scope_id`` (``team`` / ``user`` / ``project``).
"""

from __future__ import annotations

import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.api.library_access as access
from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import Libraries
from app.repositories.libraries_repository import _library_to_dict
from tests.api.wire_parity import SAMPLE_BIGINT, assert_wire_unchanged, sample_orm

svc_mod = sys.modules["app.services.library.libraries_service"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OTHER = "00000000-0000-0000-0000-000000000099"
TEAM = "7300000000000000777"
FOREIGN_TEAM = "7300000000000000888"
LIB_ID = SAMPLE_BIGINT + 40


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


def _library(**overrides: Any) -> Dict[str, Any]:
    """What the repository hands the router: the real ``_library_to_dict`` over
    an ORM row with every column set."""
    obj = sample_orm(
        Libraries,
        id=LIB_ID,
        scope_type="team",
        scope_id=TEAM,
        created_by=uuid.UUID(USER),
    )
    for key, value in overrides.items():
        setattr(obj, key, value)
    return _library_to_dict(obj)


class _Repo:
    def __init__(self) -> None:
        self.row: Optional[Dict[str, Any]] = _library()
        self.rows: List[Dict[str, Any]] = [self.row]
        self.writes: List[tuple] = []

    async def get_by_id(self, library_id: str):
        return self.row

    async def list_by_scope(self, scope_type: str, scope_id: str):
        self.writes.append(("list", scope_type, scope_id))
        return self.rows

    async def create(self, data: dict):
        self.writes.append(("create", data))
        return {**_library(), "name": data["name"]}

    async def update(self, library_id: str, data: dict):
        self.writes.append(("update", library_id, data))
        return {**(self.row or {}), **data} if self.row else {}

    async def delete(self, library_id: str):
        self.writes.append(("delete", library_id))
        return True


# (team_id → role) the fake membership table answers from.
ROLES: Dict[str, Optional[str]] = {}


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    repo = _Repo()
    monkeypatch.setattr(svc_mod, "get_libraries_repository", lambda: repo)
    ROLES.clear()
    ROLES[TEAM] = "editor"

    async def _role(user_id, *, project_id=None, team_id=None):
        if project_id is not None:
            return ROLES.get(f"project:{project_id}")
        return ROLES.get(str(team_id))

    monkeypatch.setattr(access, "resolve_effective_role", _role)
    yield repo
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_foreign_team_is_403_and_never_read(client, _wiring) -> None:
    resp = await client.get("/api/v1/libraries", params={"scope_id": FOREIGN_TEAM})
    assert resp.status_code == 403
    assert _wiring.writes == []


@pytest.mark.asyncio
async def test_list_own_team_is_allowed(client, _wiring) -> None:
    resp = await client.get("/api/v1/libraries", params={"scope_id": TEAM})
    assert resp.status_code == 200
    assert _wiring.writes == [("list", "team", TEAM)]


@pytest.mark.asyncio
async def test_list_junk_scope_id_is_403_not_500(client) -> None:
    resp = await client.get("/api/v1/libraries", params={"scope_id": "abc"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_in_foreign_team_is_403_and_never_written(client, _wiring) -> None:
    resp = await client.post(
        "/api/v1/libraries", json={"name": "Stolen", "scope_id": FOREIGN_TEAM}
    )
    assert resp.status_code == 403
    assert _wiring.writes == []


@pytest.mark.asyncio
async def test_create_in_own_team_is_allowed(client, _wiring) -> None:
    resp = await client.post(
        "/api/v1/libraries", json={"name": "Mine", "scope_id": TEAM}
    )
    assert resp.status_code == 200, resp.text
    assert _wiring.writes[0][0] == "create"


ID_CALLS = [
    ("GET", None),
    ("PATCH", {"name": "Renamed"}),
    ("DELETE", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,body", ID_CALLS)
async def test_foreign_team_library_is_typed_404_and_never_written(
    client, _wiring, method, body
) -> None:
    _wiring.row = _library(scope_id=FOREIGN_TEAM)
    kwargs = {"json": body} if body else {}
    resp = await client.request(method, f"/api/v1/libraries/{LIB_ID}", **kwargs)
    _assert_typed_404(resp)
    assert _wiring.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method,body", ID_CALLS)
async def test_own_team_library_is_allowed(client, _wiring, method, body) -> None:
    kwargs = {"json": body} if body else {}
    resp = await client.request(method, f"/api/v1/libraries/{LIB_ID}", **kwargs)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method,body", ID_CALLS)
async def test_other_users_personal_library_is_typed_404(
    client, _wiring, method, body
) -> None:
    _wiring.row = _library(scope_type="user", scope_id=OTHER)
    kwargs = {"json": body} if body else {}
    resp = await client.request(method, f"/api/v1/libraries/{LIB_ID}", **kwargs)
    _assert_typed_404(resp)
    assert _wiring.writes == []


@pytest.mark.asyncio
async def test_own_personal_library_is_allowed(client, _wiring) -> None:
    _wiring.row = _library(scope_type="user", scope_id=USER)
    resp = await client.delete(f"/api/v1/libraries/{LIB_ID}")
    assert resp.status_code == 200
    assert _wiring.writes == [("delete", str(LIB_ID))]


@pytest.mark.asyncio
async def test_project_library_follows_project_role(client, _wiring) -> None:
    project_id = "7300000000000000555"
    _wiring.row = _library(scope_type="project", scope_id=project_id)
    resp = await client.get(f"/api/v1/libraries/{LIB_ID}")
    _assert_typed_404(resp)
    ROLES[f"project:{project_id}"] = "viewer"
    assert (await client.get(f"/api/v1/libraries/{LIB_ID}")).status_code == 200
    # A viewer reads but may not change it.
    resp = await client.patch(f"/api/v1/libraries/{LIB_ID}", json={"name": "x"})
    assert resp.status_code == 403
    assert _wiring.writes == []


@pytest.mark.asyncio
async def test_missing_library_is_typed_404(client, _wiring) -> None:
    _wiring.row = None
    _assert_typed_404(await client.get(f"/api/v1/libraries/{LIB_ID}"))


@pytest.mark.asyncio
async def test_non_numeric_library_id_is_typed_404_not_500(client) -> None:
    _assert_typed_404(await client.get("/api/v1/libraries/abc"))
