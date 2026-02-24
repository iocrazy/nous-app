# app/schemas/payment.py

"""
Payment system validation schema module

Defines Pydantic 2.0 validation schemas for payment orders, including
order creation requests, full order responses, and order status queries.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    """Request body for creating a new payment order"""

    package_id: str = Field(..., description="Point package ID to purchase")
    payment_method: str = Field(
        ...,
        pattern="^(wechat|alipay)$",
        description="Payment method: wechat or alipay",
    )
    team_id: str = Field(..., description="Team ID that will receive the points")


class OrderResponse(BaseModel):
    """Full payment order details"""

    id: str = Field(..., description="Order ID")
    team_id: str = Field(..., description="Team ID")
    user_id: str = Field(..., description="User who created the order")
    package_id: str = Field(..., description="Point package ID")
    points_amount: int = Field(..., gt=0, description="Points to be credited")
    amount_cents: int = Field(..., gt=0, description="Payment amount in cents")
    currency: str = Field(..., description="Currency code")
    payment_method: str = Field(..., description="Payment method used")
    payment_status: str = Field(..., description="Current payment status")
    payment_url: Optional[str] = Field(
        None, description="URL to redirect user for payment"
    )
    trade_no: Optional[str] = Field(
        None, description="Third-party payment provider trade number"
    )
    paid_at: Optional[datetime] = Field(
        None, description="Payment completion timestamp"
    )
    expired_at: datetime = Field(..., description="Order expiration timestamp")
    created_at: datetime = Field(..., description="Order creation timestamp")


class OrderStatusResponse(BaseModel):
    """Lightweight order status for polling"""

    order_id: str = Field(..., description="Order ID")
    payment_status: str = Field(..., description="Current payment status")
    points_amount: int = Field(
        ..., gt=0, description="Points associated with the order"
    )
    paid_at: Optional[datetime] = Field(
        None, description="Payment completion timestamp"
    )
