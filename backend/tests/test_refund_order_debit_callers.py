"""Unit tests for the admin/credits_router.refund_order caller of the atomic
refund-and-debit RPC.

refund_order now calls ``refund_order_and_debit_atomic(order_id)`` (one
idempotent, clamp-at-0 RPC) instead of the old two-await ``update_order`` +
``add_points(negative)`` block (which silently no-op'd the deduct because
add_points rejects amount <= 0). These tests monkeypatch the repo to return each
RPC result shape and assert the caller maps it to the right external envelope.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


class _FakeAdminRepo:
    def __init__(self, order, rpc_result):
        self._order = order
        self._rpc_result = rpc_result
        self.refund_calls: list = []

    async def get_order(self, order_id):
        return self._order

    async def refund_order_and_debit_atomic(self, order_id):
        self.refund_calls.append(order_id)
        return self._rpc_result


class _FakeAuth:
    user_id = "admin-uuid"


class _FakeRequest:
    client = None


async def _run_refund(monkeypatch, order, rpc_result):
    import importlib

    # The admin package __init__ rebinds ``credits_router`` to the APIRouter
    # object, shadowing the submodule — import the module explicitly.
    credits_router = importlib.import_module("app.api.admin.credits_router")

    repo = _FakeAdminRepo(order, rpc_result)
    monkeypatch.setattr(credits_router, "get_admin_credits_repository", lambda: repo)

    async def _fake_audit(**kwargs):
        return None

    monkeypatch.setattr(credits_router, "create_audit_log", _fake_audit)
    return repo, await credits_router.refund_order(
        order_id="9261000000000011",
        auth=_FakeAuth(),
        request=_FakeRequest(),
    )


_ADMIN_PAID_ORDER = {
    "id": 9_261_000_000_000_011,
    "team_id": 9_261_000_000_000_001,
    "user_id": "00000000-0000-0000-0000-000000000001",
    "points_amount": 100,
    "amount_cents": 990,
    "payment_status": "paid",
}


async def test_admin_refund_order_success(monkeypatch):
    repo, resp = await _run_refund(
        monkeypatch,
        _ADMIN_PAID_ORDER,
        {
            "success": True,
            "already_refunded": False,
            "points_debited": 100,
            "new_balance": 0,
            "reason": None,
        },
    )
    assert resp["ok"] is True
    assert resp["message"] == "Order refunded and points deducted"
    assert repo.refund_calls == ["9261000000000011"]


async def test_admin_refund_order_already_refunded_message(monkeypatch):
    repo, resp = await _run_refund(
        monkeypatch,
        _ADMIN_PAID_ORDER,
        {
            "success": True,
            "already_refunded": True,
            "points_debited": 0,
            "new_balance": 0,
            "reason": None,
        },
    )
    assert resp["ok"] is True
    assert "already refunded" in resp["message"].lower()


async def test_admin_refund_order_rpc_failure_400(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await _run_refund(monkeypatch, _ADMIN_PAID_ORDER, None)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Failed to refund order"


async def test_admin_refund_order_reason_400(monkeypatch):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await _run_refund(
            monkeypatch,
            _ADMIN_PAID_ORDER,
            {
                "success": False,
                "already_refunded": False,
                "points_debited": 0,
                "new_balance": None,
                "reason": "Order status is 'pending', cannot refund",
            },
        )
    assert exc.value.status_code == 400
    assert "pending" in exc.value.detail


async def test_admin_refund_order_non_paid_precheck_400(monkeypatch):
    from fastapi import HTTPException

    pending_order = {**_ADMIN_PAID_ORDER, "payment_status": "pending"}
    with pytest.raises(HTTPException) as exc:
        await _run_refund(monkeypatch, pending_order, {"success": True})
    assert exc.value.status_code == 400
    # Friendly precheck fires before the RPC is even called.
