# app/repositories/points_repository.py

"""
Points Repository

Data access layer for the points capacity system, covering point pricing,
packages, team quotas, member quotas, and point transactions.
Uses async Supabase client.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class PointsRepository:
    """Points system data access (async)"""

    TABLE_PRICING = "point_pricing"
    TABLE_PACKAGES = "point_packages"
    TABLE_TEAM_QUOTAS = "team_quotas"
    TABLE_MEMBER_QUOTAS = "member_quotas"
    TABLE_TRANSACTIONS = "point_transactions"

    def __init__(self):
        pass

    async def _get_client(self):
        """Get async client (loop-aware, safe for Celery workers)."""
        return await get_async_supabase_admin()

    # ------------------------------------------------------------------ #
    # Point Pricing
    # ------------------------------------------------------------------ #

    async def get_pricing(self, action_type: str) -> Optional[Dict[str, Any]]:
        """
        Get the cost definition for a specific action type.

        Args:
            action_type: The action identifier (e.g. 'video_parse').

        Returns:
            Pricing row dict or None if not found / inactive.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_PRICING)
                .select("*")
                .eq("action_type", action_type)
                .eq("is_active", True)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get pricing for {action_type}: {e}")
            return None

    async def get_all_pricing(self) -> List[Dict[str, Any]]:
        """
        Get all active pricing rules.

        Returns:
            List of pricing row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_PRICING)
                .select("*")
                .eq("is_active", True)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get all pricing: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Point Packages
    # ------------------------------------------------------------------ #

    async def get_active_packages(self) -> List[Dict[str, Any]]:
        """
        Get all active purchasable packages ordered by sort_order.

        Returns:
            List of package row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_PACKAGES)
                .select("*")
                .eq("is_active", True)
                .order("sort_order", desc=False)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get active packages: {e}")
            return []

    async def get_package_by_id(self, package_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific package by its UUID.

        Args:
            package_id: UUID of the package.

        Returns:
            Package row dict or None.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_PACKAGES)
                .select("*")
                .eq("id", package_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get package {package_id}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Team Quotas
    # ------------------------------------------------------------------ #

    async def get_team_quota(self, team_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a team's quota record (balance + storage).

        Args:
            team_id: UUID of the team.

        Returns:
            Team quota row dict or None.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_TEAM_QUOTAS)
                .select("*")
                .eq("team_id", team_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get team quota for {team_id}: {e}")
            return None

    async def create_team_quota(
        self,
        team_id: str,
        points_balance: int = 0,
        storage_limit_bytes: int = 5368709120,
    ) -> Dict[str, Any]:
        """
        Create a new team quota record with initial values.

        Args:
            team_id: UUID of the team.
            points_balance: Initial points balance (default 0).
            storage_limit_bytes: Storage cap in bytes (default 5 GB).

        Returns:
            Created team quota row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_TEAM_QUOTAS)
                .insert(
                    {
                        "team_id": team_id,
                        "points_balance": points_balance,
                        "storage_limit_bytes": storage_limit_bytes,
                        "storage_used_bytes": 0,
                    }
                )
                .execute()
            )
            logger.info(f"Created team quota for team {team_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create team quota for {team_id}: {e}")
            raise

    async def update_points_balance(
        self, team_id: str, new_balance: int
    ) -> Dict[str, Any]:
        """
        Set the points balance for a team.

        Args:
            team_id: UUID of the team.
            new_balance: New absolute balance value.

        Returns:
            Updated team quota row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_TEAM_QUOTAS)
                .update({"points_balance": new_balance})
                .eq("team_id", team_id)
                .execute()
            )
            logger.info(f"Updated points balance for team {team_id} to {new_balance}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update points balance for {team_id}: {e}")
            raise

    async def update_storage_used(
        self, team_id: str, storage_used_bytes: int
    ) -> Dict[str, Any]:
        """
        Update the storage used counter for a team.

        Args:
            team_id: UUID of the team.
            storage_used_bytes: New storage used value in bytes.

        Returns:
            Updated team quota row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_TEAM_QUOTAS)
                .update({"storage_used_bytes": storage_used_bytes})
                .eq("team_id", team_id)
                .execute()
            )
            logger.info(
                f"Updated storage used for team {team_id} to {storage_used_bytes}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update storage used for {team_id}: {e}")
            raise

    # ------------------------------------------------------------------ #
    # Member Quotas
    # ------------------------------------------------------------------ #

    async def get_member_quota(
        self, team_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific member's quota within a team.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user.

        Returns:
            Member quota row dict or None.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_MEMBER_QUOTAS)
                .select("*")
                .eq("team_id", team_id)
                .eq("user_id", user_id)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(
                f"Failed to get member quota for user {user_id} in team {team_id}: {e}"
            )
            return None

    async def upsert_member_quota(
        self, team_id: str, user_id: str, monthly_points_limit: Optional[int]
    ) -> Dict[str, Any]:
        """
        Create or update a member's monthly points limit.

        Uses upsert on the (team_id, user_id) unique constraint.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user.
            monthly_points_limit: Monthly cap (None = unlimited).

        Returns:
            Upserted member quota row dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_MEMBER_QUOTAS)
                .upsert(
                    {
                        "team_id": team_id,
                        "user_id": user_id,
                        "monthly_points_limit": monthly_points_limit,
                    },
                    on_conflict="team_id,user_id",
                )
                .execute()
            )
            logger.info(f"Upserted member quota for user {user_id} in team {team_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(
                f"Failed to upsert member quota for user {user_id} in team {team_id}: {e}"
            )
            raise

    async def increment_member_usage(
        self, team_id: str, user_id: str, points: int
    ) -> None:
        """
        Increment a member's monthly usage counter by the given points.

        Fetches the current value and writes back the incremented value.

        Args:
            team_id: UUID of the team.
            user_id: UUID of the user.
            points: Number of points to add to the usage counter.
        """
        try:
            client = await self._get_client()
            # Fetch current usage
            current = await self.get_member_quota(team_id, user_id)
            if current is None:
                logger.warning(
                    f"No member quota found for user {user_id} in team {team_id}, "
                    f"creating one before incrementing"
                )
                await self.upsert_member_quota(team_id, user_id, None)
                current_usage = 0
            else:
                current_usage = current.get("points_used_this_month", 0)

            new_usage = current_usage + points
            await (
                client.table(self.TABLE_MEMBER_QUOTAS)
                .update({"points_used_this_month": new_usage})
                .eq("team_id", team_id)
                .eq("user_id", user_id)
                .execute()
            )
            logger.info(
                f"Incremented usage for user {user_id} in team {team_id} "
                f"by {points} (now {new_usage})"
            )
        except Exception as e:
            logger.error(
                f"Failed to increment member usage for user {user_id} "
                f"in team {team_id}: {e}"
            )
            raise

    async def get_team_member_quotas(self, team_id: str) -> List[Dict[str, Any]]:
        """
        Get all member quotas for a team.

        Args:
            team_id: UUID of the team.

        Returns:
            List of member quota row dicts.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_MEMBER_QUOTAS)
                .select("*")
                .eq("team_id", team_id)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get member quotas for team {team_id}: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Point Transactions
    # ------------------------------------------------------------------ #

    async def create_transaction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert a new point transaction (ledger entry).

        Args:
            data: Transaction dict with keys matching point_transactions columns
                  (team_id, user_id, amount, balance_after, type, etc.).

        Returns:
            Created transaction row dict.
        """
        try:
            client = await self._get_client()
            result = await client.table(self.TABLE_TRANSACTIONS).insert(data).execute()
            logger.info(
                f"Created transaction for team {data.get('team_id')}: "
                f"{data.get('type')} {data.get('amount')}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create transaction: {e}")
            raise

    async def get_transactions(
        self,
        team_id: str,
        limit: int = 50,
        offset: int = 0,
        type_filter: Optional[str] = None,
        reference_type_filter: Optional[str] = None,
        search: Optional[str] = None,
        days: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get transaction history for a team with pagination and optional filters.

        Args:
            team_id: UUID of the team.
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.
            type_filter: Optional transaction type filter
                         (e.g. 'purchase', 'consume').
            reference_type_filter: Optional reference_type (action type) filter
                                   (e.g. 'ai_transcription', 'ai_summary').
            search: Optional text to search in the description field (case-insensitive).
            days: Optional filter to last N days.

        Returns:
            List of transaction row dicts ordered by created_at DESC.
        """
        try:
            client = await self._get_client()
            query = (
                client.table(self.TABLE_TRANSACTIONS).select("*").eq("team_id", team_id)
            )
            if type_filter:
                query = query.eq("type", type_filter)
            if reference_type_filter:
                query = query.eq("reference_type", reference_type_filter)
            if search:
                query = query.ilike("description", f"%{search}%")
            if days is not None:
                from datetime import datetime, timedelta, timezone

                cutoff = (
                    datetime.now(timezone.utc) - timedelta(days=days)
                ).isoformat()
                query = query.gte("created_at", cutoff)
            query = query.order("created_at", desc=True)
            query = query.range(offset, offset + limit - 1)
            result = await query.execute()
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get transactions for team {team_id}: {e}")
            return []

    async def get_admin_overview(self) -> Dict[str, Any]:
        """
        Aggregate system-wide points statistics for admin overview.

        Returns:
            Dict with total_points_in_system, total_consumed, total_purchased,
            active_teams_count, total_transactions_count.
        """
        try:
            client = await self._get_client()

            # Fetch all team quotas for balance sum and active count
            quotas_result = await (
                client.table(self.TABLE_TEAM_QUOTAS).select("points_balance").execute()
            )
            quotas = quotas_result.data or []
            total_points_in_system = sum(q.get("points_balance", 0) for q in quotas)
            active_teams_count = len(quotas)

            # Fetch all transactions for aggregation
            txn_result = await (
                client.table(self.TABLE_TRANSACTIONS).select("amount,type").execute()
            )
            txns = txn_result.data or []
            total_transactions_count = len(txns)

            total_consumed = 0
            total_purchased = 0
            for txn in txns:
                amount = txn.get("amount", 0)
                txn_type = txn.get("type", "")
                if amount < 0:
                    total_consumed += abs(amount)
                if txn_type == "purchase" and amount > 0:
                    total_purchased += amount

            return {
                "total_points_in_system": total_points_in_system,
                "total_consumed": total_consumed,
                "total_purchased": total_purchased,
                "active_teams_count": active_teams_count,
                "total_transactions_count": total_transactions_count,
            }
        except Exception as e:
            logger.error(f"Failed to get admin overview: {e}")
            return {
                "total_points_in_system": 0,
                "total_consumed": 0,
                "total_purchased": 0,
                "active_teams_count": 0,
                "total_transactions_count": 0,
            }

    async def get_usage_stats(self, team_id: str) -> Dict[str, Any]:
        """
        Get aggregated usage statistics for a team.

        Returns a dict with:
            - total_consumed: Sum of all debit (negative) amounts.
            - total_purchased: Sum of all credit amounts from purchases.
            - by_type: Breakdown of totals keyed by transaction type.

        Args:
            team_id: UUID of the team.

        Returns:
            Aggregated stats dict.
        """
        try:
            client = await self._get_client()
            result = (
                await client.table(self.TABLE_TRANSACTIONS)
                .select("amount,type")
                .eq("team_id", team_id)
                .execute()
            )
            rows = result.data or []

            total_consumed = 0
            total_purchased = 0
            by_type: Dict[str, int] = {}

            for row in rows:
                amount = row.get("amount", 0)
                txn_type = row.get("type", "unknown")

                # Aggregate by type
                by_type[txn_type] = by_type.get(txn_type, 0) + amount

                # Debits are negative amounts
                if amount < 0:
                    total_consumed += abs(amount)

                # Credits from purchases
                if txn_type == "purchase" and amount > 0:
                    total_purchased += amount

            return {
                "total_consumed": total_consumed,
                "total_purchased": total_purchased,
                "by_type": by_type,
            }
        except Exception as e:
            logger.error(f"Failed to get usage stats for team {team_id}: {e}")
            return {
                "total_consumed": 0,
                "total_purchased": 0,
                "by_type": {},
            }
