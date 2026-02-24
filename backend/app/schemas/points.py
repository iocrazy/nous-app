# app/schemas/points.py

"""
Points system validation schema module

Defines Pydantic 2.0 validation schemas for the points and quota system,
including point packages, team/member quotas, transactions, pricing,
and administrative operations.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PointPackage(BaseModel):
    """Purchasable point package definition"""

    id: str = Field(..., description="Package ID")
    name: str = Field(..., description="Package display name")
    description: Optional[str] = Field(None, description="Package description")
    points_amount: int = Field(
        ..., gt=0, description="Number of points in this package"
    )
    price_cents: int = Field(..., gt=0, description="Price in cents")
    currency: str = Field(default="CNY", description="Currency code")
    is_active: bool = Field(
        default=True, description="Whether the package is available for purchase"
    )
    sort_order: int = Field(default=0, description="Display sort order (ascending)")


class TeamQuota(BaseModel):
    """Team-level quota and balance information"""

    team_id: str = Field(..., description="Team ID")
    points_balance: int = Field(default=0, ge=0, description="Current points balance")
    storage_limit_bytes: int = Field(
        default=5368709120, description="Storage limit in bytes (default 5 GB)"
    )
    storage_used_bytes: int = Field(
        default=0, ge=0, description="Storage used in bytes"
    )
    free_points_granted: bool = Field(
        default=False, description="Whether free initial points have been granted"
    )


class MemberQuota(BaseModel):
    """Per-member monthly points quota within a team"""

    id: str = Field(..., description="Member quota record ID")
    team_id: str = Field(..., description="Team ID")
    user_id: str = Field(..., description="User ID")
    monthly_points_limit: Optional[int] = Field(
        default=None, ge=0, description="Monthly points limit (NULL = unlimited)"
    )
    points_used_this_month: int = Field(
        default=0, ge=0, description="Points consumed in the current month"
    )
    reset_at: Optional[datetime] = Field(
        None, description="Timestamp when the monthly counter resets"
    )


class MemberQuotaUpdate(BaseModel):
    """Request body for updating a member's monthly quota"""

    monthly_points_limit: Optional[int] = Field(
        default=None, ge=0, description="Monthly points limit (NULL = unlimited)"
    )


class PointTransaction(BaseModel):
    """Record of a single points transaction"""

    id: str = Field(..., description="Transaction ID")
    team_id: str = Field(..., description="Team ID")
    user_id: Optional[str] = Field(
        None, description="User who triggered the transaction"
    )
    amount: int = Field(
        ..., description="Points amount (positive = credit, negative = debit)"
    )
    balance_after: int = Field(
        ..., ge=0, description="Team points balance after this transaction"
    )
    type: str = Field(
        ...,
        pattern="^(purchase|consume|refund|gift|admin_adjust)$",
        description="Transaction type: purchase, consume, refund, gift, or admin_adjust",
    )
    reference_type: Optional[str] = Field(
        None, description="Type of the referenced entity (e.g. order, video)"
    )
    reference_id: Optional[str] = Field(None, description="ID of the referenced entity")
    description: Optional[str] = Field(None, description="Human-readable description")
    created_at: datetime = Field(..., description="Transaction timestamp")


class PointPricing(BaseModel):
    """Points cost definition for a specific action type"""

    action_type: str = Field(
        ..., description="Action identifier (e.g. video_parse, ai_summary)"
    )
    points_cost: int = Field(..., ge=0, description="Points required for this action")
    description: Optional[str] = Field(None, description="Action description")
    is_active: bool = Field(
        default=True, description="Whether this pricing rule is active"
    )


class PointsBalanceResponse(BaseModel):
    """API response containing team balance and storage usage"""

    team_id: str = Field(..., description="Team ID")
    points_balance: int = Field(..., description="Current points balance")
    storage_limit_bytes: int = Field(..., description="Storage limit in bytes")
    storage_used_bytes: int = Field(..., description="Storage used in bytes")
    storage_used_percent: float = Field(
        ..., ge=0, le=100, description="Storage usage as a percentage"
    )


class PointsAdjustRequest(BaseModel):
    """Admin request to manually adjust a team's points balance"""

    team_id: str = Field(..., description="Team ID")
    amount: int = Field(
        ..., description="Points to adjust (positive to add, negative to deduct)"
    )
    description: str = Field(..., min_length=1, description="Reason for the adjustment")


class QuotaCheckResult(BaseModel):
    """Result of a pre-action quota check"""

    allowed: bool = Field(..., description="Whether the action is permitted")
    points_cost: int = Field(..., ge=0, description="Points cost of the action")
    current_balance: int = Field(..., ge=0, description="Current team points balance")
    reason: Optional[str] = Field(
        None, description="Explanation when the action is denied"
    )
