"""Payment routes: wire parity after they gained response models (P9).

Each JSON route runs over real HTTP through the real service and repository
code; only the database session is scripted. The session hands back ORM
objects with every column set (``sample_orm``), so the dict the handler builds
is the one production builds, and the body must equal what FastAPI sent for
that dict with no model (``tests/api/wire_parity.py`` explains why).

The two provider callbacks are disabled (501 until signature verification is
implemented); their contract is pinned here too, so nobody reads a missing
200 as "untyped" and bolts on a JSON body the providers would never accept.

Team scope (403 for a foreign ``team_id``, 404 for a foreign order) is pinned
in ``test_payment_team_scope.py``.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import Orders, PointPackages
from app.schemas.payment import PaymentOrderRow, PaymentPackageRow
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

r = sys.modules["app.api.payment_router"]
pay_repo_mod = sys.modules["app.repositories.payment_repository"]
points_repo_mod = sys.modules["app.repositories.points_repository"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
TEAM_ID = "7300000000000000009"


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


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    _Db.results = []

    class _Session:
        async def execute(self, stmt):
            return _Db.results.pop(0)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    async def _member(team_id, user_id):
        return user_id == USER and str(team_id) == TEAM_ID

    for mod in (pay_repo_mod, points_repo_mod):
        monkeypatch.setattr(mod, "read_scope", _scope)
        monkeypatch.setattr(mod, "write_scope", _scope)
    monkeypatch.setattr(r, "_is_team_member", _member)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _order(**overrides: Any) -> Orders:
    # payment_method / payment_status are CHECK-constrained vocabularies.
    obj = sample_orm(
        Orders, team_id=int(TEAM_ID), payment_method="wechat", payment_status="paid"
    )
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


def _order_with_nulls() -> Orders:
    return _order(package_id=None, trade_no=None, payment_url=None, paid_at=None)


def _package(**overrides: Any) -> PointPackages:
    obj = sample_orm(PointPackages, is_active=True)
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


def test_models_declare_every_column() -> None:
    """A column the model does not declare would be dropped from the wire."""
    assert set(PaymentOrderRow.model_fields) == column_names(Orders)
    assert set(PaymentPackageRow.model_fields) == column_names(PointPackages)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "packages",
    [[], lambda: [_package(), _package(description=None)]],
    ids=["empty", "rows"],
)
async def test_packages_wire(client, packages) -> None:
    objs = packages() if callable(packages) else packages
    _Db.results = [_Result(objs)]
    raw = {"success": True, "data": [points_repo_mod._package_row(o) for o in objs]}
    resp = await client.get("/api/v1/payment/packages")
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_order, _order_with_nulls], ids=["full", "nulls"])
async def test_orders_wire(client, make) -> None:
    obj = make()
    _Db.results = [_Result([obj])]
    raw = {"success": True, "data": [pay_repo_mod._order_row(obj)]}
    resp = await client.get(f"/api/v1/payment/orders?team_id={TEAM_ID}")
    assert_wire_unchanged(resp, raw)
    # Snowflake ids stay JSON numbers.
    assert resp.json()["data"][0]["id"] == obj.id


@pytest.mark.asyncio
async def test_orders_empty_wire(client) -> None:
    _Db.results = [_Result([])]
    resp = await client.get(f"/api/v1/payment/orders?team_id={TEAM_ID}")
    assert_wire_unchanged(resp, {"success": True, "data": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_order, _order_with_nulls], ids=["full", "nulls"])
async def test_order_status_wire(client, make) -> None:
    obj = make()
    # The router's ownership lookup, then the service's own lookup.
    _Db.results = [_Result([obj]), _Result([obj])]
    row = pay_repo_mod._order_row(obj)
    raw = {
        "success": True,
        "data": {
            "order_id": row["id"],
            "payment_status": row["payment_status"],
            "points_amount": row["points_amount"],
            "paid_at": row["paid_at"],
        },
    }
    resp = await client.get(f"/api/v1/payment/order/{obj.id}/status")
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
async def test_create_order_wire(client) -> None:
    package = _package()
    created = _order_with_nulls()
    # get_package_by_id, then the INSERT ... RETURNING.
    _Db.results = [_Result([package]), _Result([created])]
    raw = {"success": True, "data": pay_repo_mod._order_row(created)}
    resp = await client.post(
        "/api/v1/payment/create-order",
        json={
            "package_id": str(package.id),
            "payment_method": "alipay",
            "team_id": TEAM_ID,
        },
    )
    assert_wire_unchanged(resp, raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["alipay", "wechat"])
async def test_callbacks_answer_501_as_declared(client, provider) -> None:
    resp = await client.post(f"/api/v1/payment/callback/{provider}", content=b"x=1")
    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body["success"] is False
    assert body["code"] == "http_501"

    op = app.openapi()["paths"][f"/api/v1/payment/callback/{provider}"]["post"]
    assert list(op["responses"]) == ["501"]
    schema = op["responses"]["501"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/ErrorResponse"}
