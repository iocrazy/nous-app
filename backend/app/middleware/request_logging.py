"""Request logging middleware for API audit trail."""

import asyncio
import base64
import json
import time
import uuid
from typing import Optional

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.db import get_async_supabase_admin

# Paths that should NOT be logged
EXCLUDED_PATHS = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/",
}

EXCLUDED_PREFIXES = (
    "/media/",
    "/assets/",
)

# Fields whose values should be sanitized in request bodies and query strings
SENSITIVE_FIELDS = {
    "password",
    "token",
    "secret",
    "key",
    "authorization",
    "access_token",
    "refresh_token",
}
SENSITIVE_QUERY_KEYS = {
    "token",
    "share_token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
}

# Max body size to log (10KB)
MAX_BODY_SIZE = 10 * 1024


def _should_exclude(path: str) -> bool:
    if path in EXCLUDED_PATHS:
        return True
    if path.startswith(EXCLUDED_PREFIXES):
        return True
    return False


def _sanitize_body(body: dict) -> dict:
    sanitized = {}
    for k, v in body.items():
        if any(s in k.lower() for s in SENSITIVE_FIELDS):
            sanitized[k] = "***"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_body(v)
        else:
            sanitized[k] = v
    return sanitized


def _sanitize_query_params(params: dict) -> dict:
    return {
        k: ("***" if k.lower() in SENSITIVE_QUERY_KEYS else v)
        for k, v in params.items()
    }


def _extract_user_id_from_jwt(
    authorization: Optional[str],
) -> tuple[Optional[str], str]:
    """Extract user_id from JWT without verification (lightweight).

    Returns:
        (user_id, auth_type)
    """
    if not authorization:
        return None, "anonymous"

    if authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            # JWT has 3 parts: header.payload.signature
            parts = token.split(".")
            if len(parts) >= 2:
                # Decode payload (add padding)
                payload_b64 = parts[1]
                padding = 4 - len(payload_b64) % 4
                if padding != 4:
                    payload_b64 += "=" * padding
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                return payload.get("sub"), "jwt"
        except Exception:
            pass
        return None, "jwt"

    return None, "anonymous"


def _get_client_ip(request: Request) -> str:
    # Check common proxy headers
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    if request.client:
        return request.client.host
    return "unknown"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if _should_exclude(path):
            return await call_next(request)

        request_id = str(uuid.uuid4())
        start_time = time.monotonic()

        # Read request body for logging (only for methods that have a body)
        request_body = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                raw_body = await request.body()
                if len(raw_body) > MAX_BODY_SIZE:
                    request_body = {"_truncated": True, "size_bytes": len(raw_body)}
                elif raw_body:
                    try:
                        parsed = json.loads(raw_body)
                        if isinstance(parsed, dict):
                            request_body = _sanitize_body(parsed)
                        else:
                            request_body = parsed
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        request_body = {"_binary": True, "size_bytes": len(raw_body)}
            except Exception:
                pass

        # Extract auth info
        auth_header = request.headers.get("authorization")
        api_key_header = request.headers.get("x-api-key")

        if api_key_header:
            user_id = None  # Can't resolve without DB call
            auth_type = "api_key"
        else:
            user_id, auth_type = _extract_user_id_from_jwt(auth_header)

        # Process request with loguru context for log correlation
        error_detail = None
        try:
            with logger.contextualize(request_id=request_id):
                response = await call_next(request)
        except Exception as exc:
            error_detail = str(exc)
            raise
        finally:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # Capture error detail from response
        status_code = response.status_code
        if status_code >= 400 and not error_detail:
            error_detail = f"HTTP {status_code}"

        # Add X-Request-ID header
        response.headers["X-Request-ID"] = request_id

        # Parse query params (redact sensitive keys like token/share_token)
        query_params = (
            _sanitize_query_params(dict(request.query_params))
            if request.query_params
            else None
        )

        # Fire-and-forget: write log to DB
        asyncio.create_task(
            _write_log(
                request_id=request_id,
                user_id=user_id,
                auth_type=auth_type,
                method=request.method,
                path=path,
                query_params=query_params,
                request_body=request_body,
                status_code=status_code,
                response_time_ms=elapsed_ms,
                ip_address=_get_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                error_detail=error_detail if status_code >= 400 else None,
            )
        )

        return response


async def _write_log(
    request_id: str,
    user_id: Optional[str],
    auth_type: str,
    method: str,
    path: str,
    query_params: Optional[dict],
    request_body: Optional[dict],
    status_code: int,
    response_time_ms: int,
    ip_address: str,
    user_agent: Optional[str],
    error_detail: Optional[str],
) -> None:
    """Write request log to database (fire-and-forget)."""
    try:
        supabase = await get_async_supabase_admin()
        await supabase.table("api_request_logs").insert(
            {
                "request_id": request_id,
                "user_id": user_id,
                "auth_type": auth_type,
                "method": method,
                "path": path,
                "query_params": query_params,
                "request_body": request_body,
                "status_code": status_code,
                "response_time_ms": response_time_ms,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "error_detail": error_detail,
            }
        ).execute()
    except Exception as e:
        logger.warning(f"Failed to write request log: {e}")
