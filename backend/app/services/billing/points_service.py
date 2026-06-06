# app/services/points_service.py

"""
Points Service - Core Business Logic

Orchestrates all points operations on top of the PointsRepository data-access
layer, including consumption, quota checks, refunds, balance queries, and
welcome-bonus provisioning.
"""

from typing import Any, Dict, Optional

from loguru import logger

from app.repositories.points_repository import get_points_repository

# Default welcome bonus for new teams
FREE_WELCOME_POINTS = 500

# Default storage limit (5 GB)
DEFAULT_STORAGE_LIMIT_BYTES = 5_368_709_120


class PointsService:
    """High-level business logic for the points capacity system (async)."""

    def __init__(self):
        self.repo = get_points_repository()

    # ------------------------------------------------------------------ #
    # Core: check + consume (atomic debit flow)
    # ------------------------------------------------------------------ #

    async def check_and_consume(
        self,
        team_id: str,
        user_id: str,
        action_type: str,
        reference_id: Optional[str] = None,
        count: int = 1,
        override_cost: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomic flow: look up pricing, verify team balance, verify member
        monthly quota, deduct points, record the transaction, and update
        the member's usage counter.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user performing the action.
            action_type: The action identifier (e.g. 'video_parse').
            reference_id: Optional reference to a related entity.
            count: Multiplier for the base cost (batch operations).
            override_cost: If provided, skip pricing lookup and use this cost directly
                          (used for Nous duration-based billing).
            description: Optional human-readable description to override the
                        generic "Consumed N points for action" message.

        Returns:
            Dict with keys: success, points_cost, balance_after, reason.
        """
        if override_cost is not None:
            points_cost = override_cost
        else:
            # 1. Get pricing for this action
            pricing = await self.repo.get_pricing(action_type)
            if pricing is None:
                logger.info(
                    f"No pricing for action '{action_type}'; allowing for free "
                    f"(team={team_id}, user={user_id})"
                )
                return {
                    "success": True,
                    "points_cost": 0,
                    "balance_after": None,
                    "reason": None,
                }
            points_cost = pricing["points_cost"] * count

        # If the action costs 0 points, short-circuit
        if points_cost == 0:
            return {
                "success": True,
                "points_cost": 0,
                "balance_after": None,
                "reason": None,
            }

        # 2. Atomic check-and-consume via Postgres RPC.
        # The RPC decrements balance + increments member usage in a single
        # transaction, preventing the double-spend race that existed when
        # we read balance then wrote it back from Python.
        rpc_result = await self.repo.consume_points_atomic(
            team_id=team_id,
            user_id=user_id,
            points_cost=points_cost,
        )
        if rpc_result is None:
            logger.error(
                f"Atomic consume RPC unavailable for team {team_id}; denying "
                f"action {action_type} to avoid unsafe fallback"
            )
            return {
                "success": False,
                "points_cost": points_cost,
                "balance_after": None,
                "reason": "Points service temporarily unavailable.",
            }

        success = bool(rpc_result.get("success"))
        balance_after = rpc_result.get("balance_after")
        reason = rpc_result.get("reason")

        if not success:
            logger.info(
                f"Consume denied for team {team_id}, user {user_id}, "
                f"action {action_type}: {reason}"
            )
            return {
                "success": False,
                "points_cost": points_cost,
                "balance_after": balance_after,
                "reason": reason,
            }

        # 3. Record transaction (debit is stored as negative amount).
        # This is outside the atomic block but failure here doesn't
        # re-credit points; log + continue so the consumption is durable.
        try:
            await self.repo.create_transaction(
                {
                    "team_id": team_id,
                    "user_id": user_id,
                    "amount": -points_cost,
                    "balance_after": balance_after,
                    "type": "consume",
                    "reference_type": action_type,
                    "reference_id": reference_id,
                    "description": description
                    or f"Consumed {points_cost} points for {action_type}",
                }
            )
        except Exception as e:
            logger.error(
                f"Failed to record consume transaction for team={team_id} "
                f"action={action_type}: {e}"
            )

        logger.info(
            f"Consumed {points_cost} points for team {team_id}, "
            f"user {user_id}, action {action_type}. "
            f"Balance after: {balance_after}"
        )

        return {
            "success": True,
            "points_cost": points_cost,
            "balance_after": balance_after,
            "reason": None,
        }

    # ------------------------------------------------------------------ #
    # Dry-run quota check (no side-effects)
    # ------------------------------------------------------------------ #

    async def check_quota(
        self,
        team_id: str,
        user_id: str,
        action_type: str,
        count: int = 1,
    ) -> Dict[str, Any]:
        """
        Same checks as check_and_consume but WITHOUT consuming points.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user.
            action_type: The action identifier.
            count: Multiplier for the base cost.

        Returns:
            Dict with keys: allowed, points_cost, current_balance, reason.
        """
        # 1. Get pricing
        pricing = await self.repo.get_pricing(action_type)
        if pricing is None:
            return {
                "allowed": True,
                "points_cost": 0,
                "current_balance": None,
                "reason": None,
            }

        points_cost = pricing["points_cost"] * count

        if points_cost == 0:
            return {
                "allowed": True,
                "points_cost": 0,
                "current_balance": None,
                "reason": None,
            }

        # 2. Check team balance
        team_quota = await self.repo.get_team_quota(team_id)
        if team_quota is None:
            return {
                "allowed": False,
                "points_cost": points_cost,
                "current_balance": 0,
                "reason": "Team quota not found. Please contact support.",
            }

        current_balance = team_quota.get("points_balance", 0)
        if current_balance < points_cost:
            return {
                "allowed": False,
                "points_cost": points_cost,
                "current_balance": current_balance,
                "reason": (
                    f"Insufficient points balance. "
                    f"Required: {points_cost}, available: {current_balance}."
                ),
            }

        # 3. Check member monthly quota
        member_quota = await self.repo.get_member_quota(team_id, user_id)
        if member_quota is not None:
            monthly_limit = member_quota.get("monthly_points_limit")
            if monthly_limit is not None:
                used_this_month = member_quota.get("points_used_this_month", 0)
                if used_this_month + points_cost > monthly_limit:
                    return {
                        "allowed": False,
                        "points_cost": points_cost,
                        "current_balance": current_balance,
                        "reason": (
                            f"Monthly points limit exceeded. "
                            f"Limit: {monthly_limit}, "
                            f"used: {used_this_month}, "
                            f"required: {points_cost}."
                        ),
                    }

        return {
            "allowed": True,
            "points_cost": points_cost,
            "current_balance": current_balance,
            "reason": None,
        }

    # ------------------------------------------------------------------ #
    # Storage check
    # ------------------------------------------------------------------ #

    async def check_storage(
        self,
        team_id: str,
        additional_bytes: int = 0,
    ) -> Dict[str, Any]:
        """
        Check whether a team has sufficient storage capacity.

        Args:
            team_id: UUID of the team.
            additional_bytes: Number of bytes the pending operation needs.

        Returns:
            Dict with keys: allowed, reason, storage_used, storage_limit.
        """
        team_quota = await self.repo.get_team_quota(team_id)
        if team_quota is None:
            return {
                "allowed": False,
                "reason": "Team quota not found. Please contact support.",
                "storage_used": 0,
                "storage_limit": 0,
            }

        storage_used = team_quota.get("storage_used_bytes", 0)
        storage_limit = team_quota.get(
            "storage_limit_bytes", DEFAULT_STORAGE_LIMIT_BYTES
        )

        if storage_used + additional_bytes > storage_limit:
            return {
                "allowed": False,
                "reason": (
                    f"Storage limit exceeded. "
                    f"Used: {storage_used} bytes, "
                    f"limit: {storage_limit} bytes, "
                    f"requested: {additional_bytes} bytes."
                ),
                "storage_used": storage_used,
                "storage_limit": storage_limit,
            }

        return {
            "allowed": True,
            "reason": None,
            "storage_used": storage_used,
            "storage_limit": storage_limit,
        }

    # ------------------------------------------------------------------ #
    # Credit points (purchase / gift / admin_adjust)
    # ------------------------------------------------------------------ #

    async def add_points(
        self,
        team_id: str,
        amount: int,
        type: str,
        description: str,
        user_id: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Credit points to a team's balance.

        Args:
            team_id: UUID of the team.
            amount: Number of points to add (must be positive).
            type: Transaction type ('purchase', 'gift', 'admin_adjust', 'refund').
            description: Human-readable reason.
            user_id: Optional user who triggered the operation.
            reference_id: Optional reference (e.g. order ID).

        Returns:
            Dict with keys: success, new_balance.
        """
        if amount <= 0:
            logger.warning(
                f"add_points called with non-positive amount {amount} "
                f"for team {team_id}"
            )
            return {"success": False, "new_balance": None}

        team_quota = await self.repo.get_team_quota(team_id)
        if team_quota is None:
            logger.warning(
                f"add_points: no team quota for team {team_id}; " f"creating one first"
            )
            team_quota = await self.repo.create_team_quota(team_id)

        current_balance = team_quota.get("points_balance", 0)
        new_balance = current_balance + amount

        await self.repo.update_points_balance(team_id, new_balance)

        await self.repo.create_transaction(
            {
                "team_id": team_id,
                "user_id": user_id,
                "amount": amount,
                "balance_after": new_balance,
                "type": type,
                "reference_type": type,
                "reference_id": reference_id,
                "description": description,
            }
        )

        logger.info(
            f"Added {amount} points ({type}) to team {team_id}. "
            f"Balance: {current_balance} -> {new_balance}"
        )

        return {"success": True, "new_balance": new_balance}

    # ------------------------------------------------------------------ #
    # Refund wrapper
    # ------------------------------------------------------------------ #

    async def refund_points(
        self,
        team_id: str,
        user_id: str,
        amount: int,
        reference_type: str,
        reference_id: Optional[str] = None,
        reason: str = "Operation failed",
    ) -> Dict[str, Any]:
        """
        Atomically + idempotently refund points. Safe to call from Celery
        retry paths: the RPC enforces at-most-one refund per
        (team_id, reference_type, reference_id) via a partial unique index.

        Returns:
            Dict with keys: success, new_balance, already_refunded.
        """
        description = f"Refund: {reason} ({reference_type})"

        rpc_result = await self.repo.refund_points_atomic(
            team_id=team_id,
            user_id=user_id,
            amount=amount,
            reference_type=reference_type,
            reference_id=reference_id,
            description=description,
        )

        if rpc_result is None:
            logger.error(
                f"Refund RPC unavailable for team {team_id}, "
                f"ref={reference_type}:{reference_id}"
            )
            return {
                "success": False,
                "new_balance": None,
                "already_refunded": False,
            }

        success = bool(rpc_result.get("success"))
        already_refunded = bool(rpc_result.get("already_refunded"))
        new_balance = rpc_result.get("new_balance")

        if success and not already_refunded:
            logger.info(
                f"Refunded {amount} points to team {team_id} "
                f"for {reference_type} (ref={reference_id}): {reason}"
            )
        elif already_refunded:
            logger.info(
                f"Refund for {reference_type}:{reference_id} already "
                f"applied — no-op"
            )

        return {
            "success": success,
            "new_balance": new_balance,
            "already_refunded": already_refunded,
        }

    # ------------------------------------------------------------------ #
    # Reclaim daily gift (debit flow)
    # ------------------------------------------------------------------ #

    async def reclaim_daily_gift(
        self,
        team_id: str,
        amount: int,
        user_id: str | None = None,
        description: str = "Daily gift reclaim - unused points",
    ) -> Dict[str, Any]:
        """Reclaim unused daily gift points from team balance."""
        if amount <= 0:
            return {"success": True, "reclaimed": 0}

        team_quota = await self.repo.get_team_quota(team_id)
        if team_quota is None:
            return {"success": False, "reclaimed": 0}

        current_balance = team_quota.get("points_balance", 0)
        actual_reclaim = min(amount, current_balance)

        if actual_reclaim <= 0:
            return {"success": True, "reclaimed": 0}

        new_balance = current_balance - actual_reclaim
        await self.repo.update_points_balance(team_id, new_balance)

        await self.repo.create_transaction(
            {
                "team_id": team_id,
                "user_id": user_id,
                "amount": -actual_reclaim,
                "balance_after": new_balance,
                "type": "daily_gift_reclaim",
                "reference_type": "daily_gift_reclaim",
                "description": description,
            }
        )

        logger.info(
            f"Reclaimed {actual_reclaim} daily gift points from team {team_id}. "
            f"Balance: {current_balance} -> {new_balance}"
        )

        return {"success": True, "reclaimed": actual_reclaim}

    # ------------------------------------------------------------------ #
    # Balance query
    # ------------------------------------------------------------------ #

    async def get_balance(self, team_id: str) -> Dict[str, Any]:
        """
        Get the current balance and storage usage for a team.

        Args:
            team_id: UUID of the team.

        Returns:
            Dict with keys: team_id, points_balance, storage_limit_bytes,
            storage_used_bytes, storage_used_percent.
        """
        team_quota = await self.repo.get_team_quota(team_id)
        if team_quota is None:
            return {
                "team_id": team_id,
                "points_balance": 0,
                "storage_limit_bytes": 0,
                "storage_used_bytes": 0,
                "storage_used_percent": 0.0,
            }

        storage_limit = team_quota.get(
            "storage_limit_bytes", DEFAULT_STORAGE_LIMIT_BYTES
        )
        storage_used = team_quota.get("storage_used_bytes", 0)

        if storage_limit > 0:
            storage_used_percent = round((storage_used / storage_limit) * 100, 2)
        else:
            storage_used_percent = 0.0

        return {
            "team_id": team_id,
            "points_balance": team_quota.get("points_balance", 0),
            "storage_limit_bytes": storage_limit,
            "storage_used_bytes": storage_used,
            "storage_used_percent": storage_used_percent,
        }

    # ------------------------------------------------------------------ #
    # Ensure team quota exists (with optional welcome bonus)
    # ------------------------------------------------------------------ #

    async def ensure_team_quota(
        self,
        team_id: str,
        grant_free_points: bool = True,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ensure a team has a quota record. If no record exists, create one
        with the default storage limit. Optionally grant a welcome bonus of
        free points.

        Args:
            team_id: UUID of the team.
            grant_free_points: Whether to grant the welcome bonus when
                creating a new record (default True).

        Returns:
            The team quota record (existing or newly created).
        """
        existing = await self.repo.get_team_quota(team_id)
        if existing is not None:
            logger.debug(f"Team quota already exists for team {team_id}")
            return existing

        # Create a new quota record
        initial_balance = FREE_WELCOME_POINTS if grant_free_points else 0
        quota = await self.repo.create_team_quota(
            team_id=team_id,
            points_balance=initial_balance,
            storage_limit_bytes=DEFAULT_STORAGE_LIMIT_BYTES,
        )

        logger.info(
            f"Created team quota for team {team_id} "
            f"(initial balance={initial_balance})"
        )

        # Record a gift transaction for the welcome bonus
        if grant_free_points and initial_balance > 0:
            await self.repo.create_transaction(
                {
                    "team_id": team_id,
                    "user_id": user_id,
                    "amount": initial_balance,
                    "balance_after": initial_balance,
                    "type": "gift",
                    "reference_type": "welcome_bonus",
                    "reference_id": None,
                    "description": (f"Welcome bonus: {initial_balance} free points"),
                }
            )
            logger.info(
                f"Granted {initial_balance} welcome bonus points " f"to team {team_id}"
            )

        return quota
