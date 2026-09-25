"""Admin tags console (``/api/v1/admin/tags*``): wire parity after the eleven
routes gained response models, the typed 404s for writes that name no row,
and the admin gate on every route.

Each request goes over real HTTP through the real router, the real
``get_admin_auth`` dependency and the real ``AdminTagsRepository``; only the
database session is scripted. Rows are ORM instances with every column set
(``sample_orm``), turned into dicts by the repository's own ``_obj_dict``, so
the expected body is exactly what the handler built before the models existed.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import TagGroups, Tags
from app.schemas.admin_tags import (
    AdminTagGroupListItem,
    AdminTagGroupRow,
    AdminTagListItem,
    AdminTagRow,
)
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

pytestmark = pytest.mark.unit

repo_mod = importlib.import_module("app.repositories.admin.tags_repository")

ADMIN = "00000000-0000-0000-0000-0000000000ad"
BASE = "/api/v1/admin/tags"


def _group_obj(**over: Any) -> TagGroups:
    return sample_orm(TagGroups, **over)


def _tag_obj(**over: Any) -> Tags:
    return sample_orm(Tags, **{"type": "user", **over})


GROUP = _group_obj()
GROUP_ID = GROUP.id
TAG = _tag_obj()
TAG_ID = TAG.id
# A legacy row with every nullable column NULL: the models must accept it.
NULL_TAG = _tag_obj(
    id=TAG_ID + 1,
    type="time",
    slug=None,
    color=None,
    icon=None,
    user_id=None,
    created_at=None,
    name_zh=None,
    scope_id=None,
    group_id=None,
    sort_order=None,
)
NULL_GROUP = _group_obj(id=GROUP_ID + 1, sort_order=None, created_at=None)


def _tag(obj: Tags) -> dict[str, Any]:
    return repo_mod._obj_dict(obj, repo_mod._TAG_N2A)


def _group(obj: TagGroups) -> dict[str, Any]:
    return repo_mod._obj_dict(obj, repo_mod._GROUP_N2A)


# --------------------------------------------------------------------------- #
# Scripted session
# --------------------------------------------------------------------------- #


class _Result:
    def __init__(
        self, *, rows: List[Any] | None = None, tuples: List[Any] | None = None
    ):
        self._rows = rows or []
        self._tuples = tuples or []

    def scalars(self):
        rows = self._rows

        class _S:
            def all(self):
                return list(rows)

            def first(self):
                return rows[0] if rows else None

        return _S()

    def all(self):
        return list(self._tuples)

    def first(self):
        return self._tuples[0] if self._tuples else None


class _Db:
    role: str = "admin"
    results: List[Any] = []
    scalars: List[Any] = []
    statements: List[str] = []


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Session:
    async def execute(self, stmt, *a, **kw):
        sql = _sql(stmt)
        if "user_profiles.role" in sql:
            return _Result(tuples=[(_Db.role,)])
        _Db.statements.append(sql)
        if not _Db.results:
            return _Result()
        nxt = _Db.results.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def scalar(self, stmt, *a, **kw):
        _Db.statements.append(_sql(stmt))
        return _Db.scalars.pop(0) if _Db.scalars else None


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=ADMIN, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    _Db.role = "admin"
    _Db.results = []
    _Db.scalars = []
    _Db.statements = []

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)
    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    monkeypatch.setattr(repo_mod, "write_scope", _scope)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _writes() -> list[str]:
    return [
        s
        for s in _Db.statements
        if s.startswith(("UPDATE", "DELETE", "INSERT")) and " public.tag" in s
    ]


def _exists(*ids: int) -> _Result:
    """The answer to an existence check (``SELECT <table>.id WHERE id IN``)."""
    return _Result(rows=list(ids))


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


# --------------------------------------------------------------------------- #
# Models pinned to the table
# --------------------------------------------------------------------------- #


def test_row_models_declare_every_column() -> None:
    assert set(AdminTagRow.model_fields) == column_names(Tags)
    assert set(AdminTagGroupRow.model_fields) == column_names(TagGroups)
    assert set(AdminTagListItem.model_fields) == column_names(Tags) | {
        "group_name",
        "usage_count",
    }
    assert set(AdminTagGroupListItem.model_fields) == column_names(TagGroups) | {
        "tag_count"
    }


# --------------------------------------------------------------------------- #
# Tag groups
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_groups_wire_unchanged(client) -> None:
    _Db.results = [
        _Result(rows=[GROUP, NULL_GROUP]),
        _Result(tuples=[(GROUP_ID,), (None,), (GROUP_ID,)]),
    ]
    resp = await client.get(f"{BASE}/groups")
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "groups": [
                {**_group(GROUP), "tag_count": 2},
                {**_group(NULL_GROUP), "tag_count": 0},
            ],
            "total_tags": 3,
            "uncategorized_count": 1,
        },
    )
    assert resp.json()["groups"][0]["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_create_group_wire_unchanged(client) -> None:
    _Db.scalars = [4]
    _Db.results = [_Result(rows=[GROUP])]
    resp = await client.post(f"{BASE}/groups", json={"name": "Pipeline"})
    assert_wire_unchanged(resp, {"success": True, "group": _group(GROUP)})


@pytest.mark.asyncio
async def test_create_group_duplicate_name_is_409(client) -> None:
    _Db.scalars = [4]
    _Db.results = [IntegrityError("INSERT", {}, Exception("tag_groups_name_key"))]
    resp = await client.post(f"{BASE}/groups", json={"name": "Pipeline"})
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_update_group_wire_unchanged(client) -> None:
    _Db.results = [_Result(rows=[GROUP])]
    resp = await client.patch(f"{BASE}/groups/{GROUP_ID}", json={"name": "X"})
    assert_wire_unchanged(resp, {"success": True, "group": _group(GROUP)})


@pytest.mark.asyncio
@pytest.mark.parametrize("gid", [str(GROUP_ID), "abc"])
async def test_update_group_miss_is_typed_404(client, gid) -> None:
    _Db.results = [_Result()]
    resp = await client.patch(f"{BASE}/groups/{gid}", json={"name": "X"})
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_delete_group_wire_unchanged(client) -> None:
    _Db.results = [_Result(tuples=[(GROUP_ID,)])]
    resp = await client.delete(f"{BASE}/groups/{GROUP_ID}")
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("gid", [str(GROUP_ID), "abc"])
async def test_delete_group_miss_is_typed_404(client, gid) -> None:
    resp = await client.delete(f"{BASE}/groups/{gid}")
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_reorder_groups_wire_unchanged(client) -> None:
    _Db.results = [_exists(GROUP_ID, GROUP_ID + 1)]
    resp = await client.post(
        f"{BASE}/groups/reorder", json={"ids": [str(GROUP_ID + 1), str(GROUP_ID)]}
    )
    assert_wire_unchanged(resp, {"success": True})
    assert len(_writes()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["123", "abc"])
async def test_reorder_groups_unknown_id_is_404_and_writes_nothing(client, bad) -> None:
    _Db.results = [_exists(GROUP_ID)]
    resp = await client.post(
        f"{BASE}/groups/reorder", json={"ids": [str(GROUP_ID), bad]}
    )
    _assert_typed_404(resp)
    assert _writes() == []


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_tags_wire_unchanged(client) -> None:
    _Db.scalars = [7]
    _Db.results = [
        _Result(tuples=[(TAG, "Pipeline"), (NULL_TAG, None)]),
        _Result(tuples=[(TAG_ID,), (TAG_ID,)]),
    ]
    resp = await client.get(BASE)
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "items": [
                {**_tag(TAG), "group_name": "Pipeline", "usage_count": 2},
                {**_tag(NULL_TAG), "group_name": None, "usage_count": 0},
            ],
            "total": 7,
        },
    )
    item = resp.json()["items"][0]
    assert item["id"] == TAG_ID and item["group_id"] == TAG.group_id
    assert isinstance(item["user_id"], str)
    assert item["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_list_tags_bad_filter_and_sort_are_not_500(client) -> None:
    resp = await client.get(BASE, params={"group_id": "abc"})
    assert_wire_unchanged(resp, {"success": True, "items": [], "total": 0})
    _Db.scalars = [0]
    resp = await client.get(BASE, params={"sort_by": "metadata", "sort_order": "asc"})
    assert_wire_unchanged(resp, {"success": True, "items": [], "total": 0})


@pytest.mark.asyncio
async def test_create_tag_wire_unchanged(client) -> None:
    _Db.results = [_exists(GROUP_ID), _Result(rows=[TAG])]
    resp = await client.post(BASE, json={"name": "Keep", "group_id": str(GROUP_ID)})
    assert_wire_unchanged(resp, {"success": True, "tag": _tag(TAG)})


@pytest.mark.asyncio
@pytest.mark.parametrize("gid", ["123", "abc"])
async def test_create_tag_unknown_group_is_404_not_409(client, gid) -> None:
    """An unknown group used to surface as the FK violation, which the
    duplicate-name handler reported as ``409 Tag already exists``."""
    _Db.results = [_exists()]
    resp = await client.post(BASE, json={"name": "Keep", "group_id": gid})
    _assert_typed_404(resp)
    assert _writes() == []


@pytest.mark.asyncio
async def test_update_tag_wire_unchanged(client) -> None:
    _Db.results = [_Result(rows=[NULL_TAG])]
    resp = await client.patch(f"{BASE}/{NULL_TAG.id}", json={"name": "X"})
    assert_wire_unchanged(resp, {"success": True, "tag": _tag(NULL_TAG)})


@pytest.mark.asyncio
@pytest.mark.parametrize("tid", [str(TAG_ID), "abc"])
async def test_update_tag_miss_is_typed_404(client, tid) -> None:
    _Db.results = [_Result()]
    resp = await client.patch(f"{BASE}/{tid}", json={"name": "X"})
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_update_tag_unknown_group_is_404(client) -> None:
    _Db.results = [_exists()]
    resp = await client.patch(f"{BASE}/{TAG_ID}", json={"group_id": "123"})
    _assert_typed_404(resp)
    assert _writes() == []


@pytest.mark.asyncio
async def test_update_tag_duplicate_name_is_409(client) -> None:
    _Db.results = [IntegrityError("UPDATE", {}, Exception("unique_tag_per_scope"))]
    resp = await client.patch(f"{BASE}/{TAG_ID}", json={"name": "Taken"})
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_delete_tag_wire_unchanged(client) -> None:
    _Db.results = [_Result(), _Result(tuples=[(TAG_ID,)])]
    resp = await client.delete(f"{BASE}/{TAG_ID}")
    assert_wire_unchanged(resp, {"success": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("tid", [str(TAG_ID), "abc"])
async def test_delete_tag_miss_is_typed_404(client, tid) -> None:
    resp = await client.delete(f"{BASE}/{tid}")
    _assert_typed_404(resp)


# --------------------------------------------------------------------------- #
# Batch / reorder
# --------------------------------------------------------------------------- #

IDS = [str(TAG_ID), str(TAG_ID + 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,script,message,writes",
    [
        (
            {"action": "move", "tag_ids": IDS, "group_id": str(GROUP_ID)},
            [_exists(GROUP_ID), _exists(TAG_ID, TAG_ID + 1)],
            "Moved 2 tags",
            2,
        ),
        (
            {"action": "move", "tag_ids": IDS},
            [_exists(TAG_ID, TAG_ID + 1)],
            "Moved 2 tags",
            2,
        ),
        (
            {"action": "delete", "tag_ids": IDS},
            [_exists(TAG_ID, TAG_ID + 1)],
            "Deleted 2 tags",
            2,  # the resource_tags cascade is not counted by _writes()
        ),
        (
            {"action": "color", "tag_ids": IDS, "color": "#ff0000"},
            [_exists(TAG_ID, TAG_ID + 1)],
            "Updated color for 2 tags",
            2,
        ),
    ],
)
async def test_batch_wire_unchanged(client, body, script, message, writes) -> None:
    _Db.results = list(script)
    resp = await client.post(f"{BASE}/batch", json=body)
    assert_wire_unchanged(resp, {"success": True, "message": message})
    assert len(_writes()) == writes


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["move", "delete", "color"])
@pytest.mark.parametrize("bad", ["123", "abc"])
async def test_batch_unknown_tag_is_404_and_writes_nothing(client, action, bad) -> None:
    _Db.results = [_exists(TAG_ID)]
    body = {"action": action, "tag_ids": [str(TAG_ID), bad], "color": "#fff"}
    resp = await client.post(f"{BASE}/batch", json=body)
    _assert_typed_404(resp)
    assert _writes() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("gid", ["123", "abc"])
async def test_batch_move_to_unknown_group_is_404(client, gid) -> None:
    _Db.results = [_exists()]
    body = {"action": "move", "tag_ids": IDS, "group_id": gid}
    resp = await client.post(f"{BASE}/batch", json=body)
    _assert_typed_404(resp)
    assert _writes() == []


@pytest.mark.asyncio
async def test_batch_bad_requests_stay_400(client) -> None:
    resp = await client.post(f"{BASE}/batch", json={"action": "x", "tag_ids": IDS})
    assert resp.status_code == 400, resp.text
    resp = await client.post(f"{BASE}/batch", json={"action": "color", "tag_ids": IDS})
    assert resp.status_code == 400, resp.text
    assert _writes() == []


@pytest.mark.asyncio
async def test_reorder_tags_wire_unchanged(client) -> None:
    _Db.results = [_exists(TAG_ID, TAG_ID + 1)]
    resp = await client.post(f"{BASE}/reorder", json={"tag_ids": IDS})
    assert_wire_unchanged(resp, {"success": True})
    assert len(_writes()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["123", "abc"])
async def test_reorder_tags_unknown_id_is_404_and_writes_nothing(client, bad) -> None:
    _Db.results = [_exists(TAG_ID)]
    resp = await client.post(f"{BASE}/reorder", json={"tag_ids": [str(TAG_ID), bad]})
    _assert_typed_404(resp)
    assert _writes() == []


# --------------------------------------------------------------------------- #
# Admin gate on every route
# --------------------------------------------------------------------------- #

ROUTES = [
    ("GET", "/groups", "/groups", None),
    ("POST", "/groups", "/groups", {"name": "G"}),
    ("PATCH", "/groups/{group_id}", f"/groups/{GROUP_ID}", {"name": "G"}),
    ("DELETE", "/groups/{group_id}", f"/groups/{GROUP_ID}", None),
    ("POST", "/groups/reorder", "/groups/reorder", {"ids": [str(GROUP_ID)]}),
    ("GET", "", "", None),
    ("POST", "", "", {"name": "T"}),
    ("PATCH", "/{tag_id}", f"/{TAG_ID}", {"name": "T"}),
    ("DELETE", "/{tag_id}", f"/{TAG_ID}", None),
    ("POST", "/batch", "/batch", {"action": "delete", "tag_ids": [str(TAG_ID)]}),
    ("POST", "/reorder", "/reorder", {"tag_ids": [str(TAG_ID)]}),
]


def test_route_table_covers_the_router() -> None:
    router = importlib.import_module("app.api.admin.tags_router").router
    declared = {(m, r.path) for r in router.routes for m in r.methods}
    assert declared == {(m, t) for m, t, _, _ in ROUTES}


@pytest.mark.asyncio
@pytest.mark.parametrize("method,_template,path,body", ROUTES)
async def test_every_route_refuses_a_non_admin(
    client, method, _template, path, body
) -> None:
    _Db.role = "user"
    resp = await client.request(method, f"{BASE}{path}", json=body)
    assert resp.status_code == 403, resp.text
    assert _Db.statements == []


@pytest.mark.asyncio
async def test_the_same_request_passes_as_admin(client) -> None:
    """Control for the refusals above: the gate, not the request, refused."""
    _Db.results = [_Result(), _Result(tuples=[(TAG_ID,)])]
    resp = await client.delete(f"{BASE}/{TAG_ID}")
    assert resp.status_code == 200, resp.text
