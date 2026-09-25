"""Admin credits routes (``/admin/credits/*``): refusals, and wire parity after
they gained response models (P8).

Every case runs over real HTTP through the real router, the real
``AdminCreditsRepository`` and the real ``get_admin_auth``; only the database
sessions are scripted. The repository's session hands back ORM objects with
every column set (``sample_orm``), so the dict each handler builds is the one
production builds.
"""

from __future__ import annotations

import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import Orders, PointPackages, PointPricing
from app.schemas.admin_credits import AdminPointPackage, AdminPointPricing
from app.services.billing.points_service import PointsService
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

pytestmark = pytest.mark.unit

credits_router = sys.modules["app.api.admin.credits_router"]
repo_mod = sys.modules["app.repositories.admin.credits_repository"]

ADMIN = "00000000-0000-0000-0000-000000000042"
ORDER_ID = "7300000000000000123"
PACKAGE_ID = str(uuid.UUID(int=7))


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Result:
    def __init__(self, objs: list[Any] | None = None, row: Any = None):
        self._objs = objs or []
        self._row = row

    def scalars(self):
        objs = self._objs

        class _S:
            def all(self):
                return list(objs)

            def first(self):
                return objs[0] if objs else None

        return _S()

    def first(self):
        return self._row

    def mappings(self):
        row = self._row

        class _M:
            def first(self):
                return row

        return _M()


class _Db:
    """The repository's scripted session: each ``execute`` pops the next
    result, or raises it when it is an exception."""

    results: list[Any] = []
    statements: list[str] = []
    audits: list[dict[str, Any]] = []


def _next(stmt: Any) -> Any:
    _Db.statements.append(_sql(stmt) if hasattr(stmt, "compile") else str(stmt))
    out = _Db.results.pop(0) if _Db.results else _Result()
    if isinstance(out, BaseException):
        raise out
    return out


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=ADMIN, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    _Db.results = []
    _Db.statements = []
    _Db.audits = []

    class _AdminSession:
        async def execute(self, *a, **kw):
            return _Result(row=("admin",))

    @asynccontextmanager
    async def _admin_scope():
        yield _AdminSession()

    class _RepoSession:
        async def execute(self, stmt, *a, **kw):
            return _next(stmt)

    @asynccontextmanager
    async def _repo_scope():
        yield _RepoSession()

    async def _audit(**kwargs):
        _Db.audits.append(kwargs)

    monkeypatch.setattr("app.db.session.read_scope", _admin_scope)
    monkeypatch.setattr(repo_mod, "read_scope", _repo_scope)
    monkeypatch.setattr(repo_mod, "write_scope", _repo_scope)
    monkeypatch.setattr(credits_router, "create_audit_log", _audit)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _package() -> PointPackages:
    return sample_orm(PointPackages)


def _pricing(**overrides: Any) -> PointPricing:
    return sample_orm(PointPricing, **overrides)


def _unique_violation() -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception("duplicate key value"))


def _assert_typed_404(resp) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["details"]["code"] == "not_found_or_out_of_scope"


_PACKAGE_BODY = {
    "name": "Starter",
    "description": "Entry pack",
    "points_amount": 1000,
    "price_cents": 990,
    "sort_order": 1,
    "is_active": True,
}


# --------------------------------------------------------------------------- #
# Refusals that used to be 500s, silent successes, or money holes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["confirm", "refund"])
async def test_non_numeric_order_id_is_404_not_500(client, action) -> None:
    resp = await client.post(f"/api/v1/admin/credits/orders/abc/{action}")
    assert resp.status_code == 404, resp.text
    assert _Db.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["put", "delete"])
async def test_non_uuid_package_id_is_typed_404(client, method) -> None:
    kwargs = {"json": _PACKAGE_BODY} if method == "put" else {}
    resp = await getattr(client, method)(
        "/api/v1/admin/credits/packages/not-a-uuid", **kwargs
    )
    _assert_typed_404(resp)
    assert _Db.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("points_amount", 0), ("points_amount", -5), ("price_cents", 0)]
)
async def test_unbuyable_package_is_400(client, field, value) -> None:
    body = {**_PACKAGE_BODY, field: value}
    resp = await client.post("/api/v1/admin/credits/packages", json=body)
    assert resp.status_code == 400, resp.text
    resp = await client.put(f"/api/v1/admin/credits/packages/{PACKAGE_ID}", json=body)
    assert resp.status_code == 400, resp.text
    assert _Db.statements == []


@pytest.mark.asyncio
async def test_duplicate_package_name_is_409(client) -> None:
    _Db.results = [_unique_violation()]
    resp = await client.post("/api/v1/admin/credits/packages", json=_PACKAGE_BODY)
    assert resp.status_code == 409, resp.text
    _Db.results = [_unique_violation()]
    resp = await client.put(
        f"/api/v1/admin/credits/packages/{PACKAGE_ID}", json=_PACKAGE_BODY
    )
    assert resp.status_code == 409, resp.text
    assert _Db.audits == []


@pytest.mark.asyncio
async def test_update_missing_package_is_typed_404(client) -> None:
    _Db.results = [_Result()]
    resp = await client.put(
        f"/api/v1/admin/credits/packages/{PACKAGE_ID}", json=_PACKAGE_BODY
    )
    _assert_typed_404(resp)
    assert _Db.audits == []


@pytest.mark.asyncio
async def test_delete_missing_package_is_typed_404_not_ok(client) -> None:
    _Db.results = [_Result()]
    resp = await client.delete(f"/api/v1/admin/credits/packages/{PACKAGE_ID}")
    _assert_typed_404(resp)
    assert _Db.audits == []


@pytest.mark.asyncio
async def test_delete_package_with_orders_is_409(client) -> None:
    _Db.results = [_unique_violation()]
    resp = await client.delete(f"/api/v1/admin/credits/packages/{PACKAGE_ID}")
    assert resp.status_code == 409, resp.text
    assert "deactivate" in resp.json()["error"]


@pytest.mark.asyncio
async def test_negative_points_cost_is_400(client) -> None:
    resp = await client.put(
        "/api/v1/admin/credits/pricing/video_parse", json={"points_cost": -1}
    )
    assert resp.status_code == 400, resp.text
    assert _Db.statements == []


@pytest.mark.asyncio
async def test_update_missing_pricing_is_typed_404(client) -> None:
    _Db.results = [_Result()]
    resp = await client.put(
        "/api/v1/admin/credits/pricing/nope", json={"points_cost": 3}
    )
    _assert_typed_404(resp)
    assert _Db.audits == []


# --------------------------------------------------------------------------- #
# Wire parity: body == jsonable_encoder(the dict the handler returned before)
# --------------------------------------------------------------------------- #


def test_row_models_declare_every_column() -> None:
    assert set(AdminPointPackage.model_fields) == column_names(PointPackages)
    assert set(AdminPointPricing.model_fields) == column_names(PointPricing)


def _order(**overrides: Any) -> Orders:
    return sample_orm(Orders, **overrides)


@pytest.mark.asyncio
async def test_confirm_wire_unchanged(client) -> None:
    _Db.results = [
        _Result(objs=[_order(payment_status="pending")]),
        _Result(row={"success": True, "already_credited": False, "reason": None}),
    ]
    resp = await client.post(f"/api/v1/admin/credits/orders/{ORDER_ID}/confirm")
    assert_wire_unchanged(
        resp, {"ok": True, "message": "Order confirmed and points added"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "already,message",
    [
        (False, "Order refunded and points deducted"),
        (True, "Order was already refunded"),
    ],
)
async def test_refund_wire_unchanged(client, already, message) -> None:
    _Db.results = [
        _Result(objs=[_order(payment_status="paid")]),
        _Result(
            row={"success": True, "already_refunded": already, "points_debited": 5}
        ),
    ]
    resp = await client.post(f"/api/v1/admin/credits/orders/{ORDER_ID}/refund")
    assert_wire_unchanged(resp, {"ok": True, "message": message})


@pytest.mark.asyncio
async def test_list_packages_wire_unchanged(client) -> None:
    rows = [_package(), sample_orm(PointPackages, description=None)]
    expected = [repo_mod._package_row(r) for r in rows]
    _Db.results = [_Result(objs=rows)]
    resp = await client.get("/api/v1/admin/credits/packages")
    assert_wire_unchanged(resp, expected)
    assert resp.json()[0]["created_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_create_package_wire_unchanged(client) -> None:
    row = _package()
    expected = repo_mod._package_row(row)
    _Db.results = [_Result(objs=[row])]
    resp = await client.post("/api/v1/admin/credits/packages", json=_PACKAGE_BODY)
    assert_wire_unchanged(resp, expected)
    assert _Db.audits[0]["target_id"] == expected["id"]


@pytest.mark.asyncio
async def test_update_package_wire_unchanged(client) -> None:
    row = sample_orm(PointPackages, description=None)
    expected = repo_mod._package_row(row)
    _Db.results = [_Result(objs=[row])]
    resp = await client.put(
        f"/api/v1/admin/credits/packages/{PACKAGE_ID}", json=_PACKAGE_BODY
    )
    assert_wire_unchanged(resp, expected)


@pytest.mark.asyncio
async def test_delete_package_wire_unchanged(client) -> None:
    _Db.results = [_Result(objs=[uuid.UUID(PACKAGE_ID)])]
    resp = await client.delete(f"/api/v1/admin/credits/packages/{PACKAGE_ID}")
    assert_wire_unchanged(resp, {"ok": True})
    assert len(_Db.audits) == 1


@pytest.mark.asyncio
async def test_list_pricing_wire_unchanged(client) -> None:
    rows = [_pricing(), _pricing(description=None, action_type="transcribe")]
    expected = [repo_mod._pricing_row(r) for r in rows]
    _Db.results = [_Result(objs=rows)]
    resp = await client.get("/api/v1/admin/credits/pricing")
    assert_wire_unchanged(resp, expected)


@pytest.mark.asyncio
async def test_update_pricing_wire_unchanged(client) -> None:
    row = _pricing(points_cost=0)
    expected = repo_mod._pricing_row(row)
    _Db.results = [_Result(objs=[row])]
    resp = await client.put(
        "/api/v1/admin/credits/pricing/video_parse",
        json={"points_cost": 0, "description": "Free"},
    )
    assert_wire_unchanged(resp, expected)


@pytest.fixture
def money(monkeypatch):
    """Known teams for the batch-gift lookup; recorded money primitives."""
    known = {int(ORDER_ID)}

    class _AdminSession:
        async def execute(self, stmt, *a, **kw):
            if "public.teams" in _sql(stmt):
                ids = {v for val in stmt.compile().params.values() for v in val}
                return _Result(objs=sorted(ids & known))
            return _Result(row=("admin",))

    @asynccontextmanager
    async def _admin_scope():
        yield _AdminSession()

    async def _add(self, team_id, amount, type, description, user_id=None, **kw):
        return {"success": True, "new_balance": 900}

    async def _require(raw):
        return str(raw)

    async def _adjust(team_id, amount, description, user_id):
        return {"success": True, "new_balance": 42, "applied": amount}

    points_router = sys.modules["app.api.points_router"]
    monkeypatch.setattr("app.db.session.read_scope", _admin_scope)
    monkeypatch.setattr(PointsService, "add_points", _add)
    for module in (credits_router, points_router):
        monkeypatch.setattr(module, "require_team", _require)
        monkeypatch.setattr(module, "admin_adjust_team_points", _adjust)
    return known


@pytest.mark.asyncio
async def test_batch_gift_wire_unchanged(client, money) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/batch-gift",
        json={"team_ids": [ORDER_ID, "404"], "amount": 10, "description": "Promo"},
    )
    assert_wire_unchanged(
        resp,
        {
            "ok": True,
            "gifted_count": 1,
            "errors": [{"team_id": "404", "error": "Team not found"}],
        },
    )


@pytest.mark.asyncio
async def test_batch_gift_all_gifted_wire_unchanged(client, money) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/batch-gift",
        json={"team_ids": [ORDER_ID], "amount": 10},
    )
    assert_wire_unchanged(resp, {"ok": True, "gifted_count": 1, "errors": []})


@pytest.mark.asyncio
async def test_adjust_wire_unchanged(client, money) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/adjust", json={"team_id": ORDER_ID, "amount": -5}
    )
    assert_wire_unchanged(resp, {"ok": True, "new_balance": 42})


@pytest.mark.asyncio
async def test_points_admin_adjust_wire_unchanged(client, money) -> None:
    resp = await client.post(
        "/api/v1/points/admin/adjust",
        json={"team_id": ORDER_ID, "amount": 5, "description": "Top-up"},
    )
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "message": f"Adjusted 5 points for team {ORDER_ID}",
            "new_balance": 42,
        },
    )
