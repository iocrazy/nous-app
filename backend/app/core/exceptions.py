"""Global exception handlers for FastAPI.

Every unhandled error gets a consistent ErrorResponse envelope, with the
request id attached so clients can correlate bug reports with server logs.
Domain-specific errors subclass AppError so routers can raise them without
hand-rolling HTTPException + detail at every call site.

CORS note: FastAPI's exception handlers return responses outside the user
middleware stack, so CORSMiddleware never gets to attach its headers. On a
500 that hit a route with an allowed Origin, the browser then reports a
misleading "No Access-Control-Allow-Origin header" CORS error instead of the
real 500. Every handler here calls ``_cors_headers_for(request)`` to attach
the same headers CORSMiddleware would have, so the real status + body reach
the browser.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from app.core.config import settings


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    code: str = "internal_error"
    request_id: Optional[str] = None
    details: Optional[Any] = Field(default=None)


class AppError(Exception):
    """Base class for domain errors raised from service/repository layers."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "permission_denied"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


def _request_id(request: Request) -> Optional[str]:
    header = request.headers.get("x-request-id")
    if header:
        return header
    return getattr(request.state, "request_id", None)


# CORS allowlist for the exception-handler responses. Keep this regex in sync
# with ``_CORS_ALLOW_REGEX`` in app/main.py — that's where CORSMiddleware
# enforces the same rules for non-error responses. (If these drift, prod 500s
# will silently look like CORS errors again.)
_CORS_ALLOW_REGEX = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0"
    r"|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+):\d+$"
    r"|^https://mediahub-(git-)?[a-z0-9-]+-heygos-projects\.vercel\.app$"
)


def _origin_is_allowed(origin: str) -> bool:
    """True if ``origin`` matches ``settings.CORS_ORIGINS`` or the regex."""
    if not origin:
        return False
    allow_list = settings.CORS_ORIGINS
    if "*" in allow_list:
        return True
    if origin in allow_list:
        return True
    return bool(_CORS_ALLOW_REGEX.match(origin))


def _cors_headers_for(request: Request) -> Dict[str, str]:
    """Return the CORS response headers CORSMiddleware would have added.

    FastAPI exception handlers return responses outside the middleware stack,
    so CORSMiddleware never runs on them. Without these headers, a 500 on a
    cross-origin POST reaches the browser as a generic "No Access-Control-
    Allow-Origin header" CORS error instead of the real status + body.
    """
    origin = request.headers.get("origin", "")
    if not _origin_is_allowed(origin):
        return {}
    headers: Dict[str, str] = {
        "access-control-allow-origin": origin,
        "vary": "Origin",
    }
    if settings.CORS_CREDENTIALS:
        headers["access-control-allow-credentials"] = "true"
    return headers


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the global handlers to a FastAPI app."""

    # Boundary-layer rejections (SSRF, prompt-injection, etc.) → HTTP 400.
    # Must come BEFORE the generic Exception handler. Never echo the raw
    # rejected input back to the client (log it server-side instead).
    from app.boundary.errors import BoundaryError, URLBlockedError

    @app.exception_handler(BoundaryError)
    async def _handle_boundary(request: Request, exc: BoundaryError) -> JSONResponse:
        # Server-side log carries the full reason for diagnosis.
        logger.warning(
            f"[Boundary] {type(exc).__name__} at {request.method} "
            f"{request.url.path}: {exc}"
        )
        # Client sees a generic message — no input echo, no internal detail.
        if isinstance(exc, URLBlockedError):
            client_msg = "URL not allowed"
            client_code = "url_blocked"
        else:
            client_msg = "Input rejected by boundary"
            client_code = "boundary_rejected"
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=client_msg,
                code=client_code,
                request_id=_request_id(request),
            ).model_dump(),
            headers=_cors_headers_for(request),
        )

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.info(
            f"[AppError] {exc.code} ({exc.status_code}) at {request.method} "
            f"{request.url.path}: {exc.message}"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=exc.message,
                code=exc.code,
                request_id=_request_id(request),
                details=exc.details,
            ).model_dump(),
            headers=_cors_headers_for(request),
        )

    @app.exception_handler(HTTPException)
    async def _handle_http_exception(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        merged_headers: Dict[str, str] = {**_cors_headers_for(request)}
        if exc.headers:
            merged_headers.update(exc.headers)

        # 5XX bodies frequently carry str(exception) — which leaks DB
        # column names, file paths, traceback fragments, schema hints,
        # and provider-key names to anyone who can hit the endpoint.
        # Replace the user-facing message with a generic string and put
        # the original detail in server logs (with request id) for
        # operators to correlate.
        if exc.status_code >= 500:
            logger.warning(
                f"[5xx] {request.method} {request.url.path} "
                f"({exc.status_code}) detail={detail!r} "
                f"request_id={_request_id(request)}"
            )
            return JSONResponse(
                status_code=exc.status_code,
                content=ErrorResponse(
                    error="Internal server error",
                    code=f"http_{exc.status_code}",
                    request_id=_request_id(request),
                    details=None,
                ).model_dump(),
                headers=merged_headers or None,
            )

        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=detail or "Request failed",
                code=f"http_{exc.status_code}",
                request_id=_request_id(request),
                details=None if isinstance(exc.detail, str) else exc.detail,
            ).model_dump(),
            headers=merged_headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="Request validation failed",
                code="validation_error",
                request_id=_request_id(request),
                details=exc.errors(),
            ).model_dump(),
            headers=_cors_headers_for(request),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            f"[Unhandled] {type(exc).__name__} at {request.method} {request.url.path}: {exc}"
        )
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="Internal server error",
                code="internal_error",
                request_id=_request_id(request),
            ).model_dump(),
            headers=_cors_headers_for(request),
        )
