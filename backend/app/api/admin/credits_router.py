"""Admin API routes for Credits / Points management."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.admin_deps import AdminAuthDep
from app.repositories.admin.credits_repository import get_admin_credits_repository
from app.schemas.admin import (
    AdminBatchGiftRequest,
    AdminConsumptionChartItem,
    AdminCreditsStatsResponse,
    AdminCreditTransactionListResponse,
    AdminCreditTransactionResponse,
    AdminOrderListResponse,
    AdminOrderResponse,
    AdminPackageRequest,
    AdminPointsAdjustRequest,
    AdminPricingUpdateRequest,
    AdminRevenueChartItem,
    AdminTeamCreditsDetailResponse,
    AdminTopTeamItem,
)
from app.utils.admin_helpers import (
    batch_get_team_member_counts,
    batch_get_user_auth_info,
    create_audit_log,
    get_user_info,
)

router = APIRouter()


# ============================================
# Helper: build team name map
# ============================================


async def _get_team_display_name(team: dict, supabase=None) -> str:
    """Get display name for a team. Personal teams show '{Owner}'s Workspace'."""
    if not team.get("is_personal"):
        return team["name"]
    owner_id = team.get("owner_id")
    if not owner_id:
        return team["name"]
    email, username = await get_user_info(str(owner_id))
    owner_name = username or (email.split("@")[0] if email else "")
    if not owner_name:
        return team["name"]
    return f"{owner_name[0].upper()}{owner_name[1:]}'s Workspace"


async def _get_team_name_map(team_ids: list[str]) -> dict[str, str]:
    """Fetch display names for a list of team_ids."""
    if not team_ids:
        return {}
    repo = get_admin_credits_repository()
    teams = await repo.get_teams_by_ids(team_ids)
    entries = await asyncio.gather(*[_get_team_display_name(t) for t in teams])
    return {str(teams[i]["id"]): entries[i] for i in range(len(teams))}


# ============================================
# Stats & Charts
# ============================================


@router.get("/stats", response_model=AdminCreditsStatsResponse)
async def get_credits_stats(auth: AdminAuthDep):
    """System-wide credits statistics."""
    repo = get_admin_credits_repository()

    quotas = await repo.all_quotas_balances()
    total_points = sum((r.get("points_balance") or 0) for r in quotas)

    consume_rows = await repo.transactions_by_type("consume")
    total_consumed = sum(abs(r.get("amount") or 0) for r in consume_rows)

    purchase_rows = await repo.transactions_by_type("purchase")
    total_purchased = sum((r.get("amount") or 0) for r in purchase_rows)

    revenue_rows = await repo.orders_by_status(payment_status="paid")
    total_revenue_cents = sum((r.get("amount_cents") or 0) for r in revenue_rows)

    active_teams_count = await repo.teams_count()
    pending_orders_count = await repo.orders_count_by_status("pending")

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_rows = await repo.orders_by_status(
        payment_status="paid", since_iso=month_start.isoformat()
    )
    monthly_revenue_cents = sum((r.get("amount_cents") or 0) for r in monthly_rows)

    return AdminCreditsStatsResponse(
        total_points_in_system=total_points,
        total_consumed=total_consumed,
        total_purchased=total_purchased,
        total_revenue_cents=total_revenue_cents,
        active_teams_count=active_teams_count,
        pending_orders_count=pending_orders_count,
        monthly_revenue_cents=monthly_revenue_cents,
    )


@router.get("/revenue-chart", response_model=list[AdminRevenueChartItem])
async def get_revenue_chart(
    auth: AdminAuthDep,
    period: str = Query("day", pattern="^(day|week|month)$"),
    days: int = Query(30, ge=7, le=365),
):
    """Revenue trend data grouped by period."""
    repo = get_admin_credits_repository()

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = await repo.revenue_chart_rows(since.isoformat())

    # Group by period
    buckets: dict[str, dict] = {}
    for row in rows:
        paid_at = row.get("paid_at")
        if not paid_at:
            continue
        dt = datetime.fromisoformat(paid_at.replace("Z", "+00:00"))
        if period == "day":
            key = dt.strftime("%Y-%m-%d")
        elif period == "week":
            # ISO week start (Monday)
            week_start = dt - timedelta(days=dt.weekday())
            key = week_start.strftime("%Y-%m-%d")
        else:  # month
            key = dt.strftime("%Y-%m")

        if key not in buckets:
            buckets[key] = {"revenue_cents": 0, "points_sold": 0}
        buckets[key]["revenue_cents"] += row.get("amount_cents") or 0
        buckets[key]["points_sold"] += row.get("points_amount") or 0

    return [AdminRevenueChartItem(date=k, **v) for k, v in sorted(buckets.items())]


@router.get("/consumption-chart", response_model=list[AdminConsumptionChartItem])
async def get_consumption_chart(auth: AdminAuthDep):
    """Consumption distribution by action type."""
    repo = get_admin_credits_repository()

    rows = await repo.transactions_by_type("consume", columns="description, amount")

    # Extract action_type from description (e.g. "video_parse: ..." → "video_parse")
    buckets: dict[str, int] = {}
    for row in rows:
        desc = row.get("description") or "unknown"
        action_type = desc.split(":")[0].split(" ")[0].strip()
        buckets[action_type] = buckets.get(action_type, 0) + abs(row.get("amount") or 0)

    return [
        AdminConsumptionChartItem(action_type=k, total_points=v)
        for k, v in sorted(buckets.items(), key=lambda x: -x[1])
    ]


@router.get("/top-teams", response_model=list[AdminTopTeamItem])
async def get_top_teams(
    auth: AdminAuthDep,
    limit: int = Query(10, ge=1, le=50),
):
    """Top consuming teams."""
    repo = get_admin_credits_repository()

    rows = await repo.transactions_by_type("consume", columns="team_id, amount")

    # Aggregate by team_id
    team_totals: dict[str, int] = {}
    for row in rows:
        tid = str(row.get("team_id", ""))
        if not tid:
            continue
        team_totals[tid] = team_totals.get(tid, 0) + abs(row.get("amount") or 0)

    # Sort and limit
    top_ids = sorted(team_totals, key=lambda t: -team_totals[t])[:limit]
    name_map = await _get_team_name_map(top_ids)

    return [
        AdminTopTeamItem(
            team_id=tid,
            team_name=name_map.get(tid, "Unknown"),
            total_consumed=team_totals[tid],
        )
        for tid in top_ids
    ]


# ============================================
# Transactions
# ============================================


@router.get("/transactions", response_model=AdminCreditTransactionListResponse)
async def list_transactions(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    team_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
):
    """List credit transactions with filtering and pagination."""
    repo = get_admin_credits_repository()

    valid_sort_fields = {"created_at", "amount", "type"}
    if sort_by not in valid_sort_fields:
        sort_by = "created_at"

    offset = (page - 1) * page_size
    rows, total = await repo.list_transactions(
        team_id=team_id,
        type=type,
        sort_by=sort_by,
        sort_desc=(sort_order != "asc"),
        offset=offset,
        limit=page_size,
    )

    # Enrich with team names and user emails
    team_ids = list({str(r["team_id"]) for r in rows if r.get("team_id")})
    user_ids = list({str(r["user_id"]) for r in rows if r.get("user_id")})
    name_map = await _get_team_name_map(team_ids)
    email_map = await batch_get_user_auth_info(user_ids) if user_ids else {}

    items = [
        AdminCreditTransactionResponse(
            id=str(r["id"]),
            team_id=str(r["team_id"]),
            team_name=name_map.get(str(r["team_id"])),
            user_id=str(r["user_id"]) if r.get("user_id") else None,
            user_email=(
                email_map.get(str(r["user_id"]), (None,))[0]
                if r.get("user_id")
                else None
            ),
            type=r.get("type", ""),
            amount=r.get("amount") or 0,
            balance_after=r.get("balance_after") or 0,
            description=r.get("description"),
            created_at=str(r.get("created_at", "")),
        )
        for r in rows
    ]

    return AdminCreditTransactionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ============================================
# Orders
# ============================================


@router.get("/orders", response_model=AdminOrderListResponse)
async def list_orders(
    auth: AdminAuthDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    payment_status: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    team_id: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("created_at"),
    sort_order: Optional[str] = Query("desc"),
):
    """List orders with filtering and pagination."""
    repo = get_admin_credits_repository()

    valid_sort_fields = {
        "created_at",
        "amount_cents",
        "points_amount",
        "payment_status",
    }
    if sort_by not in valid_sort_fields:
        sort_by = "created_at"

    offset = (page - 1) * page_size
    rows, total = await repo.list_orders(
        payment_status=payment_status,
        payment_method=payment_method,
        team_id=team_id,
        sort_by=sort_by,
        sort_desc=(sort_order != "asc"),
        offset=offset,
        limit=page_size,
    )

    # Enrich
    team_ids = list({str(r["team_id"]) for r in rows if r.get("team_id")})
    user_ids = list({str(r["user_id"]) for r in rows if r.get("user_id")})
    package_ids = list({str(r["package_id"]) for r in rows if r.get("package_id")})

    name_map = await _get_team_name_map(team_ids)
    email_map = await batch_get_user_auth_info(user_ids) if user_ids else {}
    pkg_name_map = await repo.get_package_names(package_ids)

    items = [
        AdminOrderResponse(
            id=str(r["id"]),
            order_no=r.get("order_no") or str(r["id"])[:8],
            team_id=str(r["team_id"]),
            team_name=name_map.get(str(r["team_id"])),
            user_id=str(r["user_id"]) if r.get("user_id") else None,
            user_email=(
                email_map.get(str(r["user_id"]), (None,))[0]
                if r.get("user_id")
                else None
            ),
            package_name=pkg_name_map.get(str(r.get("package_id", ""))),
            points_amount=r.get("points_amount") or 0,
            amount_cents=r.get("amount_cents") or 0,
            payment_method=r.get("payment_method"),
            payment_status=r.get("payment_status", "pending"),
            created_at=str(r.get("created_at", "")),
            paid_at=str(r["paid_at"]) if r.get("paid_at") else None,
        )
        for r in rows
    ]

    return AdminOrderListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/orders/{order_id}/confirm")
async def confirm_order(
    order_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Manually confirm payment for a pending order."""
    repo = get_admin_credits_repository()

    order = await repo.get_order(order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    if order["payment_status"] != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Order status is '{order['payment_status']}', can only confirm 'pending'",
        )

    # Mark paid AND credit points atomically (one idempotent RPC). Closes the
    # old two-await crash gap (paid but uncredited) and the double-credit risk.
    result = await repo.confirm_order_and_credit_atomic(order_id)
    if result is None or not result.get("success"):
        reason = (result or {}).get("reason") or "Failed to confirm order"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reason,
        )

    # Audit log
    await create_audit_log(
        admin_id=auth.user_id,
        action="order_confirm",
        target_type="order",
        target_id=order_id,
        details={
            "team_id": str(order["team_id"]),
            "points_amount": order.get("points_amount"),
            "amount_cents": order.get("amount_cents"),
        },
        ip_address=request.client.host if request.client else None,
    )

    logger.info(f"[Admin] Order {order_id} confirmed by admin={auth.user_id}")
    return {"ok": True, "message": "Order confirmed and points added"}


@router.post("/orders/{order_id}/refund")
async def refund_order(
    order_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Refund a paid order (deduct points, mark as refunded)."""
    repo = get_admin_credits_repository()

    order = await repo.get_order(order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    if order["payment_status"] != "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Order status is '{order['payment_status']}', can only refund 'paid'",
        )

    # Mark refunded AND debit points atomically (one idempotent RPC, CLAMP AT 0).
    # Closes the old two-await crash gap AND the silent no-op deduct bug:
    # add_points rejected the negative amount, so refunds previously marked the
    # order refunded but NEVER clawed back the granted points.
    result = await repo.refund_order_and_debit_atomic(order_id)
    if result is None or not result.get("success"):
        reason = (result or {}).get("reason") or "Failed to refund order"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=reason,
        )

    already_refunded = bool(result.get("already_refunded"))
    points_debited = result.get("points_debited") or 0

    await create_audit_log(
        admin_id=auth.user_id,
        action="order_refund",
        target_type="order",
        target_id=order_id,
        details={
            "team_id": str(order["team_id"]),
            "points_refunded": points_debited,
            "already_refunded": already_refunded,
        },
        ip_address=request.client.host if request.client else None,
    )

    logger.info(f"[Admin] Order {order_id} refunded by admin={auth.user_id}")
    message = (
        "Order was already refunded"
        if already_refunded
        else "Order refunded and points deducted"
    )
    return {"ok": True, "message": message}


# ============================================
# Packages CRUD
# ============================================


@router.get("/packages")
async def list_packages(auth: AdminAuthDep):
    """List all point packages (active and inactive)."""
    repo = get_admin_credits_repository()
    return await repo.list_packages()


@router.post("/packages")
async def create_package(
    body: AdminPackageRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Create a new point package."""
    repo = get_admin_credits_repository()

    payload = {
        "name": body.name,
        "description": body.description,
        "points_amount": body.points_amount,
        "price_cents": body.price_cents,
        "sort_order": body.sort_order,
        "is_active": body.is_active,
    }
    created = await repo.create_package(payload)

    await create_audit_log(
        admin_id=auth.user_id,
        action="package_create",
        target_type="point_package",
        target_id=str(created["id"]) if created else "unknown",
        details=payload,
        ip_address=request.client.host if request.client else None,
    )

    return created


@router.put("/packages/{package_id}")
async def update_package(
    package_id: str,
    body: AdminPackageRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Update an existing point package."""
    repo = get_admin_credits_repository()

    payload = {
        "name": body.name,
        "description": body.description,
        "points_amount": body.points_amount,
        "price_cents": body.price_cents,
        "sort_order": body.sort_order,
        "is_active": body.is_active,
    }
    updated = await repo.update_package(package_id, payload)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Package not found"
        )

    await create_audit_log(
        admin_id=auth.user_id,
        action="package_update",
        target_type="point_package",
        target_id=package_id,
        details=payload,
        ip_address=request.client.host if request.client else None,
    )

    return updated


@router.delete("/packages/{package_id}")
async def delete_package(
    package_id: str,
    auth: AdminAuthDep,
    request: Request,
):
    """Delete a point package."""
    repo = get_admin_credits_repository()
    await repo.delete_package(package_id)

    await create_audit_log(
        admin_id=auth.user_id,
        action="package_delete",
        target_type="point_package",
        target_id=package_id,
        ip_address=request.client.host if request.client else None,
    )

    return {"ok": True}


# ============================================
# Pricing
# ============================================


@router.get("/pricing")
async def list_pricing(auth: AdminAuthDep):
    """List all action pricing rules."""
    repo = get_admin_credits_repository()
    return await repo.list_pricing()


@router.put("/pricing/{action_type}")
async def update_pricing(
    action_type: str,
    body: AdminPricingUpdateRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Update pricing for a specific action type."""
    repo = get_admin_credits_repository()

    payload: dict = {"points_cost": body.points_cost}
    if body.description is not None:
        payload["description"] = body.description

    updated = await repo.update_pricing(action_type, payload)

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Action type not found"
        )

    await create_audit_log(
        admin_id=auth.user_id,
        action="pricing_update",
        target_type="point_pricing",
        target_id=action_type,
        details={"points_cost": body.points_cost, "description": body.description},
        ip_address=request.client.host if request.client else None,
    )

    return updated


# ============================================
# Batch Gift / Adjust / Team Detail
# ============================================


@router.post("/batch-gift")
async def batch_gift(
    body: AdminBatchGiftRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Gift points to multiple teams."""
    from app.services.billing.points_service import PointsService

    points_svc = PointsService()

    gifted_count = 0
    errors = []
    for tid in body.team_ids:
        try:
            await points_svc.add_points(
                team_id=tid,
                amount=body.amount,
                type="gift",
                description=body.description or f"Admin gift by {auth.user_id}",
                user_id=auth.user_id,
            )
            gifted_count += 1
        except Exception as e:
            logger.warning(f"[Admin] Batch gift failed for team {tid}: {e}")
            errors.append({"team_id": tid, "error": str(e)})

    await create_audit_log(
        admin_id=auth.user_id,
        action="batch_gift",
        target_type="teams",
        target_id="batch",
        details={
            "team_ids": body.team_ids,
            "amount": body.amount,
            "gifted_count": gifted_count,
        },
        ip_address=request.client.host if request.client else None,
    )

    return {"ok": True, "gifted_count": gifted_count, "errors": errors}


@router.post("/adjust")
async def adjust_points(
    body: AdminPointsAdjustRequest,
    auth: AdminAuthDep,
    request: Request,
):
    """Manually adjust points for a single team."""
    from app.services.billing.points_service import PointsService

    points_svc = PointsService()

    result = await points_svc.add_points(
        team_id=body.team_id,
        amount=body.amount,
        type="admin_adjust",
        description=body.description or f"Admin adjustment by {auth.user_id}",
        user_id=auth.user_id,
    )

    await create_audit_log(
        admin_id=auth.user_id,
        action="points_adjust",
        target_type="team_quota",
        target_id=body.team_id,
        details={"amount": body.amount, "description": body.description},
        ip_address=request.client.host if request.client else None,
    )

    return {"ok": True, "new_balance": result.get("new_balance")}


@router.get("/team/{team_id}/detail", response_model=AdminTeamCreditsDetailResponse)
async def get_team_detail(
    team_id: str,
    auth: AdminAuthDep,
):
    """Get detailed credits info for a single team."""
    repo = get_admin_credits_repository()

    team = await repo.get_team(team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )

    team_display_name = await _get_team_display_name(team)
    quota = await repo.get_team_quota(team_id)

    member_counts = await batch_get_team_member_counts([team_id])
    member_count = member_counts.get(team_id, 0)

    tx_rows = await repo.recent_transactions(team_id, limit=10)

    # Enrich transactions with user emails
    tx_user_ids = list({str(r["user_id"]) for r in tx_rows if r.get("user_id")})
    tx_email_map = await batch_get_user_auth_info(tx_user_ids) if tx_user_ids else {}

    recent = [
        AdminCreditTransactionResponse(
            id=str(r["id"]),
            team_id=str(r["team_id"]),
            team_name=team_display_name,
            user_id=str(r["user_id"]) if r.get("user_id") else None,
            user_email=(
                tx_email_map.get(str(r["user_id"]), (None,))[0]
                if r.get("user_id")
                else None
            ),
            type=r.get("type", ""),
            amount=r.get("amount") or 0,
            balance_after=r.get("balance_after") or 0,
            description=r.get("description"),
            created_at=str(r.get("created_at", "")),
        )
        for r in tx_rows
    ]

    return AdminTeamCreditsDetailResponse(
        team_id=team_id,
        team_name=team_display_name,
        points_balance=quota.get("points_balance") or 0,
        storage_limit_bytes=quota.get("storage_limit_bytes") or 0,
        storage_used_bytes=quota.get("storage_used_bytes") or 0,
        member_count=member_count,
        recent_transactions=recent,
    )
