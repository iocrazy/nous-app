"""Smart-collection refresh / init-presets: wire parity after they gained
response models (P9).

Both routes run over real HTTP through the real repository code with a
scripted session that hands back ORM rows with every column set
(``sample_orm``). ``refresh`` scripts only ``CollectionsService.
refresh_collection_cache`` (rule evaluation is not what this pins); the body
is built in the route.

Also pinned: ``refresh`` only touches a collection the caller owns — the
lookup is ``WHERE id = :id AND user_id = :caller`` and a miss is 404 before
any refresh runs.
"""

from __future__ import annotations

import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import SmartCollections
from tests.api.wire_parity import SAMPLE_BIGINT, assert_wire_unchanged, sample_orm

repo_mod = sys.modules["app.repositories.collections_repository"]
svc_mod = sys.modules["app.services.library.collections_service"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
COLLECTION_ID = str(SAMPLE_BIGINT)


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _Result:
    def __init__(self, objs: List[Any] | None = None):
        self._objs = objs or []

    def scalars(self):
        objs = self._objs

        class _S:
            def all(self):
                return list(objs)

            def first(self):
                return objs[0] if objs else None

        return _S()


class _Db:
    results: List[_Result] = []
    statements: List[Any] = []
    refreshed: List[str] = []


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    _Db.results = []
    _Db.statements = []
    _Db.refreshed = []

    class _Session:
        async def execute(self, stmt):
            _Db.statements.append(stmt)
            return _Db.results.pop(0)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    async def _refresh(self, collection_id: str, user_id: str) -> int:
        _Db.refreshed.append(collection_id)
        return 1234

    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    monkeypatch.setattr(repo_mod, "write_scope", _scope)
    monkeypatch.setattr(
        svc_mod.CollectionsService, "refresh_collection_cache", _refresh
    )
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _collection(**overrides: Any) -> SmartCollections:
    obj = sample_orm(SmartCollections, user_id=uuid.UUID(USER))
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


# ── refresh ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_owner_wire(client) -> None:
    _Db.results = [_Result([_collection(id=SAMPLE_BIGINT)])]
    resp = await client.post(f"/api/v1/collections/{COLLECTION_ID}/refresh")
    assert_wire_unchanged(
        resp,
        {
            "message": "Collection refreshed",
            "collection_id": COLLECTION_ID,
            "media_count": 1234,
        },
    )
    assert _Db.refreshed == [COLLECTION_ID]
    # The lookup is scoped to the caller.
    params = _Db.statements[0].compile(dialect=postgresql.dialect()).params
    assert SAMPLE_BIGINT in params.values()
    assert uuid.UUID(USER) in params.values()


@pytest.mark.asyncio
async def test_refresh_foreign_collection_is_404(client) -> None:
    # Another user's collection: the user-scoped lookup finds nothing.
    _Db.results = [_Result([])]
    resp = await client.post(f"/api/v1/collections/{COLLECTION_ID}/refresh")
    assert resp.status_code == 404, resp.text
    assert _Db.refreshed == []


# ── init-presets ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_presets_creates_wire(client) -> None:
    created = [
        _collection(id=SAMPLE_BIGINT + i, name=f"Preset {i}", is_preset=True)
        for i in range(4)
    ]
    # get_preset_collections → none yet; INSERT ... RETURNING → four rows.
    _Db.results = [_Result([]), _Result(created)]
    raw = {
        "message": "Preset collections created",
        "count": 4,
        "presets": [{"id": c.id, "name": c.name} for c in created],
    }
    resp = await client.post("/api/v1/collections/init-presets")
    assert_wire_unchanged(resp, raw)
    # Snowflake ids stay JSON numbers.
    assert resp.json()["presets"][0]["id"] == SAMPLE_BIGINT


@pytest.mark.asyncio
async def test_init_presets_existing_wire(client) -> None:
    _Db.results = [_Result([_collection(is_preset=True), _collection()])]
    resp = await client.post("/api/v1/collections/init-presets")
    # No ``presets`` key when nothing was created.
    assert_wire_unchanged(
        resp, {"message": "Preset collections already exist", "count": 2}
    )
