"""Admin point adjustments and gifts must move the money they report.

Before this fix:

- ``POST /admin/credits/adjust`` with a negative amount (the console form says
  "Positive to add, negative to deduct") went to ``add_points``, which refuses
  ``amount <= 0`` by returning ``{"success": False}``; the route answered
  ``200 {"ok": true, "new_balance": null}`` and audited an adjustment that
  never happened.
- ``POST /admin/credits/batch-gift`` with ``amount <= 0`` counted every team as
  gifted while nothing was credited; an unknown team id died in the quota
  insert's foreign key.
- ``POST /points/admin/adjust`` debited by read → subtract → write-absolute in
  separate transactions and ledgered the requested amount even when the
  balance was clamped at zero.

HTTP cases go through the real routers and the real ``get_admin_auth``; the
session is scripted by statement, and the money primitives are recorded.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.repositories.points_repository import PointsRepository
from app.services.billing import admin_points
from app.services.billing.points_service import PointsService

pytestmark = pytest.mark.unit

credits_router = sys.modules["app.api.admin.credits_router"]
repo_mod = sys.modules["app.repositories.points_repository"]

ADMIN = "00000000-0000-0000-0000-000000000042"
TEAM = 7_300_000_000_000_000_009
OTHER_TEAM = 7_300_000_000_000_000_010
UNKNOWN_TEAM = 7_300_000_000_000_000_099


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class _Result:
    def __init__(self, first: Any = None, scalars: list[Any] | None = None):
        self._first = first
        self._scalars = scalars or []

    def first(self) -> Any:
        return self._first

    def scalars(self):
        values = self._scalars

        class _S:
            def all(self):
                return list(values)

        return _S()


class _Ledger:
    teams: set[int] = set()
    credits: list[dict[str, Any]] = []
    debits: list[dict[str, Any]] = []
    add_result: dict[str, Any] = {}
    debit_result: dict[str, Any] | None = None


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    async def _auth() -> AuthContext:
        return AuthContext(user_id=ADMIN, auth_type="jwt")

    app.dependency_overrides[get_auth] = _auth
    _Ledger.teams = {TEAM, OTHER_TEAM}
    _Ledger.credits = []
    _Ledger.debits = []
    _Ledger.add_result = {"success": True, "new_balance": 700}
    _Ledger.debit_result = {"previous_balance": 100, "new_balance": 0, "debited": 100}

    class _Session:
        async def execute(self, stmt, *a, **kw):
            sql = _sql(stmt)
            if "user_profiles" in sql:
                return _Result(first=("admin",))
            if "FROM public.teams" in sql:
                wanted = stmt.compile().params
                ids = {v for val in wanted.values() for v in val}
                return _Result(scalars=sorted(ids & _Ledger.teams))
            return _Result()

    @asynccontextmanager
    async def _scope():
        yield _Session()

    monkeypatch.setattr("app.db.session.read_scope", _scope)

    async def _add(self, team_id, amount, type, description, user_id=None, **kw):
        _Ledger.credits.append({"team_id": team_id, "amount": amount, "type": type})
        return dict(_Ledger.add_result)

    async def _debit(self, team_id, amount, *, user_id, type, description):
        _Ledger.debits.append({"team_id": team_id, "amount": amount, "type": type})
        return _Ledger.debit_result

    async def _audit(**kwargs):
        return None

    monkeypatch.setattr(PointsService, "add_points", _add)
    monkeypatch.setattr(PointsRepository, "debit_points_clamped", _debit)
    monkeypatch.setattr(credits_router, "create_audit_log", _audit)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --------------------------------------------------------------------------- #
# POST /admin/credits/adjust
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_credits_adjust_negative_amount_debits(client) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/adjust", json={"team_id": str(TEAM), "amount": -500}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "new_balance": 0}
    assert _Ledger.debits == [
        {"team_id": str(TEAM), "amount": 500, "type": "admin_adjust"}
    ]
    assert _Ledger.credits == []


@pytest.mark.asyncio
async def test_credits_adjust_positive_amount_credits(client) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/adjust", json={"team_id": str(TEAM), "amount": 300}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "new_balance": 700}
    assert _Ledger.credits == [
        {"team_id": str(TEAM), "amount": 300, "type": "admin_adjust"}
    ]


@pytest.mark.asyncio
async def test_credits_adjust_zero_is_400(client) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/adjust", json={"team_id": str(TEAM), "amount": 0}
    )
    assert resp.status_code == 400, resp.text
    assert _Ledger.credits == [] and _Ledger.debits == []


@pytest.mark.asyncio
@pytest.mark.parametrize("team_id", [str(UNKNOWN_TEAM), "not-a-team", "-5", ""])
async def test_credits_adjust_unknown_team_is_404(client, team_id) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/adjust", json={"team_id": team_id, "amount": 10}
    )
    assert resp.status_code == 404, resp.text
    assert _Ledger.credits == [] and _Ledger.debits == []


# --------------------------------------------------------------------------- #
# POST /points/admin/adjust (same service)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_points_admin_adjust_negative_goes_through_the_clamped_debit(
    client,
) -> None:
    resp = await client.post(
        "/api/v1/points/admin/adjust",
        json={"team_id": str(TEAM), "amount": -500, "description": "Correction"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["new_balance"] == 0
    assert _Ledger.debits == [
        {"team_id": str(TEAM), "amount": 500, "type": "admin_adjust"}
    ]


@pytest.mark.asyncio
async def test_points_admin_adjust_unknown_team_is_404(client) -> None:
    resp = await client.post(
        "/api/v1/points/admin/adjust",
        json={"team_id": "abc", "amount": 5, "description": "x"},
    )
    assert resp.status_code == 404, resp.text
    assert _Ledger.credits == []


# --------------------------------------------------------------------------- #
# POST /admin/credits/batch-gift
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, -10])
async def test_batch_gift_non_positive_amount_is_400(client, amount) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/batch-gift",
        json={"team_ids": [str(TEAM)], "amount": amount},
    )
    assert resp.status_code == 400, resp.text
    assert _Ledger.credits == []


@pytest.mark.asyncio
async def test_batch_gift_counts_only_teams_that_were_credited(client) -> None:
    resp = await client.post(
        "/api/v1/admin/credits/batch-gift",
        json={
            "team_ids": [str(TEAM), str(UNKNOWN_TEAM), "junk", str(TEAM)],
            "amount": 50,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "ok": True,
        "gifted_count": 1,
        "errors": [
            {"team_id": str(UNKNOWN_TEAM), "error": "Team not found"},
            {"team_id": "junk", "error": "Team not found"},
        ],
    }
    assert _Ledger.credits == [{"team_id": str(TEAM), "amount": 50, "type": "gift"}]


@pytest.mark.asyncio
async def test_batch_gift_refused_credit_is_an_error_not_a_gift(client) -> None:
    _Ledger.add_result = {"success": False, "new_balance": None}
    resp = await client.post(
        "/api/v1/admin/credits/batch-gift",
        json={"team_ids": [str(TEAM)], "amount": 50},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gifted_count"] == 0
    assert body["errors"] == [{"team_id": str(TEAM), "error": "Points were not added"}]


# --------------------------------------------------------------------------- #
# PointsRepository.debit_points_clamped — one transaction, ledger = actual
# --------------------------------------------------------------------------- #


class _DebitSession:
    def __init__(self, balance: int | None):
        self.balance = balance
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, stmt, *a, **kw):
        self.statements.append(_sql(stmt))
        self.params.append(stmt.compile().params)
        balance = self.balance

        class _R:
            def scalar_one_or_none(self):
                return balance

        return _R()


def _patch_write_scope(monkeypatch, session: _DebitSession) -> None:
    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(repo_mod, "write_scope", _scope)


@pytest.mark.asyncio
async def test_debit_clamps_and_ledgers_the_amount_actually_taken(
    monkeypatch,
) -> None:
    monkeypatch.undo()  # the real repository method, not the recorder above
    session = _DebitSession(balance=100)
    _patch_write_scope(monkeypatch, session)

    out = await PointsRepository().debit_points_clamped(
        str(TEAM), 500, user_id=ADMIN, type="admin_adjust", description="Fix"
    )

    assert out == {"previous_balance": 100, "new_balance": 0, "debited": 100}
    select_sql, update_sql, insert_sql = session.statements
    assert "FOR UPDATE" in select_sql
    assert update_sql.startswith("UPDATE public.team_quotas")
    assert session.params[1]["points_balance"] == 0
    assert insert_sql.startswith("INSERT INTO public.point_transactions")
    assert session.params[2]["amount"] == -100
    assert session.params[2]["balance_after"] == 0


@pytest.mark.asyncio
async def test_debit_of_an_empty_balance_writes_nothing(monkeypatch) -> None:
    monkeypatch.undo()
    session = _DebitSession(balance=0)
    _patch_write_scope(monkeypatch, session)

    out = await PointsRepository().debit_points_clamped(
        str(TEAM), 50, user_id=ADMIN, type="admin_adjust", description=None
    )

    assert out == {"previous_balance": 0, "new_balance": 0, "debited": 0}
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_debit_without_a_quota_row_returns_none(monkeypatch) -> None:
    monkeypatch.undo()
    session = _DebitSession(balance=None)
    _patch_write_scope(monkeypatch, session)

    out = await PointsRepository().debit_points_clamped(
        str(TEAM), 50, user_id=ADMIN, type="admin_adjust", description=None
    )
    assert out is None
    assert len(session.statements) == 1


def test_parse_team_id_rejects_non_ids() -> None:
    assert admin_points.parse_team_id(str(TEAM)) == TEAM
    for bad in ["", "abc", "-1", "0", "1.5", str(2**63)]:
        assert admin_points.parse_team_id(bad) is None
