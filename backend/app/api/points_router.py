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

from app.core.admin_deps import AdminAuthDep
from app.core.deps import AuthDep
from app.core.scope_guards import _is_team_member
from app.db.supabase_client import get_async_supabase_admin
from app.repositories.points_repository import get_points_repository
from app.schemas.envelope import Envelope
from app.schemas.points import (
    PointsAdjustRequest,
    PointsBalance,
    PointsPricingResponse,
    PointsQuotaCheck,
    PointsTransactionsResponse,
    PointsUsageStats,
)
from app.services.billing.points_service import PointsService

router = APIRouter(prefix="/points")


# ============================================
# Helper functions
# ============================================


async def _resolve_team_id(user_id: str, team_id_param: Optional[str] = None) -> str:
    """
    Resolve the team ID for a user.

    If *team_id_param* is provided, return it once the user is a member of
    that team (403 otherwise — before this check any signed-in user could
    read any team's balance and ledger by passing its id).  Otherwise look up
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
        if not team_id_param.isdigit() or not await _is_team_member(
            team_id_param, user_id
        ):
            raise HTTPException(
                status_code=403, detail="You are not a member of this team"
            )
        return team_id_param

    try:
        from sqlalchemy import select

        from app.db.session import read_scope
        from app.models import TeamMembers, Teams

        # Prefer personal team
        async with read_scope() as session:
            personal_id = (
                await session.execute(
                    select(Teams.id)
                    .where(Teams.owner_id == user_id)
                    .where(Teams.kind == "personal")
                    .limit(1)
                )
            ).scalar()
        if personal_id is not None:
            return str(personal_id)

        # Fallback: any team membership
        async with read_scope() as session:
            member_team = (
                await session.execute(
                    select(TeamMembers.team_id)
                    .where(TeamMembers.user_id == user_id)
                    .limit(1)
                )
            ).scalar()
        if member_team is not None:
            return str(member_team)
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
    from sqlalchemy import insert, select

    from app.db.session import read_scope, write_scope
    from app.models import Teams, UserProfiles

    # Check if personal team already exists (prevent duplicates)
    async with read_scope() as session:
        existing_id = (
            await session.execute(
                select(Teams.id)
                .where(Teams.owner_id == user_id)
                .where(Teams.kind == "personal")
                .limit(1)
            )
        ).scalar()
    if existing_id is not None:
        team_id = str(existing_id)
        logger.info(f"Found existing personal team {team_id} for user {user_id}")
        svc = PointsService()
        await svc.ensure_team_quota(team_id, grant_free_points=True, user_id=user_id)
        return team_id

    # Get username for team name
    username = "User"
    try:
        async with read_scope() as session:
            prof = (
                await session.execute(
                    select(UserProfiles.username)
                    .where(UserProfiles.id == user_id)
                    .limit(1)
                )
            ).first()
        if prof is not None:
            username = prof[0] or "User"
        else:
            # user_profiles might be missing — try auth metadata. This is a
            # GoTrue admin API call (NOT PostgREST), so it stays on the client.
            client = await get_async_supabase_admin()
            user_resp = await client.auth.admin.get_user_by_id(user_id)
            if user_resp and user_resp.user:
                meta = user_resp.user.user_metadata or {}
                username = (
                    meta.get("username")
                    or (user_resp.user.email or "User").split("@")[0]
                )
                # Also create the missing user_profiles row (upsert on PK).
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                async with write_scope() as session:
                    stmt = pg_insert(UserProfiles).values(
                        id=user_id, username=username, role="user"
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[UserProfiles.id],
                        set_={"username": username, "role": "user"},
                    )
                    await session.execute(stmt)
    except Exception as e:
        logger.warning(f"Failed to resolve username for {user_id}: {e}")

    # Generate a random invite code
    invite_code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

    # Insert team — id uses DEFAULT generate_snowflake_id(). The
    # add_owner_as_member() trigger fires on commit, same as before.
    async with write_scope() as session:
        new_team_id = (
            await session.execute(
                insert(Teams)
                .values(
                    name=f"{username}'s Workspace",
                    owner_id=user_id,
                    invite_code=invite_code,
                    kind="personal",
                )
                .returning(Teams.id)
            )
        ).scalar()

    if new_team_id is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to create personal team",
        )

    team_id = str(new_team_id)
    logger.info(f"Auto-created personal team {team_id} for user {user_id}")

    # The DB trigger add_owner_as_member() handles team_members insertion.
    # Now provision free quota.
    svc = PointsService()
    await svc.ensure_team_quota(team_id, grant_free_points=True, user_id=user_id)

    return team_id


# ============================================
# Route endpoints
# ============================================


@router.get("/balance", response_model=Envelope[PointsBalance])
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


@router.get("/transactions", response_model=PointsTransactionsResponse)
async def get_transactions(
    auth: AuthDep,
    team_id: Optional[str] = Query(
        None, description="Team ID (auto-resolved if omitted)"
    ),
    limit: int = Query(50, ge=1, le=200, description="Number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    type: Optional[str] = Query(None, description="Filter by transaction type"),
    reference_type: Optional[str] = Query(
        None, description="Filter by reference_type (action type)"
    ),
    search: Optional[str] = Query(None, description="Search in description text"),
    days: Optional[int] = Query(
        None, ge=1, le=365, description="Filter to last N days"
    ),
):
    """
    Get points transaction history.

    Returns a paginated list of transaction records for the resolved team,
    ordered by creation date (most recent first).

    - **team_id**: Optional team ID; defaults to the user's first team.
    - **limit**: Page size (1-200, default 50).
    - **offset**: Number of rows to skip (default 0).
    - **type**: Optional transaction type filter (purchase, consume, refund, gift, admin_adjust).
    - **reference_type**: Optional action type filter (e.g. ai_transcription, ai_summary, video_parse).
    - **search**: Optional text search in the description field.
    - **days**: Optional filter to last N days (e.g. 7, 30).

    Authentication: Bearer Token or API Key
    """
    try:
        resolved_team_id = await _resolve_team_id(auth.user_id, team_id)
        repo = get_points_repository()
        transactions = await repo.get_transactions(
            team_id=resolved_team_id,
            limit=limit,
            offset=offset,
            type_filter=type,
            reference_type_filter=reference_type,
            search=search,
            days=days,
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


@router.get("/pricing", response_model=PointsPricingResponse)
async def get_pricing(auth: AuthDep):
    """
    Get all active pricing rules.

    Returns the full list of action types and their point costs.

    Authentication: Bearer Token or API Key
    """
    try:
        repo = get_points_repository()
        pricing = await repo.get_all_pricing()
        return {"success": True, "pricing": pricing}
    except Exception as e:
        logger.error(f"Failed to get pricing: {e}")
        raise HTTPException(status_code=500, detail="Failed to get pricing")


@router.get("/usage-stats", response_model=Envelope[PointsUsageStats])
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
        repo = get_points_repository()
        stats = await repo.get_usage_stats(resolved_team_id)
        return {"success": True, "data": stats}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get usage stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get usage stats")


@router.get("/check", response_model=Envelope[PointsQuotaCheck])
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
    auth: AdminAuthDep,
):
    """
    Admin: manually adjust a team's points balance.

    Adds or deducts points from a team's balance and records the
    transaction. Only users with the ``admin`` role in ``user_profiles``
    are permitted.

    - **team_id**: Target team ID.
    - **amount**: Points to adjust (positive to add, negative to deduct).
    - **description**: Reason for the adjustment.

    Authentication: Bearer Token or API Key (platform admin only, via
    ``get_admin_auth`` like every other ``/admin/`` route)
    """
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
            repo = get_points_repository()
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
