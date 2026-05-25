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
from dataclasses import dataclass
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import settings
from app.services.infra.supabase_auth_service import SupabaseAuthService

router = APIRouter(prefix="/auth", tags=["Media Auth"])

# Cookie config
COOKIE_NAME = "media_session"
COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 days (matches Supabase refresh token lifetime)
MEDIA_TOKEN_MAX_AGE = (
    4 * 3600
)  # 4 hours (refreshed on every Supabase TOKEN_REFRESHED event)

_warned_fallback = False


def _signing_secret() -> str:
    """Secret used to SIGN new tokens: MEDIA_TOKEN_SECRET if set, else the
    service-role key (with a one-time warning — #276)."""
    global _warned_fallback
    secret = settings.MEDIA_TOKEN_SECRET or settings.SUPABASE_SERVICE_ROLE_KEY
    if not settings.MEDIA_TOKEN_SECRET and not _warned_fallback:
        logger.warning(
            "MEDIA_TOKEN_SECRET not set — falling back to SUPABASE_SERVICE_ROLE_KEY "
            "for media-token signing. Set MEDIA_TOKEN_SECRET to decouple (#276)."
        )
        _warned_fallback = True
    if not secret:
        raise RuntimeError("No media-token secret configured")
    return secret


def _verify_secrets() -> list[str]:
    """Secrets to TRY when verifying (dual-secret grace): the dedicated secret
    AND the legacy service-role key, deduped, non-empty."""
    out: list[str] = []
    for s in (settings.MEDIA_TOKEN_SECRET, settings.SUPABASE_SERVICE_ROLE_KEY):
        if s and s not in out:
            out.append(s)
    return out


def _hmac(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def _sign_token(user_id: str, issued_at: int, expires_at: int) -> str:
    """New 4-part token: user_id.issued_at.expires_at.sig (#275 adds issued_at)."""
    payload = f"{user_id}.{issued_at}.{expires_at}"
    return f"{payload}.{_hmac(_signing_secret(), payload)}"


@dataclass(frozen=True)
class _ParsedToken:
    user_id: str
    issued_at: Optional[int]  # None for legacy 3-part tokens
    expires_at: int


def _verify_token(value: str) -> Optional[_ParsedToken]:
    """Verify signature + expiry for BOTH new (4-part) and legacy (3-part)
    tokens, trying each grace secret. Returns parsed token or None. Pure /
    sync — no denylist check here (that is async, added in Task 2)."""
    if not value:
        return None
    parts = value.split(".")
    if len(parts) == 4:
        user_id, issued_str, expires_str, sig = parts
        payload = f"{user_id}.{issued_str}.{expires_str}"
        try:
            issued_at: Optional[int] = int(issued_str)
        except ValueError:
            return None
    elif len(parts) == 3:  # legacy grace
        user_id, expires_str, sig = parts
        payload = f"{user_id}.{expires_str}"
        issued_at = None
    else:
        return None

    try:
        expires_at = int(expires_str)
    except ValueError:
        return None
    if time.time() > expires_at:
        return None

    for secret in _verify_secrets():
        if hmac.compare_digest(sig, _hmac(secret, payload)):
            return _ParsedToken(
                user_id=user_id, issued_at=issued_at, expires_at=expires_at
            )
    return None


async def _get_redis():
    from app.core.redis import get_async_redis

    return await get_async_redis()


def _revoke_key(user_id: str) -> str:
    return f"revoke:media:{user_id}"


async def revoke_media_tokens(user_id: str) -> None:
    """Revoke all media tokens for a user issued at-or-before now (#275).

    Best-effort: a Redis failure is logged, not raised (logout must not 500)."""
    if not user_id:
        return
    try:
        r = await _get_redis()
        await r.set(_revoke_key(user_id), str(int(time.time())), ex=COOKIE_MAX_AGE)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"revoke_media_tokens failed for {user_id}: {e}")


async def validate_media_cookie(cookie_value: str) -> Optional[str]:
    """Verify (sig + expiry) then check the revocation denylist (#275)."""
    parsed = _verify_token(cookie_value)
    if parsed is None:
        return None

    # Denylist check. Fail-open on Redis error (token already proven authentic).
    try:
        r = await _get_redis()
        raw = await r.get(_revoke_key(parsed.user_id))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"media denylist check failed (fail-open) for {parsed.user_id}: {e}"
        )
        return parsed.user_id

    if raw is not None:
        try:
            cutoff = int(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        except (ValueError, AttributeError):
            cutoff = None
        if cutoff is not None:
            if parsed.issued_at is None:
                return None  # legacy token can't prove it post-dates the cutoff
            if parsed.issued_at <= cutoff:
                return None
    return parsed.user_id


# ---------------------------------------------------------------------------
# Legacy shims — kept for callers that have not been updated yet.
# ---------------------------------------------------------------------------


def _get_secret() -> str:
    """DEPRECATED: use _signing_secret() or _verify_secrets().

    Kept as a shim for app.workflows.ai_transcription which builds its own
    3-part HMAC token for Volcengine ASR.  Returns the signing secret so that
    the generated URL can still be validated by _verify_token."""
    return _signing_secret()


def _sign_cookie(user_id: str, expires_at: int) -> str:
    """DEPRECATED: use _sign_token().

    Thin shim — produces a legacy 3-part token using the current signing
    secret so callers that haven't been updated continue to work."""
    payload = f"{user_id}.{expires_at}"
    return f"{payload}.{_hmac(_signing_secret(), payload)}"


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

        now = int(time.time())
        expires_at = now + COOKIE_MAX_AGE
        cookie_value = _sign_token(user_id, now, expires_at)

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
        logger.error(f"Failed to create media session: {e}")
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

        now = int(time.time())
        expires_at = now + MEDIA_TOKEN_MAX_AGE
        media_token = _sign_token(user_id, now, expires_at)

        return {"token": media_token, "expires_at": expires_at}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create media token: {e}")
        raise HTTPException(status_code=500, detail="Failed to create media token")
