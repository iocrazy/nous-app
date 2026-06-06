"""Unit tests for the two BUG 7 callers of the atomic confirm-and-credit RPC.

Both PaymentService.handle_callback and admin/credits_router.confirm_order now
call ``confirm_order_and_credit_atomic(order_id)`` (one idempotent RPC) instead
of the old two-await ``update_order`` + ``add_points`` block. These tests
monkeypatch the repo to return each RPC result shape and assert the caller maps
it to the right external envelope.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# PaymentService.handle_callback
# --------------------------------------------------------------------------- #


class _FakePaymentRepo:
    """Stub payment repo: canned order + canned RPC result."""

    def __init__(self, order: Optional[Dict[str, Any]], rpc_result: Any) -> None:
        self._order = order
        self._rpc_result = rpc_result
        self.update_order_calls: list = []
        self.confirm_calls: list = []

    async def get_order_by_trade_no(self, trade_no: str):
        return self._order

    async def update_order(self, order_id, data):
        self.update_order_calls.append((order_id, data))
        return {}

    async def confirm_order_and_credit_atomic(self, order_id):
        self.confirm_calls.append(order_id)
        return self._rpc_result


def _make_service(order, rpc_result):
    from app.services.billing.payment_service import PaymentService

    svc = PaymentService.__new__(PaymentService)  # skip __init__ (no DB)
    svc.payment_repo = _FakePaymentRepo(order, rpc_result)
    svc.points_repo = None
    svc.points_service = None
    return svc


_PENDING_ORDER = {
    "id": 9_260_000_000_000_011,
    "team_id": 9_260_000_000_000_001,
    "user_id": "00000000-0000-0000-0000-000000000001",
    "points_amount": 100,
    "payment_status": "pending",
}


async def test_handle_callback_newly_credited():
    svc = _make_service(
        _PENDING_ORDER,
        {
            "success": True,
            "already_credited": False,
            "points_added": 100,
            "new_balance": 100,
            "reason": None,
        },
    )
    result = await svc.handle_callback("MH_x", "alipay", paid=True)
    assert result == {
        "success": True,
        "message": "Payment processed successfully",
        "points_added": 100,
    }
    assert svc.payment_repo.confirm_calls == [9_260_000_000_000_011]


async def test_handle_callback_already_credited_no_op():
    svc = _make_service(
        _PENDING_ORDER,
        {
            "success": True,
            "already_credited": True,
            "points_added": 0,
            "new_balance": 100,
            "reason": None,
        },
    )
    result = await svc.handle_callback("MH_x", "alipay", paid=True)
    assert result["success"] is True
    assert result["points_added"] == 0
    assert result["message"] == "Payment processed successfully"


async def test_handle_callback_rpc_unavailable_returns_503ish():
    svc = _make_service(_PENDING_ORDER, None)
    result = await svc.handle_callback("MH_x", "alipay", paid=True)
    assert result["success"] is False
    assert result["points_added"] == 0
    assert "unavailable" in result["message"].lower()


async def test_handle_callback_rpc_failure_returns_reason():
    svc = _make_service(
        _PENDING_ORDER,
        {
            "success": False,
            "already_credited": False,
            "points_added": 0,
            "new_balance": None,
            "reason": "Order status is 'expired', cannot confirm",
        },
    )
    result = await svc.handle_callback("MH_x", "alipay", paid=True)
    assert result["success"] is False
    assert result["points_added"] == 0
    assert "expired" in result["message"]


async def test_handle_callback_already_paid_fast_path():
    """The current_status=='paid' fast-path must still short-circuit before the
    RPC (returns 'Already processed', no confirm call)."""
    paid_order = {**_PENDING_ORDER, "payment_status": "paid"}
    svc = _make_service(paid_order, {"success": True})
    result = await svc.handle_callback("MH_x", "alipay", paid=True)
    assert result == {
        "success": True,
        "message": "Already processed",
        "points_added": 0,
    }
    assert svc.payment_repo.confirm_calls == []  # never reached the RPC


async def test_handle_callback_order_not_found():
    svc = _make_service(None, {"success": True})
    result = await svc.handle_callback("MH_unknown", "alipay", paid=True)
    assert result["success"] is False
    assert result["message"] == "Order not found"
    assert svc.payment_repo.confirm_calls == []


# --------------------------------------------------------------------------- #
# admin/credits_router.confirm_order
# --------------------------------------------------------------------------- #


class _FakeAdminRepo:
    def __init__(self, order, rpc_result):
        self._order = order
        self._rpc_result = rpc_result
        self.confirm_calls: list = []

    async def get_order(self, order_id):
        return self._order

    async def confirm_order_and_credit_atomic(self, order_id):
        self.confirm_calls.append(order_id)
        return self._rpc_result


class _FakeAuth:
    user_id = "admin-uuid"


class _FakeRequest:
    client = None


async def _run_confirm(monkeypatch, order, rpc_result):
    import importlib

    # The admin package __init__ rebinds the name ``credits_router`` to the
    # APIRouter object (``from .credits_router import router as credits_router``),
    # shadowing the submodule attribute — so import the module explicitly.
    credits_router = importlib.import_module("app.api.admin.credits_router")

    repo = _FakeAdminRepo(order, rpc_result)
    monkeypatch.setattr(credits_router, "get_admin_credits_repository", lambda: repo)

    async def _fake_audit(**kwargs):
        return None

    monkeypatch.setattr(credits_router, "create_audit_log", _fake_audit)
    return repo, await credits_router.confirm_order(
        order_id="9260000000000011",
        auth=_FakeAuth(),
        request=_FakeRequest(),
    )


_ADMIN_PENDING_ORDER = {
    "id": 9_260_000_000_000_011,
    "team_id": 9_260_000_000_000_001,
    "user_id": "00000000-0000-0000-0000-000000000001",
    "points_amount": 100,
    "amount_cents": 990,
    "payment_status": "pending",
}


async def test_admin_confirm_order_success(monkeypatch):
    repo, resp = await _run_confirm(
        monkeypatch,
        _ADMIN_PENDING_ORDER,
        {
            "success": True,
            "already_credited": False,
            "points_added": 100,
            "new_balance": 100,
            "reason": None,
        },
    )
    assert resp["ok"] is True
    assert repo.confirm_calls == ["9260000000000011"]


async def test_admin_confirm_order_rpc_failure_400(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await _run_confirm(monkeypatch, _ADMIN_PENDING_ORDER, None)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Failed to confirm order"


async def test_admin_confirm_order_reason_400(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await _run_confirm(
            monkeypatch,
            _ADMIN_PENDING_ORDER,
            {
                "success": False,
                "already_credited": False,
                "points_added": 0,
                "new_balance": None,
                "reason": "Order not found",
            },
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "Order not found"


async def test_admin_confirm_order_non_pending_precheck_400(monkeypatch):
    from fastapi import HTTPException

    paid_order = {**_ADMIN_PENDING_ORDER, "payment_status": "paid"}
    with pytest.raises(HTTPException) as exc:
        await _run_confirm(monkeypatch, paid_order, {"success": True})
    assert exc.value.status_code == 400
