# app/services/points_service.py

"""
Points Service - Core Business Logic

Orchestrates all points operations on top of the PointsRepository data-access
layer, including consumption, quota checks, refunds, balance queries, and
welcome-bonus provisioning.
"""

from typing import Any, Dict, Optional

from loguru import logger

from app.repositories.points_repository import PointsRepository

# Default welcome bonus for new teams
FREE_WELCOME_POINTS = 500

# Default storage limit (5 GB)
DEFAULT_STORAGE_LIMIT_BYTES = 5_368_709_120


class PointsService:
    """High-level business logic for the points capacity system (async)."""

    def __init__(self):
        self.repo = PointsRepository()

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

        Returns:
            Dict with keys: success, points_cost, balance_after, reason.
        """
        # 1. Get pricing for this action
        pricing = await self.repo.get_pricing(action_type)
        if pricing is None:
            # No pricing configured -- allow for free
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

        # 2. Check team balance
        team_quota = await self.repo.get_team_quota(team_id)
        if team_quota is None:
            logger.warning(
                f"No team quota found for team {team_id}; denying action"
            )
            return {
                "success": False,
                "points_cost": points_cost,
                "balance_after": None,
                "reason": "Team quota not found. Please contact support.",
            }

        current_balance = team_quota.get("points_balance", 0)
        if current_balance < points_cost:
            logger.info(
                f"Insufficient balance for team {team_id}: "
                f"need {points_cost}, have {current_balance}"
            )
            return {
                "success": False,
                "points_cost": points_cost,
                "balance_after": current_balance,
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
                    logger.info(
                        f"Member {user_id} monthly limit exceeded in team "
                        f"{team_id}: limit={monthly_limit}, "
                        f"used={used_this_month}, cost={points_cost}"
                    )
                    return {
                        "success": False,
                        "points_cost": points_cost,
                        "balance_after": current_balance,
                        "reason": (
                            f"Monthly points limit exceeded. "
                            f"Limit: {monthly_limit}, "
                            f"used: {used_this_month}, "
                            f"required: {points_cost}."
                        ),
                    }

        # 4. Deduct points
        new_balance = current_balance - points_cost
        await self.repo.update_points_balance(team_id, new_balance)

        # 5. Record transaction (debit is stored as negative amount)
        await self.repo.create_transaction(
            {
                "team_id": team_id,
                "user_id": user_id,
                "amount": -points_cost,
                "balance_after": new_balance,
                "type": "consume",
                "reference_type": action_type,
                "reference_id": reference_id,
                "description": f"Consumed {points_cost} points for {action_type}",
            }
        )

        # 6. Update member usage counter
        await self.repo.increment_member_usage(team_id, user_id, points_cost)

        logger.info(
            f"Consumed {points_cost} points for team {team_id}, "
            f"user {user_id}, action {action_type}. "
            f"Balance: {current_balance} -> {new_balance}"
        )

        return {
            "success": True,
            "points_cost": points_cost,
            "balance_after": new_balance,
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
                f"add_points: no team quota for team {team_id}; "
                f"creating one first"
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
        Refund points back to a team's balance. Convenience wrapper around
        add_points with type='refund'.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user who initiated the refund.
            amount: Points to refund (positive value).
            reference_type: What kind of entity is being refunded.
            reference_id: ID of the entity being refunded.
            reason: Human-readable reason for the refund.

        Returns:
            Dict with keys: success, new_balance.
        """
        description = f"Refund: {reason} ({reference_type})"
        result = await self.add_points(
            team_id=team_id,
            amount=amount,
            type="refund",
            description=description,
            user_id=user_id,
            reference_id=reference_id,
        )
        if result["success"]:
            logger.info(
                f"Refunded {amount} points to team {team_id} "
                f"for {reference_type} (ref={reference_id}): {reason}"
            )
        return result

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
            storage_used_percent = round(
                (storage_used / storage_limit) * 100, 2
            )
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
                    "user_id": None,
                    "amount": initial_balance,
                    "balance_after": initial_balance,
                    "type": "gift",
                    "reference_type": "welcome_bonus",
                    "reference_id": None,
                    "description": (
                        f"Welcome bonus: {initial_balance} free points"
                    ),
                }
            )
            logger.info(
                f"Granted {initial_balance} welcome bonus points "
                f"to team {team_id}"
            )

        return quota
