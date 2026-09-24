"""``/api/v1/ideation/topics``: wire parity after the routes gained response
models (P7), over real HTTP.

Rows come from the real ``topics_repository._topic_row`` applied to an ORM row
with every column set (``sample_orm``), so every key is present with the type
the repository really emits. Each body must equal ``jsonable_encoder`` of the
dict the handler builds (``tests/api/wire_parity.py``).

Also pinned: a non-numeric ``team_id`` / ``topic_id`` used to reach ``int()``
in the repository and answer 500; it is now "not a member" (403) / the typed
404 ``not_found_or_out_of_scope`` that every missing topic gets.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import Topics
from app.repositories.topics_repository import _topic_row
from tests.api.wire_parity import SAMPLE_BIGINT, assert_wire_unchanged, sample_orm

r = sys.modules["app.api.ideation_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
TEAM = str(SAMPLE_BIGINT + 7)
TOPIC_ID = SAMPLE_BIGINT + 70


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


def _topic(**overrides: Any) -> Dict[str, Any]:
    obj = sample_orm(
        Topics,
        id=TOPIC_ID,
        team_id=int(TEAM),
        status="shortlisted",
        created_by=uuid.UUID(USER),
    )
    for key, value in overrides.items():
        setattr(obj, key, value)
    return _topic_row(obj)


def _blank_topic() -> Dict[str, Any]:
    """A hand-written topic: no source, no cover, no creator (legacy row)."""
    return _topic(
        cover_url=None,
        excerpt=None,
        note_id=None,
        resource_id=None,
        media_id=None,
        inspiration_topic_id=None,
        created_by=None,
    )


class _Repo:
    def __init__(self) -> None:
        self.row: Optional[Dict[str, Any]] = _topic()
        self.rows: List[Dict[str, Any]] = [_topic(), _blank_topic()]
        self.calls: List[str] = []

    async def list_topics(self, team_id, *, status=None):
        self.calls.append("list")
        return self.rows

    async def create_topic(self, team_id, **kwargs):
        self.calls.append("create")
        return self.row

    async def get_topic_team_id(self, topic_id):
        self.calls.append("team_of")
        return TEAM if self.row else None

    async def get_topic(self, topic_id, team_id):
        return self.row

    async def update_topic(self, topic_id, team_id, **kwargs):
        self.calls.append("update")
        return self.row

    async def delete_topic(self, topic_id, team_id):
        self.calls.append("delete")
        return True


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth

    async def _allow() -> None:
        return None

    gate = r.router.dependencies[0].dependency
    app.dependency_overrides[gate] = _allow
    repo = _Repo()
    monkeypatch.setattr(r, "get_topics_repository", lambda: repo)

    async def _role(user_id, *, project_id=None, team_id=None):
        return "editor" if str(team_id) == TEAM else None

    monkeypatch.setattr(r, "resolve_effective_role", _role)
    yield repo
    app.dependency_overrides.pop(get_auth, None)
    app.dependency_overrides.pop(gate, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


URL = "/api/v1/ideation/topics"


@pytest.mark.asyncio
async def test_list_wire(client, _wiring) -> None:
    resp = await client.get(URL, params={"team_id": TEAM})
    assert_wire_unchanged(resp, {"success": True, "data": _wiring.rows})
    assert resp.json()["data"][0]["id"] == str(TOPIC_ID)  # ids stay strings


@pytest.mark.asyncio
async def test_create_wire(client, _wiring) -> None:
    resp = await client.post(URL, params={"team_id": TEAM}, json={"title": "Idea"})
    assert_wire_unchanged(resp, {"success": True, "data": _wiring.row})


@pytest.mark.asyncio
async def test_get_wire_blank_topic(client, _wiring) -> None:
    _wiring.row = _blank_topic()
    resp = await client.get(f"{URL}/{TOPIC_ID}")
    assert_wire_unchanged(resp, {"success": True, "data": _wiring.row})


@pytest.mark.asyncio
async def test_patch_wire(client, _wiring) -> None:
    resp = await client.patch(f"{URL}/{TOPIC_ID}", json={"status": "produced"})
    assert_wire_unchanged(resp, {"success": True, "data": _wiring.row})


@pytest.mark.asyncio
async def test_delete_wire(client) -> None:
    resp = await client.delete(f"{URL}/{TOPIC_ID}")
    assert_wire_unchanged(resp, {"success": True, "data": {"deleted": True}})


@pytest.mark.asyncio
async def test_missing_topic_is_typed_404(client, _wiring) -> None:
    _wiring.row = None
    _assert_typed_404(await client.get(f"{URL}/{TOPIC_ID}"))


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_non_numeric_topic_id_is_typed_404_not_500(
    client, _wiring, method
) -> None:
    kwargs = {"json": {"title": "x"}} if method == "PATCH" else {}
    _assert_typed_404(await client.request(method, f"{URL}/abc", **kwargs))
    assert _wiring.calls == []


@pytest.mark.asyncio
async def test_non_numeric_team_id_is_403_not_500(client, _wiring) -> None:
    assert (await client.get(URL, params={"team_id": "abc"})).status_code == 403
    resp = await client.post(URL, params={"team_id": "abc"}, json={"title": "x"})
    assert resp.status_code == 403
    assert _wiring.calls == []
