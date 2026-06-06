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
from app.repositories.points_repository import get_points_repository
from app.schemas.payment import CreateOrderRequest
from app.services.billing.payment_service import PaymentService

router = APIRouter(prefix="/payment")

# Shared instances (lazy; each holds its own async client)
_payment_service = PaymentService()
_points_repo = get_points_repository()


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

    .. warning::

        **SECURITY**: Signature verification is NOT yet implemented.
        This endpoint is intentionally disabled until WeChat Pay SDK
        integration is complete. All incoming callbacks are rejected
        with HTTP 501 to prevent unverified payment state changes.

        Before enabling, you MUST:
        - Verify the request signature using the WeChat API certificate.
        - Decrypt and parse the encrypted XML/JSON body per WeChat Pay v3 spec.
        - Only update order state after successful signature verification.
    """
    # SECURITY: Payment callbacks MUST verify provider signatures before
    # processing any payment state changes. Accepting unverified callbacks
    # allows attackers to fraudulently mark orders as paid.
    # This endpoint will remain disabled until proper signature verification
    # is integrated via the WeChat Pay v3 SDK.
    logger.warning(
        "WeChat Pay callback received but rejected — signature verification not implemented"
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="WeChat Pay callback not yet active: signature verification required",
    )


@router.post("/callback/alipay", summary="Alipay callback")
async def alipay_callback(request: Request):
    """
    Receive payment result notification from Alipay.

    .. warning::

        **SECURITY**: Signature verification is NOT yet implemented.
        This endpoint is intentionally disabled until Alipay SDK
        integration is complete. All incoming callbacks are rejected
        with HTTP 501 to prevent unverified payment state changes.

        Before enabling, you MUST:
        - Verify the request signature using the Alipay public key.
        - Parse the form-encoded body per Alipay async notification spec.
        - Only update order state after successful signature verification.
    """
    # SECURITY: Payment callbacks MUST verify provider signatures before
    # processing any payment state changes. Accepting unverified callbacks
    # allows attackers to fraudulently mark orders as paid.
    # This endpoint will remain disabled until proper signature verification
    # is integrated via the Alipay SDK.
    logger.warning(
        "Alipay callback received but rejected — signature verification not implemented"
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Alipay callback not yet active: signature verification required",
    )
