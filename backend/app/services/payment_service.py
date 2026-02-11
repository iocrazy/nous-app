# app/services/payment_service.py

"""
Payment Service - Order Creation & Callback Handling

Orchestrates the payment workflow: creating orders from purchasable
packages, handling payment-provider callbacks, crediting points on
successful payment, and querying order state.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from loguru import logger

from app.repositories.payment_repository import PaymentRepository
from app.repositories.points_repository import PointsRepository
from app.services.points_service import PointsService


class PaymentService:
    """High-level business logic for payment orders (async)."""

    def __init__(self):
        self.payment_repo = PaymentRepository()
        self.points_repo = PointsRepository()
        self.points_service = PointsService()

    # ------------------------------------------------------------------ #
    # Create Order
    # ------------------------------------------------------------------ #

    async def create_order(
        self,
        team_id: str,
        user_id: str,
        package_id: str,
        payment_method: str,
    ) -> Dict[str, Any]:
        """
        Create a new payment order for a point package purchase.

        Steps:
        1. Validate that the package exists and is active.
        2. Generate a unique trade number.
        3. Persist the order with status ``pending``.
        4. Return the order data including a placeholder payment URL.

        Args:
            team_id: UUID of the team that will receive points.
            user_id: UUID of the user creating the order.
            package_id: UUID of the point package to purchase.
            payment_method: ``"wechat"`` or ``"alipay"``.

        Returns:
            ``{success: True, data: <order dict>}`` on success, or
            ``{success: False, error: <message>}`` on failure.
        """
        try:
            # 1. Validate package exists and is active
            package = await self.points_repo.get_package_by_id(package_id)
            if package is None:
                return {"success": False, "error": "Package not found"}
            if not package.get("is_active", False):
                return {"success": False, "error": "Package is no longer available"}

            # 2. Generate trade number: MH{datetime}_{uuid_hex8}
            now = datetime.now(timezone.utc)
            trade_no = f"MH{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"

            # 3. Build order data
            # Order expires in 30 minutes
            expired_at = datetime.fromtimestamp(
                now.timestamp() + 30 * 60, tz=timezone.utc
            )

            # Placeholder payment URL
            payment_url = (
                f"https://payment.placeholder/{payment_method}/{trade_no}"
            )

            order_data = {
                "team_id": team_id,
                "user_id": user_id,
                "package_id": package_id,
                "points_amount": package["points_amount"],
                "amount_cents": package["price_cents"],
                "currency": package.get("currency", "CNY"),
                "payment_method": payment_method,
                "payment_status": "pending",
                "payment_url": payment_url,
                "trade_no": trade_no,
                "expired_at": expired_at,
            }

            # 4. Persist to DB
            order = await self.payment_repo.create_order(order_data)

            logger.info(
                f"Created order {order.get('id')} (trade_no={trade_no}) "
                f"for team {team_id}, package {package_id}, "
                f"method={payment_method}"
            )

            return {"success": True, "data": order}

        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # Handle Payment Callback
    # ------------------------------------------------------------------ #

    async def handle_callback(
        self,
        trade_no: str,
        payment_method: str,
        paid: bool = True,
    ) -> Dict[str, Any]:
        """
        Process a payment provider callback for a given trade number.

        Handles idempotency (duplicate callbacks), status validation, and
        point crediting on successful payment.

        Args:
            trade_no: The trade number identifying the order.
            payment_method: ``"wechat"`` or ``"alipay"`` (for logging).
            paid: Whether the payment was successful.

        Returns:
            ``{success, message, points_added}`` dict.
        """
        try:
            # 1. Find order by trade_no
            order = await self.payment_repo.get_order_by_trade_no(trade_no)
            if order is None:
                logger.warning(
                    f"Callback for unknown trade_no={trade_no} "
                    f"(method={payment_method})"
                )
                return {
                    "success": False,
                    "message": "Order not found",
                    "points_added": 0,
                }

            order_id = order["id"]
            current_status = order.get("payment_status", "")

            # 2. Idempotency: already paid
            if current_status == "paid":
                logger.info(
                    f"Duplicate callback for order {order_id} "
                    f"(trade_no={trade_no}): already processed"
                )
                return {
                    "success": True,
                    "message": "Already processed",
                    "points_added": 0,
                }

            # 3. Only pending orders can transition
            if current_status != "pending":
                logger.warning(
                    f"Callback for order {order_id} rejected: "
                    f"current status is '{current_status}'"
                )
                return {
                    "success": False,
                    "message": f"Order status is '{current_status}', cannot process",
                    "points_added": 0,
                }

            # 4. Payment failed
            if not paid:
                await self.payment_repo.update_order(
                    order_id, {"payment_status": "failed"}
                )
                logger.info(
                    f"Order {order_id} marked as failed "
                    f"(trade_no={trade_no})"
                )
                return {
                    "success": True,
                    "message": "Order marked as failed",
                    "points_added": 0,
                }

            # 5. Payment succeeded -- mark paid and credit points
            paid_at = datetime.now(timezone.utc)
            await self.payment_repo.update_order(
                order_id,
                {
                    "payment_status": "paid",
                    "paid_at": paid_at,
                },
            )

            points_amount = order.get("points_amount", 0)
            team_id = order["team_id"]
            user_id = order.get("user_id")

            # Credit points via PointsService
            add_result = await self.points_service.add_points(
                team_id=team_id,
                amount=points_amount,
                type="purchase",
                description=(
                    f"Purchased {points_amount} points "
                    f"(order {order_id}, trade_no {trade_no})"
                ),
                user_id=user_id,
                reference_id=order_id,
            )

            if add_result.get("success"):
                logger.info(
                    f"Order {order_id} paid: credited {points_amount} points "
                    f"to team {team_id}"
                )
            else:
                logger.error(
                    f"Order {order_id} paid but failed to credit points: "
                    f"{add_result}"
                )

            return {
                "success": True,
                "message": "Payment processed successfully",
                "points_added": points_amount,
            }

        except Exception as e:
            logger.error(
                f"Failed to handle callback for trade_no={trade_no}: {e}"
            )
            return {
                "success": False,
                "message": str(e),
                "points_added": 0,
            }

    # ------------------------------------------------------------------ #
    # Order Status
    # ------------------------------------------------------------------ #

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """
        Get lightweight status information for a single order (for polling).

        Args:
            order_id: UUID of the order.

        Returns:
            ``{success, data: {order_id, payment_status, points_amount, paid_at}}``
        """
        order = await self.payment_repo.get_order_by_id(order_id)
        if order is None:
            return {"success": False, "error": "Order not found"}

        return {
            "success": True,
            "data": {
                "order_id": order["id"],
                "payment_status": order.get("payment_status"),
                "points_amount": order.get("points_amount"),
                "paid_at": order.get("paid_at"),
            },
        }

    # ------------------------------------------------------------------ #
    # Team Order History
    # ------------------------------------------------------------------ #

    async def get_team_orders(
        self,
        team_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get paginated order history for a team.

        Delegates directly to ``PaymentRepository.get_team_orders()``.

        Args:
            team_id: UUID of the team.
            limit: Maximum records to return.
            offset: Pagination offset.

        Returns:
            List of order dicts ordered by created_at DESC.
        """
        return await self.payment_repo.get_team_orders(
            team_id=team_id, limit=limit, offset=offset
        )
