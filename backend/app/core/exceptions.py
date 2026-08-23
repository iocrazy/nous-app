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

import math
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


def _scrub_nonfinite(value: Any) -> Any:
    """Replace NaN / ±Infinity with their text form, recursively.

    Same failure class the ``jsonable_encoder`` above already fixes once, one
    variant it does not cover. A validation error echoes the offending ``input``
    verbatim, and ``json.loads`` — which is what FastAPI parses request bodies
    with — **accepts the bare tokens** ``NaN`` / ``Infinity``. So a client can
    put a non-finite float into any float field, pydantic correctly rejects it,
    and then Starlette's ``JSONResponse`` (which renders with
    ``allow_nan=False``) raises while serializing the 422 body — turning a
    correct, actionable 422 into an opaque 500.

    It is not hypothetical or specific to one route: every endpoint with a
    constrained float field has this shape. ``/distribution/covers/select``
    (``timestamp_seconds: float, ge=0``) has had it since it shipped.

    The value is stringified rather than dropped so the message still tells the
    user what they actually sent — "Input should be greater than or equal to 0"
    with the input missing is a worse error, not a safer one.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {k: _scrub_nonfinite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_nonfinite(v) for v in value]
    return value


def _request_id(request: Request) -> Optional[str]:
    header = request.headers.get("x-request-id")
    if header:
        return header
    return getattr(request.state, "request_id", None)


# Single source of truth for the dynamic-origin CORS allowlist.
#
# Two places need it: CORSMiddleware in app/main.py (normal responses) and the
# exception handlers below (error responses, which bypass the middleware
# stack). These used to be two hand-synced copies with a "keep in sync"
# comment — exactly the invariant that drifts, and when it drifts prod 500s
# silently look like CORS errors. app/main.py now imports this constant.
#
# Covers:
#   - localhost / RFC1918 dev hosts on any explicit port
#   - Cloudflare Pages previews for this project
#     (<branch-alias>.nous-app.pages.dev, <deploy-hash>.nous-app.pages.dev)
# Stable production origins (app.nous.ink, cn/api.nous.ink, ...) live in
# ``settings.CORS_ORIGINS``, not here.
CORS_ALLOW_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0"
    r"|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+):\d+$"
    r"|^https://[a-z0-9-]+\.nous-app\.pages\.dev$"
)

_CORS_ALLOW_REGEX = re.compile(CORS_ALLOW_ORIGIN_REGEX)


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
        # Exception: the module gate's 503. Its body is entirely of our own
        # construction — a fixed code plus a module id from the registry, no
        # exception text — so generalizing it leaks nothing and only destroys
        # the typed contract the frontend needs to tell "feature switched off"
        # apart from "server broke".
        is_module_disabled = (
            isinstance(exc.detail, dict) and exc.detail.get("code") == "MODULE_DISABLED"
        )

        if exc.status_code >= 500 and not is_module_disabled:
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
        # jsonable_encoder mirrors FastAPI's default handler: a pydantic
        # field_validator raising ValueError puts the exception OBJECT into
        # each error's ctx — serializing that raw turns every such 422 into
        # a 500 (TypeError inside JSONResponse rendering).
        from fastapi.encoders import jsonable_encoder

        return JSONResponse(
            status_code=422,
            content=ErrorResponse(
                error="Request validation failed",
                code="validation_error",
                request_id=_request_id(request),
                details=_scrub_nonfinite(jsonable_encoder(exc.errors())),
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
