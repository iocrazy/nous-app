"""Provider 故障的类型化 HTTP 面（spec 2026-08-11 §2）。

为什么是独立模块而不是 core/exceptions.py：handler 需要 import services 层的
异常类与分类器，而 core/exceptions.py 是全应用的底座，不该反向依赖 services。
main.py 在 register_exception_handlers(app) 之后调用本模块注册——FastAPI 按
异常类精确匹配，这两类异常从此不再落进兜底的 internal_error。

分类复用 error_catalog.classify_ai_error（走整条异常链，含 __cause__/__context__），
不新写分类逻辑——DBOS workflow 路径与 HTTP 路径从此共用同一套码。

CORS：core/exceptions.py 的模块 docstring 说明 FastAPI 异常 handler 的响应绕过
用户中间件栈，CORSMiddleware 不会给它们加头，所以那边每个 handler 都调
``_cors_headers_for(request)`` 补上同样的头。本模块的两个 handler 同样绕过中间件
栈，因此照抄同一处理，并像 ``_handle_http_exception`` 一样与 Retry-After 合并。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import ErrorResponse, _cors_headers_for, _request_id
from app.services.ai import error_catalog
from app.services.ai.error_catalog import classify_ai_error
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError

logger = logging.getLogger(__name__)

# error_catalog 码 → (HTTP 状态, 响应 code, 用户文案)。用户文案不透出内部异常串。
_MAPPING: dict[str, tuple[int, str, str]] = {
    error_catalog.PROVIDER_RATE_LIMIT: (
        503,
        "provider_rate_limit",
        "The model provider is rate-limiting requests. Try again shortly.",
    ),
    error_catalog.PROVIDER_UNREACHABLE: (
        502,
        "provider_unreachable",
        "The model provider is unreachable right now. Try again shortly.",
    ),
    error_catalog.PROVIDER_AUTH: (
        502,
        "provider_auth",
        "The model provider rejected our credentials. The team has been notified.",
    ),
    error_catalog.PROVIDER_BAD_MODEL: (
        502,
        "provider_bad_model",
        "The configured model is not available at the provider.",
    ),
    error_catalog.TASK_TIMEOUT: (
        504,
        "task_timeout",
        "The model took too long to respond. Try again.",
    ),
}

_FALLBACK = (500, "internal_error", "Internal server error")


def provider_error_payload(exc: BaseException) -> tuple[int, str, str]:
    """(status, code, user_message)。SSE error event（Task 3）与 handler 共用。"""
    ai_code = classify_ai_error(exc)
    return _MAPPING.get(ai_code or "", _FALLBACK)


def register_provider_error_handlers(app: FastAPI) -> None:
    async def _handle(request: Request, exc: Exception) -> JSONResponse:
        status, code, message = provider_error_payload(exc)
        rid = _request_id(request)
        logger.error(
            "[provider_errors] %s -> %s %s (request_id=%s): %r",
            type(exc).__name__,
            status,
            code,
            rid,
            exc,
        )
        headers = dict(_cors_headers_for(request))
        if code == "provider_rate_limit":
            headers["Retry-After"] = "60"
        return JSONResponse(
            status_code=status,
            content=ErrorResponse(
                error=message, code=code, request_id=rid
            ).model_dump(),
            headers=headers or None,
        )

    app.add_exception_handler(AllModelsFailed, _handle)
    app.add_exception_handler(LLMCallError, _handle)
