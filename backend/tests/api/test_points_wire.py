"""Points routes: wire parity after they gained response models (P4).

Each route runs over real HTTP through the real service and repository code;
only the database session is scripted. The session hands back ORM objects
with every column set (``sample_orm``), so the dict the handler builds is the
one production builds, and the body must equal what FastAPI sent for that
dict with no model (``tests/api/wire_parity.py`` explains why).

Also pinned here: an explicit ``team_id`` must name a team the caller belongs
to. Before, ``_resolve_team_id`` returned it unchecked, so any signed-in user
could read another team's balance, ledger and usage by passing its id.
"""

from __future__ import annotations

import re
import sys
from contextlib import asynccontextmanager
from typing import Any, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import MemberQuotas, PointPricing, PointTransactions, TeamQuotas
from app.schemas.points import (
    PointsPricingRow,
    PointsTransactionRow,
    PointsTransactionType,
)
from tests.api.wire_parity import assert_wire_unchanged, column_names, sample_orm

r = sys.modules["app.api.points_router"]
repo_mod = sys.modules["app.repositories.points_repository"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
TEAM_ID = "7300000000000000009"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _Result:
    def __init__(self, objs: List[Any] | None = None, rows: List[Any] | None = None):
        self._objs = objs or []
        self._rows = rows or []

    def scalars(self):
        objs = self._objs

        class _S:
            def all(self):
                return list(objs)

            def first(self):
                return objs[0] if objs else None

        return _S()

    def all(self):
        return list(self._rows)


class _Db:
    """Scripted session: each ``execute`` pops the next result."""

    results: List[_Result] = []
    member_of: set = set()


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    _Db.results = []
    _Db.member_of = {TEAM_ID}

    class _Session:
        async def execute(self, stmt):
            return _Db.results.pop(0)

    @asynccontextmanager
    async def _scope():
        yield _Session()

    async def _member(team_id, user_id):
        return user_id == USER and team_id in _Db.member_of

    monkeypatch.setattr(repo_mod, "read_scope", _scope)
    monkeypatch.setattr(r, "_is_team_member", _member)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _txn(**overrides: Any) -> PointTransactions:
    # Set after construction: the ``model`` column would collide with
    # ``sample_orm``'s own first parameter.
    obj = sample_orm(PointTransactions, type="consume")
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


def _txn_with_nulls() -> PointTransactions:
    return _txn(
        user_id=None,
        reference_type=None,
        reference_id=None,
        description=None,
        provider=None,
        model=None,
        duration_seconds=None,
        is_nous=None,
    )


# --------------------------------------------------------------------------- #
# Model ↔ source pins
# --------------------------------------------------------------------------- #


def test_transaction_row_declares_every_orm_column() -> None:
    assert set(PointsTransactionRow.model_fields) == column_names(PointTransactions)


def test_pricing_row_declares_every_orm_column() -> None:
    assert set(PointsPricingRow.model_fields) == column_names(PointPricing)


def test_transaction_type_matches_the_check_constraint() -> None:
    (check,) = [
        c
        for c in PointTransactions.__table__.constraints
        if c.name == "point_transactions_type_check"
    ]
    allowed = re.findall(r"'([a-z_]+)'", str(check.sqltext))
    assert set(PointsTransactionType.__args__) == set(allowed)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_balance_wire_unchanged(client) -> None:
    _Db.results = [_Result([sample_orm(TeamQuotas, storage_used_bytes=1234567)])]
    resp = await client.get(f"/api/v1/points/balance?team_id={TEAM_ID}")
    _Db.results = [_Result([sample_orm(TeamQuotas, storage_used_bytes=1234567)])]
    raw = await r.PointsService().get_balance(TEAM_ID)
    assert_wire_unchanged(resp, {"success": True, "data": raw})
    data = resp.json()["data"]
    assert data["team_id"] == TEAM_ID
    assert isinstance(data["storage_used_percent"], float)


@pytest.mark.asyncio
async def test_balance_without_quota_row_is_zeros(client) -> None:
    _Db.results = [_Result([])]
    resp = await client.get(f"/api/v1/points/balance?team_id={TEAM_ID}")
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "data": {
                "team_id": TEAM_ID,
                "points_balance": 0,
                "storage_limit_bytes": 0,
                "storage_used_bytes": 0,
                "storage_used_percent": 0.0,
            },
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [False, True])
async def test_transactions_wire_unchanged(client, nulls) -> None:
    objs = [_txn_with_nulls() if nulls else _txn(), _txn(type="daily_gift")]
    _Db.results = [_Result(objs)]
    resp = await client.get(f"/api/v1/points/transactions?team_id={TEAM_ID}")
    rows = [repo_mod._txn_row(o) for o in objs]
    assert_wire_unchanged(
        resp, {"success": True, "count": len(rows), "transactions": rows}
    )
    first = resp.json()["transactions"][0]
    assert isinstance(first["id"], int) and first["id"] > 2**53
    assert isinstance(first["team_id"], int)
    assert first["created_at"].endswith("+00:00")
    if not nulls:
        # The one Numeric column goes out as a string (strategy-C parity).
        assert isinstance(first["duration_seconds"], str)


@pytest.mark.asyncio
async def test_transactions_empty(client) -> None:
    _Db.results = [_Result([])]
    resp = await client.get(f"/api/v1/points/transactions?team_id={TEAM_ID}")
    assert_wire_unchanged(resp, {"success": True, "count": 0, "transactions": []})


@pytest.mark.asyncio
async def test_pricing_wire_unchanged(client) -> None:
    objs = [sample_orm(PointPricing), sample_orm(PointPricing, description=None)]
    _Db.results = [_Result(objs)]
    resp = await client.get("/api/v1/points/pricing")
    rows = [repo_mod._pricing_row(o) for o in objs]
    assert_wire_unchanged(resp, {"success": True, "pricing": rows})
    assert isinstance(resp.json()["pricing"][0]["id"], str)


@pytest.mark.asyncio
async def test_usage_stats_wire_unchanged(client) -> None:
    rows = [(-30, "consume"), (500, "purchase"), (20, "daily_gift"), (None, None)]
    _Db.results = [_Result(rows=rows)]
    resp = await client.get(f"/api/v1/points/usage-stats?team_id={TEAM_ID}")
    _Db.results = [_Result(rows=rows)]
    raw = await repo_mod.PointsRepository().get_usage_stats(TEAM_ID)
    assert_wire_unchanged(resp, {"success": True, "data": raw})
    assert resp.json()["data"] == {
        "total_consumed": 30,
        "total_purchased": 500,
        "by_type": {"consume": -30, "purchase": 500, "daily_gift": 20, "unknown": 0},
    }


@pytest.mark.asyncio
async def test_check_wire_unchanged_when_allowed(client) -> None:
    def _script():
        return [
            _Result([sample_orm(PointPricing, points_cost=7)]),
            _Result([sample_orm(TeamQuotas, points_balance=1000)]),
            _Result(
                [
                    sample_orm(
                        MemberQuotas,
                        monthly_points_limit=None,
                        points_used_this_month=0,
                    )
                ]
            ),
        ]

    _Db.results = _script()
    resp = await client.get(
        f"/api/v1/points/check?action_type=ai_summary&count=2&team_id={TEAM_ID}"
    )
    _Db.results = _script()
    raw = await r.PointsService().check_quota(
        team_id=TEAM_ID, user_id=USER, action_type="ai_summary", count=2
    )
    assert_wire_unchanged(resp, {"success": True, "data": raw})
    assert resp.json()["data"] == {
        "allowed": True,
        "points_cost": 14,
        "current_balance": 1000,
        "reason": None,
    }


@pytest.mark.asyncio
async def test_check_free_action_has_null_balance(client) -> None:
    _Db.results = [_Result([])]  # no pricing row
    resp = await client.get(
        f"/api/v1/points/check?action_type=unpriced&team_id={TEAM_ID}"
    )
    assert_wire_unchanged(
        resp,
        {
            "success": True,
            "data": {
                "allowed": True,
                "points_cost": 0,
                "current_balance": None,
                "reason": None,
            },
        },
    )


@pytest.mark.asyncio
async def test_check_denial_is_still_402(client) -> None:
    _Db.results = [
        _Result([sample_orm(PointPricing, points_cost=50)]),
        _Result([sample_orm(TeamQuotas, points_balance=10)]),
    ]
    resp = await client.get(
        f"/api/v1/points/check?action_type=ai_summary&team_id={TEAM_ID}"
    )
    assert resp.status_code == 402
    assert "Insufficient points balance" in resp.json()["error"]


# --------------------------------------------------------------------------- #
# Team scope
# --------------------------------------------------------------------------- #

_TEAM_ROUTES = [
    "/api/v1/points/balance",
    "/api/v1/points/transactions",
    "/api/v1/points/usage-stats",
    "/api/v1/points/check?action_type=ai_summary",
]


def _with_team(path: str, team_id: str) -> str:
    return f"{path}{'&' if '?' in path else '?'}team_id={team_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _TEAM_ROUTES)
@pytest.mark.parametrize("team_id", ["7300000000000000777", "not-a-team"])
async def test_foreign_team_id_is_403(client, path, team_id) -> None:
    resp = await client.get(_with_team(path, team_id))
    # No results are scripted: a query on the foreign team's behalf would
    # pop from an empty list and surface as a 500, not a 403.
    assert resp.status_code == 403, resp.text
