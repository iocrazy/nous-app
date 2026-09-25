"""Smart-collection refresh / init-presets: wire parity after they gained
response models (P9).

Both routes run over real HTTP through the real repository code with a
scripted session that hands back ORM rows with every column set
(``sample_orm``). ``refresh`` scripts only ``CollectionsService.
refresh_collection_cache`` (rule evaluation is not what this pins); the body
is built in the route.

Also pinned: ``refresh`` only touches a collection the caller owns — the
lookup is ``WHERE id = :id AND user_id = :caller`` and a miss is 404 before
any refresh runs — and a non-numeric id is 404, not a 500 from ``int()``.
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


@pytest.mark.asyncio
async def test_refresh_non_numeric_id_is_404(client) -> None:
    resp = await client.post("/api/v1/collections/not-an-id/refresh")
    assert resp.status_code == 404, resp.text
    assert _Db.statements == []
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


# ── list / get / media: the row model (id is a BIGINT, not a UUID) ────────
#
# ``CollectionResponse.id`` was declared ``UUID`` while ``smart_collections.id``
# is a Snowflake BIGINT, so every route returning a real row answered 500.
# These run the real repository conversion on a row whose id is past 2^53 and
# on a legacy row with every nullable column NULL.

RULES = {
    "match": "any",
    "conditions": [{"field": "tag", "operator": "in", "value": ["a"]}],
}
_NULLABLE = (
    "icon",
    "description",
    "cached_count",
    "cached_at",
    "is_preset",
    "sort_by",
    "sort_order",
    "created_at",
    "updated_at",
    "is_active",
    "color",
    "scope_id",
    "cached_video_ids",
)


def _row_dict(obj: SmartCollections) -> dict:
    """What the route builds for ``obj``: the repository's own conversion
    followed by the router's projection."""
    router_mod = sys.modules["app.api.collections_router"]
    return router_mod._collection_out(repo_mod._sc_to_dict(obj))


@pytest.mark.asyncio
async def test_list_wire_keeps_bigint_id_a_number(client) -> None:
    full = _collection(id=SAMPLE_BIGINT, rules=RULES)
    legacy = _collection(id=SAMPLE_BIGINT + 1, rules=RULES, **dict.fromkeys(_NULLABLE))
    _Db.results = [_Result([full, legacy])]
    resp = await client.get("/api/v1/collections")
    raw = {"collections": [_row_dict(full), _row_dict(legacy)], "total": 2}
    assert_wire_unchanged(resp, raw)
    body = resp.json()["collections"]
    assert body[0]["id"] == SAMPLE_BIGINT and SAMPLE_BIGINT > 2**53
    # The legacy row's NULLs fell back instead of failing validation.
    assert body[1]["icon"] == "📁" and body[1]["media_count"] == 0
    assert body[1]["is_active"] is True and body[1]["is_preset"] is False
    assert body[1]["sort_by"] == "created_at" and body[1]["sort_order"] == "desc"


@pytest.mark.asyncio
async def test_get_wire(client) -> None:
    row = _collection(id=SAMPLE_BIGINT, rules=RULES)
    _Db.results = [_Result([row])]
    resp = await client.get(f"/api/v1/collections/{COLLECTION_ID}")
    assert_wire_unchanged(resp, _row_dict(row))
    assert resp.json()["id"] == SAMPLE_BIGINT


@pytest.mark.asyncio
async def test_media_wire_collection_id_is_a_number(client, monkeypatch) -> None:
    row = _collection(id=SAMPLE_BIGINT, rules=RULES)
    _Db.results = [_Result([row])]

    async def _media(self, **_kw):
        return [{"id": 1}], 1

    monkeypatch.setattr(svc_mod.CollectionsService, "get_collection_media", _media)
    resp = await client.get(f"/api/v1/collections/{COLLECTION_ID}/media")
    raw = {
        "collection_id": SAMPLE_BIGINT,
        "collection_name": row.name,
        "media": [{"id": 1}],
        "total": 1,
        "page": 1,
        "page_size": 20,
    }
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_create_wire(client) -> None:
    row = _collection(id=SAMPLE_BIGINT, rules=RULES)
    # INSERT ... RETURNING the new row.
    _Db.results = [_Result([row])]
    resp = await client.post(
        "/api/v1/collections", json={"name": row.name, "rules": RULES}
    )
    assert_wire_unchanged(resp, _row_dict(row), status=201)
    assert resp.json()["id"] == SAMPLE_BIGINT


@pytest.mark.asyncio
async def test_update_wire(client) -> None:
    before = _collection(id=SAMPLE_BIGINT, rules=RULES, is_preset=False)
    after = _collection(id=SAMPLE_BIGINT, rules=RULES, name="Renamed")
    # get_collection_by_id (ownership) → UPDATE ... RETURNING.
    _Db.results = [_Result([before]), _Result([after])]
    resp = await client.put(
        f"/api/v1/collections/{COLLECTION_ID}", json={"name": "Renamed"}
    )
    assert_wire_unchanged(resp, _row_dict(after))
    assert resp.json()["id"] == SAMPLE_BIGINT
