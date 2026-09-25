"""Tag group writes: wire parity after they gained response models (P6),
and the admin gate they were missing.

``tag_groups`` is one platform-wide table with no owner column (mig 101:
"writable by admin only"). Until P6 any signed-in user could reorder, rename,
create or delete the groups every user sees, and deleting one also ran
``UPDATE tags SET group_id = NULL`` over every user's tags in it. The four
writes now require the platform admin role, like the admin panel's own
``/admin/tags/groups`` routes.

Each route runs over real HTTP through the real router and the real
``get_admin_auth`` dependency; only the database session is scripted. Group
ids come from the ``TagGroups`` mapper (``sample_row``) so they are real
Snowflake BIGINTs above 2**53.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import TagGroups
from app.schemas.tags import TagGroupMutationResult
from tests.api.wire_parity import assert_wire_unchanged, sample_row

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
GROUP_A = sample_row(TagGroups)["id"]
GROUP_B = GROUP_A + 1


class _Result:
    def __init__(self, value: Any = None, rows: List[Any] | None = None):
        self._value = value
        self._rows = rows or []

    def first(self):
        return self._value

    def scalars(self):
        rows = self._rows

        class _S:
            def all(self):
                return list(rows)

        return _S()


class _Db:
    results: List[_Result] = []
    statements: List[Any] = []


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=USER, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    _Db.results = []
    _Db.statements = []

    class _Session:
        async def execute(self, stmt, *a, **kw):
            _Db.statements.append(stmt)
            return _Db.results.pop(0) if _Db.results else _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)
    monkeypatch.setattr("app.db.session.write_scope", _scope)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _as_admin(*then: _Result) -> None:
    _Db.results = [_Result(("admin",)), *then]


def _as_user(*then: _Result) -> None:
    _Db.results = [_Result(("user",)), *then]


def _writes() -> list[str]:
    """Writes to ``tags`` / ``tag_groups`` (the request-log middleware also
    INSERTs through the same session; that is not the route's write)."""
    return [
        s
        for s in map(_sql, _Db.statements)
        if s.startswith(("UPDATE", "DELETE", "INSERT")) and " public.tag" in s
    ]


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


def test_model_declares_exactly_the_key_the_handlers_build() -> None:
    assert set(TagGroupMutationResult.model_fields) == {"success"}


# --------------------------------------------------------------------------- #
# PUT /tags/groups/reorder
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reorder_wire_unchanged(client) -> None:
    _as_admin(_Result(rows=[GROUP_A, GROUP_B]))
    resp = await client.put(
        "/api/v1/tags/groups/reorder",
        json={"group_ids": [str(GROUP_B), str(GROUP_A)]},
    )
    assert_wire_unchanged(resp, {"success": True})
    updates = [s for s in _writes() if s.startswith("UPDATE public.tag_groups")]
    assert len(updates) == 2


@pytest.mark.asyncio
async def test_reorder_refused_for_a_non_admin(client) -> None:
    _as_user(_Result(rows=[GROUP_A]))
    resp = await client.put(
        "/api/v1/tags/groups/reorder", json={"group_ids": [str(GROUP_A)]}
    )
    assert resp.status_code == 403, resp.text
    assert _writes() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["not-a-number", "unknown"])
async def test_reorder_with_an_unknown_group_is_404_and_writes_nothing(
    client, bad
) -> None:
    # "unknown": numeric but not in the table (only GROUP_A comes back).
    ids = [str(GROUP_A), "123" if bad == "unknown" else bad]
    _as_admin(_Result(rows=[GROUP_A]))
    resp = await client.put("/api/v1/tags/groups/reorder", json={"group_ids": ids})
    _assert_typed_404(resp)
    assert _writes() == []


# --------------------------------------------------------------------------- #
# DELETE /tags/groups/{group_id}
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_delete_wire_unchanged(client) -> None:
    _as_admin(_Result(), _Result((GROUP_A,)))
    resp = await client.delete(f"/api/v1/tags/groups/{GROUP_A}")
    assert_wire_unchanged(resp, {"success": True})
    ungroup, delete = _writes()
    assert ungroup.startswith("UPDATE public.tags SET group_id=")
    assert delete.startswith("DELETE FROM public.tag_groups")


@pytest.mark.asyncio
async def test_delete_refused_for_a_non_admin(client) -> None:
    """The un-grouping touches every user's tags: a regular user used to be
    able to strip the group off all of them."""
    _as_user(_Result(), _Result((GROUP_A,)))
    resp = await client.delete(f"/api/v1/tags/groups/{GROUP_A}")
    assert resp.status_code == 403, resp.text
    assert _writes() == []


@pytest.mark.asyncio
async def test_delete_unknown_group_is_404(client) -> None:
    # The DELETE ... RETURNING matched nothing: the transaction raises, so the
    # un-grouping UPDATE before it rolls back with it.
    _as_admin(_Result(), _Result(None))
    resp = await client.delete(f"/api/v1/tags/groups/{GROUP_A}")
    _assert_typed_404(resp)


@pytest.mark.asyncio
async def test_delete_non_numeric_id_is_404_not_500(client) -> None:
    _as_admin()
    resp = await client.delete("/api/v1/tags/groups/abc")
    _assert_typed_404(resp)
    assert _writes() == []


# --------------------------------------------------------------------------- #
# Create / rename share the table and the gate
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_and_rename_refused_for_a_non_admin(client) -> None:
    _as_user()
    resp = await client.post("/api/v1/tags/groups", json={"name": "Mine"})
    assert resp.status_code == 403, resp.text
    _as_user()
    resp = await client.put(f"/api/v1/tags/groups/{GROUP_A}", json={"name": "Mine"})
    assert resp.status_code == 403, resp.text
    assert _writes() == []


@pytest.mark.asyncio
async def test_listing_groups_stays_open_to_every_user(client) -> None:
    """Reading is not gated: every user's tag picker groups by these."""
    _Db.results = [_Result()]

    class _Mappings:
        def all(self):
            return [{"id": GROUP_A, "name": "Pipeline", "sort_order": 9}]

    _Db.results[0].mappings = lambda: _Mappings()  # type: ignore[attr-defined]
    resp = await client.get("/api/v1/tags/groups")
    assert resp.status_code == 200, resp.text
    assert resp.json()["groups"][0]["id"] == str(GROUP_A)
