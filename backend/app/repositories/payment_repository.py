# app/repositories/payment_repository.py

"""
Payment Repository

Data access layer for payment orders, providing CRUD operations
and query functions against the Supabase `orders` table.
Uses async Supabase client.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class PaymentRepository:
    """Payment Order Repository (async)"""

    TABLE_NAME = "orders"

    def __init__(self):
        self._client = None  # Lazy initialisation

    async def _get_client(self):
        """Get the async Supabase admin client."""
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

    async def _get_table(self):
        """Get a table reference for the orders table."""
        client = await self._get_client()
        return client.table(self.TABLE_NAME)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_order(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new payment order.

        Args:
            data: Order data dictionary. Expected keys include team_id,
                  user_id, package_id, points_amount, amount_cents,
                  currency, payment_method, etc.

        Returns:
            The created order record.
        """
        try:
            # Serialise any datetime values
            for key in ("paid_at", "expired_at", "created_at", "updated_at"):
                if key in data and isinstance(data[key], datetime):
                    data[key] = data[key].isoformat()

            table = await self._get_table()
            result = await table.insert(data).execute()
            logger.info(
                f"Created order: {result.data[0]['id'] if result.data else 'unknown'}"
            )
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            raise

    async def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get an order by its primary key.

        Args:
            order_id: UUID of the order.

        Returns:
            The order record, or None if not found.
        """
        try:
            table = await self._get_table()
            result = await table.select("*").eq("id", order_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            return None

    async def get_order_by_trade_no(self, trade_no: str) -> Optional[Dict[str, Any]]:
        """
        Get an order by third-party transaction number.

        Useful for payment callback idempotency checks -- if the trade_no
        already maps to a paid order we can skip duplicate processing.

        Args:
            trade_no: The external payment provider's transaction ID.

        Returns:
            The order record, or None if not found.
        """
        try:
            table = await self._get_table()
            result = await table.select("*").eq("trade_no", trade_no).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(f"Failed to get order by trade_no {trade_no}: {e}")
            return None

    async def update_order(self, order_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing order's fields.

        Args:
            order_id: UUID of the order to update.
            data: Dictionary of fields to update.

        Returns:
            The updated order record.
        """
        try:
            # Serialise datetime values
            for key in ("paid_at", "expired_at", "created_at", "updated_at"):
                if key in data and isinstance(data[key], datetime):
                    data[key] = data[key].isoformat()

            # Set updated_at automatically
            data["updated_at"] = datetime.now().isoformat()

            table = await self._get_table()
            result = await table.update(data).eq("id", order_id).execute()
            logger.info(f"Updated order: {order_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            logger.error(f"Failed to update order {order_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_team_orders(
        self,
        team_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get a team's order history, newest first.

        Args:
            team_id: UUID of the team.
            limit: Maximum number of records to return.
            offset: Number of records to skip (for pagination).

        Returns:
            List of order records ordered by created_at DESC.
        """
        try:
            table = await self._get_table()
            result = (
                await table.select("*")
                .eq("team_id", team_id)
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.error(f"Failed to get orders for team {team_id}: {e}")
            return []

    async def expire_pending_orders(self) -> int:
        """
        Mark pending orders whose expiry time has passed as 'expired'.

        Targets rows where payment_status = 'pending' AND expired_at < NOW().

        Returns:
            The number of orders that were expired.
        """
        try:
            now = datetime.now().isoformat()
            table = await self._get_table()
            result = (
                await table.update(
                    {
                        "payment_status": "expired",
                        "updated_at": now,
                    }
                )
                .eq("payment_status", "pending")
                .lt("expired_at", now)
                .execute()
            )
            count = len(result.data) if result.data else 0
            if count > 0:
                logger.info(f"Expired {count} pending order(s)")
            return count
        except Exception as e:
            logger.error(f"Failed to expire pending orders: {e}")
            return 0
