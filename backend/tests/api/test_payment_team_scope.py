"""Team scope on ``/payment/*``.

``payment_router._resolve_team_id`` used an explicit ``team_id`` as-is, so any
signed-in user could list another team's orders or open an order on its
behalf by passing that team's id. ``/order/{id}/status`` looked the order up
by id alone, and order ids are Snowflakes (near-sequential), so a caller could
walk other teams' payment status and amounts. Same hole as ``/points/*``
(see ``test_points_wire.py``); same guard: the id must be numeric and the
caller a member, else 403. A foreign order answers 404 so its existence is
not confirmed.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app
from app.models import Orders
from tests.api.wire_parity import sample_orm

r = sys.modules["app.api.payment_router"]

pytestmark = pytest.mark.unit

USER = "00000000-0000-0000-0000-000000000042"
OWN_TEAM = "7300000000000000009"
FOREIGN_TEAM = "7300000000000000777"
ORDER_ID = "7300000000000001234"


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=USER, auth_type="jwt")


class _Svc:
    calls: List[str] = []
    order_team: str = OWN_TEAM

    async def get_team_orders(self, team_id: str, limit: int, offset: int):
        self.calls.append(f"orders:{team_id}")
        return []

    async def create_order(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(f"create:{kwargs['team_id']}")
        # A real order row: the route now declares ``PaymentOrderRow``.
        repo_mod = sys.modules["app.repositories.payment_repository"]
        return {
            "success": True,
            "data": repo_mod._order_row(
                sample_orm(Orders, payment_method="wechat", payment_status="pending")
            ),
        }

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        self.calls.append(f"status:{order_id}")
        return {
            "success": True,
            "data": {
                "order_id": int(order_id),
                "payment_status": "pending",
                "points_amount": 100,
                "paid_at": None,
            },
        }


class _Repo:
    async def get_order_by_id(self, order_id: str):
        return {"id": int(order_id), "team_id": int(_Svc.order_team)}


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    app.dependency_overrides[get_auth] = _fake_auth
    _Svc.calls = []
    _Svc.order_team = OWN_TEAM
    svc = _Svc()
    svc.payment_repo = _Repo()

    async def _member(team_id, user_id):
        return user_id == USER and str(team_id) == OWN_TEAM

    monkeypatch.setattr(r, "_payment_service", svc)
    monkeypatch.setattr(r, "_is_team_member", _member)
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
@pytest.mark.parametrize("team_id", [FOREIGN_TEAM, "not-a-team"])
async def test_orders_for_a_foreign_team_is_403(client, team_id) -> None:
    resp = await client.get(f"/api/v1/payment/orders?team_id={team_id}")
    assert resp.status_code == 403, resp.text
    assert _Svc.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("team_id", [FOREIGN_TEAM, "not-a-team"])
async def test_create_order_for_a_foreign_team_is_403(client, team_id) -> None:
    resp = await client.post(
        "/api/v1/payment/create-order",
        json={"package_id": "p1", "payment_method": "wechat", "team_id": team_id},
    )
    assert resp.status_code == 403, resp.text
    assert _Svc.calls == []


@pytest.mark.asyncio
async def test_own_team_still_works(client) -> None:
    resp = await client.get(f"/api/v1/payment/orders?team_id={OWN_TEAM}")
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        "/api/v1/payment/create-order",
        json={"package_id": "p1", "payment_method": "wechat", "team_id": OWN_TEAM},
    )
    assert resp.status_code == 200, resp.text
    assert _Svc.calls == [f"orders:{OWN_TEAM}", f"create:{OWN_TEAM}"]


@pytest.mark.asyncio
async def test_order_status_of_a_foreign_team_is_404(client) -> None:
    _Svc.order_team = FOREIGN_TEAM
    resp = await client.get(f"/api/v1/payment/order/{ORDER_ID}/status")
    assert resp.status_code == 404, resp.text
    assert _Svc.calls == []


@pytest.mark.asyncio
async def test_order_status_of_own_team(client) -> None:
    resp = await client.get(f"/api/v1/payment/order/{ORDER_ID}/status")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["payment_status"] == "pending"
