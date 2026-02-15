# backend/app/api/points_router.py

"""
Points Router

Points capacity system API endpoints: balance, transactions, pricing,
usage statistics, quota checks, and admin adjustments.
Requires authentication (JWT or API Key).
"""

import random
import string
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.points_repository import PointsRepository
from app.schemas.points import PointsAdjustRequest
from app.services.points_service import PointsService

router = APIRouter(prefix="/points")


# ============================================
# Helper functions
# ============================================


async def _resolve_team_id(user_id: str, team_id_param: Optional[str] = None) -> str:
    """
    Resolve the team ID for a user.

    If *team_id_param* is provided, return it directly.  Otherwise look up
    the user's first team from the ``team_members`` table.  If the user has
    no team at all, a personal workspace team is auto-created with free
    quota so that every user can use the Points system out of the box.

    Args:
        user_id: UUID of the authenticated user.
        team_id_param: Optional team ID passed as a query parameter.

    Returns:
        The resolved team ID string.
    """
    if team_id_param:
        return team_id_param

    try:
        client = await get_async_supabase_admin()
        result = (
            await client.table("team_members")
            .select("team_id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return str(result.data[0]["team_id"])
    except Exception as e:
        logger.error(f"Failed to resolve team_id for user {user_id}: {e}")

    # Auto-create a personal team for the user
    team_id = await _auto_create_personal_team(user_id)
    return team_id


async def _auto_create_personal_team(user_id: str) -> str:
    """
    Create a personal workspace team for a user who has none.

    Inserts into ``teams`` (the DB trigger auto-adds the owner to
    ``team_members``), then provisions a free-tier quota.

    Returns:
        The new team ID as a string.
    """
    client = await get_async_supabase_admin()

    # Get username for team name
    username = "User"
    try:
        profile = (
            await client.table("user_profiles")
            .select("username")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        if profile.data:
            username = profile.data[0].get("username") or "User"
        else:
            # user_profiles might be missing — try auth metadata
            user_resp = await client.auth.admin.get_user_by_id(user_id)
            if user_resp and user_resp.user:
                meta = user_resp.user.user_metadata or {}
                username = (
                    meta.get("username")
                    or (user_resp.user.email or "User").split("@")[0]
                )
                # Also create the missing user_profiles row
                await client.table("user_profiles").upsert(
                    {"id": user_id, "username": username, "role": "user"}
                ).execute()
    except Exception as e:
        logger.warning(f"Failed to resolve username for {user_id}: {e}")

    # Generate a random invite code
    invite_code = "".join(
        random.choices(string.ascii_uppercase + string.digits, k=8)
    )

    # Insert team — id uses DEFAULT generate_snowflake_id()
    team_result = (
        await client.table("teams")
        .insert(
            {
                "name": f"{username}'s Workspace",
                "owner_id": user_id,
                "invite_code": invite_code,
            }
        )
        .execute()
    )

    if not team_result.data:
        raise HTTPException(
            status_code=500,
            detail="Failed to create personal team",
        )

    team_id = str(team_result.data[0]["id"])
    logger.info(f"Auto-created personal team {team_id} for user {user_id}")

    # The DB trigger add_owner_as_member() handles team_members insertion.
    # Now provision free quota.
    svc = PointsService()
    await svc.ensure_team_quota(team_id, grant_free_points=True, user_id=user_id)

    return team_id


async def _check_admin_role(user_id: str) -> bool:
    """
    Check whether the user has the ``admin`` role in the ``user_profiles`` table.

    Args:
        user_id: UUID of the authenticated user.

    Returns:
        True if the user is an admin, False otherwise.
    """
    try:
        client = await get_async_supabase_admin()
        result = (
            await client.table("user_profiles")
            .select("role")
            .eq("id", user_id)
            .execute()
        )
        if result.data:
            return result.data[0].get("role") == "admin"
    except Exception as e:
        logger.error(f"Failed to check admin role for user {user_id}: {e}")
    return False


# ============================================
# Route endpoints
# ============================================


@router.get("/balance")
async def get_balance(
    auth: AuthDep,
    team_id: Optional[str] = Query(
        None, description="Team ID (auto-resolved if omitted)"
    ),
):
    """
    Get team points balance and storage info.

    Returns the current points balance, storage limit, storage used, and
    usage percentage for the resolved team.

    - **team_id**: Optional team ID; defaults to the user's first team.

    Authentication: Bearer Token or API Key
    """
    try:
        resolved_team_id = await _resolve_team_id(auth.user_id, team_id)
        svc = PointsService()
        data = await svc.get_balance(resolved_team_id)
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get points balance: {e}")
        raise HTTPException(status_code=500, detail="Failed to get points balance")


@router.get("/transactions")
async def get_transactions(
    auth: AuthDep,
    team_id: Optional[str] = Query(
        None, description="Team ID (auto-resolved if omitted)"
    ),
    limit: int = Query(50, ge=1, le=200, description="Number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    type: Optional[str] = Query(None, description="Filter by transaction type"),
):
    """
    Get points transaction history.

    Returns a paginated list of transaction records for the resolved team,
    ordered by creation date (most recent first).

    - **team_id**: Optional team ID; defaults to the user's first team.
    - **limit**: Page size (1-200, default 50).
    - **offset**: Number of rows to skip (default 0).
    - **type**: Optional transaction type filter (purchase, consume, refund, gift, admin_adjust).

    Authentication: Bearer Token or API Key
    """
    try:
        resolved_team_id = await _resolve_team_id(auth.user_id, team_id)
        repo = PointsRepository()
        transactions = await repo.get_transactions(
            team_id=resolved_team_id,
            limit=limit,
            offset=offset,
            type_filter=type,
        )
        return {
            "success": True,
            "count": len(transactions),
            "transactions": transactions,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get transactions: {e}")
        raise HTTPException(status_code=500, detail="Failed to get transactions")


@router.get("/pricing")
async def get_pricing(auth: AuthDep):
    """
    Get all active pricing rules.

    Returns the full list of action types and their point costs.

    Authentication: Bearer Token or API Key
    """
    try:
        repo = PointsRepository()
        pricing = await repo.get_all_pricing()
        return {"success": True, "pricing": pricing}
    except Exception as e:
        logger.error(f"Failed to get pricing: {e}")
        raise HTTPException(status_code=500, detail="Failed to get pricing")


@router.get("/usage-stats")
async def get_usage_stats(
    auth: AuthDep,
    team_id: Optional[str] = Query(
        None, description="Team ID (auto-resolved if omitted)"
    ),
):
    """
    Get team usage statistics.

    Returns aggregated stats including total consumed, total purchased,
    and a breakdown by transaction type.

    - **team_id**: Optional team ID; defaults to the user's first team.

    Authentication: Bearer Token or API Key
    """
    try:
        resolved_team_id = await _resolve_team_id(auth.user_id, team_id)
        repo = PointsRepository()
        stats = await repo.get_usage_stats(resolved_team_id)
        return {"success": True, "data": stats}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get usage stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get usage stats")


@router.get("/check")
async def check_quota(
    auth: AuthDep,
    action_type: str = Query(
        ..., description="Action type to check (e.g. video_parse)"
    ),
    count: int = Query(1, ge=1, description="Number of actions to check"),
    team_id: Optional[str] = Query(
        None, description="Team ID (auto-resolved if omitted)"
    ),
):
    """
    Pre-check whether an action is allowed.

    Performs a dry-run quota check without consuming any points, useful
    for disabling UI buttons when the user lacks sufficient balance.

    - **action_type**: The action identifier (required).
    - **count**: Multiplier for batch operations (default 1).
    - **team_id**: Optional team ID; defaults to the user's first team.

    Authentication: Bearer Token or API Key
    """
    try:
        resolved_team_id = await _resolve_team_id(auth.user_id, team_id)
        svc = PointsService()
        result = await svc.check_quota(
            team_id=resolved_team_id,
            user_id=auth.user_id,
            action_type=action_type,
            count=count,
        )

        if not result.get("allowed"):
            raise HTTPException(
                status_code=402,
                detail=result.get("reason", "Insufficient points"),
            )

        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to check quota: {e}")
        raise HTTPException(status_code=500, detail="Failed to check quota")


@router.post("/admin/adjust")
async def admin_adjust_points(
    request: PointsAdjustRequest,
    auth: AuthDep,
):
    """
    Admin: manually adjust a team's points balance.

    Adds or deducts points from a team's balance and records the
    transaction. Only users with the ``admin`` role in ``user_profiles``
    are permitted.

    - **team_id**: Target team ID.
    - **amount**: Points to adjust (positive to add, negative to deduct).
    - **description**: Reason for the adjustment.

    Authentication: Bearer Token or API Key (admin only)
    """
    # Verify admin role
    is_admin = await _check_admin_role(auth.user_id)
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: admin role required for this operation",
        )

    try:
        svc = PointsService()

        if request.amount > 0:
            result = await svc.add_points(
                team_id=request.team_id,
                amount=request.amount,
                type="admin_adjust",
                description=request.description,
                user_id=auth.user_id,
            )
        elif request.amount < 0:
            # For negative adjustments, deduct from balance directly
            repo = PointsRepository()
            team_quota = await repo.get_team_quota(request.team_id)
            if team_quota is None:
                raise HTTPException(
                    status_code=404,
                    detail="Team quota not found",
                )

            current_balance = team_quota.get("points_balance", 0)
            new_balance = current_balance + request.amount  # amount is negative
            if new_balance < 0:
                new_balance = 0

            await repo.update_points_balance(request.team_id, new_balance)
            await repo.create_transaction(
                {
                    "team_id": request.team_id,
                    "user_id": auth.user_id,
                    "amount": request.amount,
                    "balance_after": new_balance,
                    "type": "admin_adjust",
                    "reference_type": "admin_adjust",
                    "description": request.description,
                }
            )
            result = {"success": True, "new_balance": new_balance}
        else:
            raise HTTPException(
                status_code=400,
                detail="Amount must not be zero",
            )

        if not result.get("success"):
            raise HTTPException(
                status_code=500,
                detail="Failed to adjust points",
            )

        return {
            "success": True,
            "message": f"Adjusted {request.amount} points for team {request.team_id}",
            "new_balance": result.get("new_balance"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to adjust points: {e}")
        raise HTTPException(status_code=500, detail="Failed to adjust points")


@router.get("/admin/overview")
async def admin_overview(auth: AuthDep):
    """
    Admin: get system-wide points overview.

    Returns aggregated statistics including total points in system,
    total consumed, total purchased, active teams count, and total
    transactions count.

    Authentication: Bearer Token or API Key (admin only)
    """
    is_admin = await _check_admin_role(auth.user_id)
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: admin role required for this operation",
        )

    try:
        repo = PointsRepository()
        overview = await repo.get_admin_overview()
        return {"success": True, "data": overview}
    except Exception as e:
        logger.error(f"Failed to get admin overview: {e}")
        raise HTTPException(status_code=500, detail="Failed to get admin overview")
