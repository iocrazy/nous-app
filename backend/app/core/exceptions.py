"""Global exception handlers for FastAPI.

Every unhandled error gets a consistent ErrorResponse envelope, with the
request id attached so clients can correlate bug reports with server logs.
Domain-specific errors subclass AppError so routers can raise them without
hand-rolling HTTPException + detail at every call site.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field


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


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the global handlers to a FastAPI app."""

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
        )

    @app.exception_handler(HTTPException)
    async def _handle_http_exception(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=detail or "Request failed",
                code=f"http_{exc.status_code}",
                request_id=_request_id(request),
                details=None if isinstance(exc.detail, str) else exc.detail,
            ).model_dump(),
            headers=exc.headers,
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
        )
