# backend/app/api/media_auth.py

"""
Media session cookie management.

Provides httpOnly cookie-based authentication for the /media/ static file route.
Browser <video>/<img> tags cannot send Authorization headers, so we use a
secure cookie set after JWT login.

Flow:
  1. Frontend calls POST /api/v1/auth/media-session with Bearer JWT
  2. Backend validates JWT, extracts user_id, signs a media_session cookie
  3. /media/{path} route validates the cookie before serving files
  4. Frontend calls DELETE /api/v1/auth/media-session on logout

Future: ?share_token= and ?review_token= query params bypass cookie auth.
"""

import hashlib
import hmac
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import settings
from app.services.supabase_auth_service import SupabaseAuthService

router = APIRouter(prefix="/auth", tags=["Media Auth"])

# Cookie config
COOKIE_NAME = "media_session"
COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 days (matches Supabase refresh token lifetime)
MEDIA_TOKEN_MAX_AGE = (
    4 * 3600
)  # 4 hours (refreshed on every Supabase TOKEN_REFRESHED event)


def _get_secret() -> str:
    """Use SUPABASE_SERVICE_ROLE_KEY as HMAC secret (always available)."""
    secret = settings.SUPABASE_SERVICE_ROLE_KEY
    if not secret:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY not configured")
    return secret


def _sign_cookie(user_id: str, expires_at: int) -> str:
    """Create a signed cookie value: user_id.expires_at.signature"""
    payload = f"{user_id}.{expires_at}"
    sig = hmac.new(
        _get_secret().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{payload}.{sig}"


def validate_media_cookie(cookie_value: str) -> Optional[str]:
    """Validate cookie and return user_id if valid, None otherwise."""
    if not cookie_value:
        return None
    parts = cookie_value.split(".")
    if len(parts) != 3:
        return None

    user_id, expires_str, sig = parts

    # Check expiry
    try:
        expires_at = int(expires_str)
    except ValueError:
        return None
    if time.time() > expires_at:
        return None

    # Verify signature
    expected_payload = f"{user_id}.{expires_str}"
    expected_sig = hmac.new(
        _get_secret().encode(),
        expected_payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]

    if not hmac.compare_digest(sig, expected_sig):
        return None

    return user_id


@router.post("/media-session")
async def create_media_session(
    request: Request,
    authorization: str = Header(...),
):
    """
    Create a media session cookie.

    Validates the JWT and sets an httpOnly cookie for /media/ access.
    Called after login and on token refresh.
    """
    try:
        token = authorization.replace("Bearer ", "")
        auth_service = SupabaseAuthService()
        user = await auth_service.get_user(token)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = user.get("id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid user")

        expires_at = int(time.time()) + COOKIE_MAX_AGE
        cookie_value = _sign_cookie(user_id, expires_at)

        response = JSONResponse(content={"success": True})

        response.set_cookie(
            key=COOKIE_NAME,
            value=cookie_value,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/media",  # Only sent for /media/ requests
        )

        logger.info(f"Media session created for user {user_id}")
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create media session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create media session")


@router.delete("/media-session")
async def delete_media_session(request: Request):
    """Clear the media session cookie on logout."""
    response = JSONResponse(content={"success": True})
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/media",
        secure=True,
        samesite="lax",
    )
    logger.info("Media session cleared")
    return response


@router.post("/media-token")
async def create_media_token(
    authorization: str = Header(...),
):
    """
    Generate a short-lived signed media token for URL-based auth.

    Used as ?token= query parameter on /media/ URLs.
    Enables <video>/<img> tags to authenticate without cookies.
    """
    try:
        token = authorization.replace("Bearer ", "")
        auth_service = SupabaseAuthService()
        user = await auth_service.get_user(token)

        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        user_id = user.get("id")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid user")

        expires_at = int(time.time()) + MEDIA_TOKEN_MAX_AGE
        media_token = _sign_cookie(user_id, expires_at)

        return {"token": media_token, "expires_at": expires_at}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create media token: {e}")
        raise HTTPException(status_code=500, detail="Failed to create media token")
