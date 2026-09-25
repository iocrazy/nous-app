"""Response models for the admin credits write routes and the point-package /
pricing reads (``/admin/credits/*``) plus ``POST /points/admin/adjust``.

Each model declares exactly the keys the handler already sent. Package and
pricing rows come from ``AdminCreditsRepository`` whole (``SELECT *``), where
``_parity`` has already turned uuids and timestamps into strings — so ids and
``created_at`` / ``updated_at`` are ``str`` here (``+00:00`` ISO, the
repository's ``isoformat()``), not ``datetime``.

Wire parity is pinned by ``tests/api/admin/test_admin_credits_wire.py``.
"""

from __future__ import annotations

from pydantic import BaseModel


class AdminPointPackage(BaseModel):
    """One ``point_packages`` row."""

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


class AdminPointPricing(BaseModel):
    """One ``point_pricing`` row."""

    id: str
    action_type: str
    points_cost: int
    description: str | None
    is_active: bool
    created_at: str
    updated_at: str


class AdminCreditsOrderActionResult(BaseModel):
    """Manual order confirm / refund."""

    ok: bool
    message: str


class AdminCreditsOkResult(BaseModel):
    """Package delete."""

    ok: bool


class AdminBatchGiftError(BaseModel):
    """A team the gift did not reach, and why."""

    team_id: str
    error: str


class AdminBatchGiftResult(BaseModel):
    ok: bool
    gifted_count: int
    errors: list[AdminBatchGiftError]


class AdminPointsAdjustResult(BaseModel):
    """``POST /admin/credits/adjust``: the team's balance after the change."""

    ok: bool
    new_balance: int


class AdminTeamPointsAdjustResult(BaseModel):
    """``POST /points/admin/adjust`` (the user app's Billing page)."""

    success: bool
    message: str
    new_balance: int
