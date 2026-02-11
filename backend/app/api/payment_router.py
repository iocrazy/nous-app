# app/api/payment_router.py

"""
Payment API Router

Provides endpoints for browsing point packages, creating payment orders,
polling order status, viewing order history, and receiving payment-provider
callbacks (WeChat Pay / Alipay).
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.points_repository import PointsRepository
from app.schemas.payment import CreateOrderRequest
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payment")

# Shared instances (lazy; each holds its own async client)
_payment_service = PaymentService()
_points_repo = PointsRepository()


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


async def _resolve_team_id(
    auth: AuthDep,
    team_id: Optional[str] = None,
) -> str:
    """
    Resolve the team ID for the current request.

    If *team_id* is provided explicitly it is used as-is; otherwise
    the authenticated user's own ID is used as a personal-team fallback.
    """
    if team_id:
        return team_id
    # Fallback: treat the user's own ID as a personal team
    return auth.user_id


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #


@router.get("/packages", summary="Get active point packages")
async def get_packages():
    """
    Return all active purchasable point packages.

    No authentication required -- this is public catalogue data.
    """
    packages = await _points_repo.get_active_packages()
    return {"success": True, "data": packages}


@router.post("/create-order", summary="Create a payment order")
async def create_order(body: CreateOrderRequest, auth: AuthDep):
    """
    Create a new payment order for the specified package and payment method.

    The caller must be authenticated. The ``team_id`` is taken from the
    request body.
    """
    team_id = await _resolve_team_id(auth, body.team_id)
    result = await _payment_service.create_order(
        team_id=team_id,
        user_id=auth.user_id,
        package_id=body.package_id,
        payment_method=body.payment_method,
    )
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create order"),
        )
    return result


@router.get("/order/{order_id}/status", summary="Poll order status")
async def get_order_status(order_id: str, auth: AuthDep):
    """
    Lightweight endpoint for the frontend to poll payment status.
    """
    result = await _payment_service.get_order_status(order_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("error", "Order not found"),
        )
    return result


@router.get("/orders", summary="Get team order history")
async def get_orders(
    auth: AuthDep,
    team_id: Optional[str] = Query(None, description="Team ID"),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset"),
):
    """
    Return paginated order history for a team.
    """
    resolved_team_id = await _resolve_team_id(auth, team_id)
    orders = await _payment_service.get_team_orders(
        team_id=resolved_team_id, limit=limit, offset=offset
    )
    return {"success": True, "data": orders}


# ---------------------------------------------------------------------- #
# Payment Provider Callbacks (NO auth -- called by external providers)
# ---------------------------------------------------------------------- #


@router.post("/callback/wechat", summary="WeChat Pay callback")
async def wechat_callback(request: Request):
    """
    Receive payment result notification from WeChat Pay.

    .. note::

        This is a placeholder implementation. In production you MUST:
        - Verify the request signature using the WeChat API certificate.
        - Parse the encrypted XML/JSON body per WeChat Pay v3 spec.
        - Return the appropriate response format.

    Returns WeChat-expected JSON:
    ``{"code": "SUCCESS"|"FAIL", "message": "..."}``
    """
    try:
        # TODO: Verify WeChat Pay signature
        # TODO: Decrypt and parse WeChat Pay notification body
        body = await request.body()
        logger.info(f"WeChat callback received: {len(body)} bytes")

        # --- Placeholder: extract trade_no from body ---
        # In production, parse the notification body to get the trade_no
        # and payment result. For now we just log the raw body.
        #
        # Example (pseudo-code):
        #   data = decrypt_wechat_body(body, api_key)
        #   trade_no = data["out_trade_no"]
        #   paid = data["trade_state"] == "SUCCESS"

        # For now, return success acknowledgement
        # Real implementation would call:
        #   result = await _payment_service.handle_callback(
        #       trade_no=trade_no, payment_method="wechat", paid=paid
        #   )

        return {"code": "SUCCESS", "message": "OK"}

    except Exception as e:
        logger.error(f"WeChat callback error: {e}")
        return {"code": "FAIL", "message": str(e)}


@router.post("/callback/alipay", summary="Alipay callback")
async def alipay_callback(request: Request):
    """
    Receive payment result notification from Alipay.

    .. note::

        This is a placeholder implementation. In production you MUST:
        - Verify the request signature using the Alipay public key.
        - Parse the form-encoded body per Alipay async notification spec.
        - Return ``"success"`` or ``"fail"`` as plain text.

    Returns plain text ``"success"`` or ``"fail"``.
    """
    try:
        # TODO: Verify Alipay signature
        # TODO: Parse Alipay form-encoded notification body
        body = await request.body()
        logger.info(f"Alipay callback received: {len(body)} bytes")

        # --- Placeholder: extract trade_no from body ---
        # In production, parse form data to get out_trade_no and trade_status.
        #
        # Example (pseudo-code):
        #   form = parse_qs(body.decode())
        #   trade_no = form["out_trade_no"][0]
        #   paid = form["trade_status"][0] == "TRADE_SUCCESS"

        # For now, return success acknowledgement
        # Real implementation would call:
        #   result = await _payment_service.handle_callback(
        #       trade_no=trade_no, payment_method="alipay", paid=paid
        #   )

        return "success"

    except Exception as e:
        logger.error(f"Alipay callback error: {e}")
        return "fail"
