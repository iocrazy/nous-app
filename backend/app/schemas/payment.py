# app/schemas/payment.py

"""
Payment system validation schema module

Defines Pydantic 2.0 validation schemas for payment orders, including
order creation requests and the response rows the ``/payment/*`` routes send
(wire parity pinned by ``tests/api/test_payment_wire.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Both enforced by CHECK constraints on ``orders`` (``orders_payment_*_check``).
PaymentMethod = Literal["wechat", "alipay"]
PaymentStatus = Literal["pending", "paid", "failed", "expired", "refunded"]


class CreateOrderRequest(BaseModel):
    """Request body for creating a new payment order"""

    package_id: str = Field(..., description="Point package ID to purchase")
    payment_method: str = Field(
        ...,
        pattern="^(wechat|alipay)$",
        description="Payment method: wechat or alipay",
    )
    team_id: str = Field(..., description="Team ID that will receive the points")


class PaymentPackageRow(BaseModel):
    """One ``point_packages`` row as ``GET /payment/packages`` sends it.

    The repository (``points_repository._parity``) has already turned the uuid
    id and both timestamps into strings (``+00:00`` ISO), so they are ``str``.
    """

    id: str
    name: str
    description: str | None
    points_amount: int
    price_cents: int
    currency: str
    is_active: bool
    sort_order: int
    created_at: str
    updated_at: str


class PaymentOrderRow(BaseModel):
    """One ``orders`` row (``SELECT *``) as the payment routes send it.

    ``id`` / ``team_id`` are Snowflake BIGINTs and stay JSON **numbers**
    (``payment_repository._parity`` keeps them native). uuids and timestamps
    are already ISO / uuid strings.
    """

    id: int
    team_id: int
    user_id: str
    package_id: str | None
    points_amount: int
    amount_cents: int
    currency: str
    payment_method: PaymentMethod
    payment_status: PaymentStatus
    payment_url: str | None
    trade_no: str | None
    paid_at: str | None
    expired_at: str
    created_at: str
    updated_at: str


class PaymentOrderStatus(BaseModel):
    """``GET /payment/order/{id}/status``: the lightweight polling view."""

    order_id: int
    payment_status: PaymentStatus
    points_amount: int
    paid_at: str | None
