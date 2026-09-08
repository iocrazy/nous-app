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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import ErrorResponse, _cors_headers_for, _request_id
from app.services.ai import error_catalog
from app.services.ai.error_catalog import classify_ai_error
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError
from app.services.codex.errors import CodexLocalError

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
    # codex-local: the failure is on the USER's own machine, so the copy points
    # at what they can do there instead of at "the provider" (which for this
    # path is their own laptop). 424 = we could not act because the dependency
    # the request needs (their daemon) is not there.
    error_catalog.LOCAL_DAEMON_OFFLINE: (
        424,
        "local_daemon_offline",
        "Your local codex daemon is not connected. Start it on your machine "
        "(Settings → AI → Local CLI) and try again.",
    ),
    error_catalog.LOCAL_CLI_MISSING: (
        424,
        "local_cli_missing",
        "The codex CLI is not installed on your machine. Run "
        "`npm i -g @openai/codex` and restart the daemon.",
    ),
    error_catalog.LOCAL_REF_REJECTED: (
        400,
        "local_ref_rejected",
        "One of the attached images could not be used by your local daemon. "
        "Attach images from your nous library and try again.",
    ),
    error_catalog.LOCAL_TOOLS_UNSUPPORTED: (
        422,
        "local_tools_unsupported",
        "Local Codex cannot run tools or Skills. Unbind them from this agent "
        "or pick another model.",
    ),
    error_catalog.LOCAL_CODEX_NOT_LOGGED_IN: (
        401,
        "local_codex_not_logged_in",
        "Your local codex CLI is not logged in. Run `codex login` on your machine.",
    ),
    error_catalog.LOCAL_CODEX_FAILED: (
        424,
        "local_codex_failed",
        "Local Codex failed to produce a reply. Check the daemon log on your machine.",
    ),
    # 426 Upgrade Required — the one thing that fixes this is on the user's
    # machine and takes one command, so the copy is that command's location
    # rather than "try again".
    error_catalog.LOCAL_DAEMON_OUTDATED: (
        426,
        "local_daemon_outdated",
        "Your local codex daemon is out of date. Re-run the install command "
        "from Settings → AI → Local CLI to update it.",
    ),
}

_FALLBACK = (500, "internal_error", "Internal server error")


def provider_error_payload(exc: BaseException) -> tuple[int, str, str]:
    """(status, code, user_message)。SSE error event（Task 3）与 handler 共用。"""
    ai_code = classify_ai_error(exc)
    return _MAPPING.get(ai_code or "", _FALLBACK)


def stream_error_data(exc: BaseException) -> dict:
    """SSE ``event: error`` 的 data dict。provider 异常给类型码与用户文案
    （复用 ``provider_error_payload`` 的同一映射）；其他异常维持旧 shape
    （类型名+串）并补 ``code=internal_error``——「触发路径必须类型化回显」。

    单一入口：router 的 ``_stream_error_payload`` 与
    ``AILibraryChatService.chat_stream`` 的异常分支都调用这里，避免两处
    各写一份判断逻辑而其中一份漏掉 code 字段（2026-08-11 终审 Critical 1）。
    """
    if isinstance(exc, (AllModelsFailed, LLMCallError, CodexLocalError)):
        _status, code, message = provider_error_payload(exc)
        return {"error": message, "code": code}
    if isinstance(exc, HTTPException):
        # A typed refusal raised inside the turn (phase 2a: 409 no_open_question
        # / 400 answer_shape) must reach the SSE client with ITS code, not be
        # flattened into internal_error plus a repr of the exception.
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("code"):
            return {
                "error": str(detail.get("message") or detail["code"]),
                "code": str(detail["code"]),
                "status": exc.status_code,
            }
        return {
            "error": str(detail) if detail else f"HTTP {exc.status_code}",
            "code": f"http_{exc.status_code}",
            "status": exc.status_code,
        }
    return {"error": f"{type(exc).__name__}: {exc}", "code": "internal_error"}


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
    # codex-local errors can reach the HTTP/SSE face WITHOUT the retry
    # middleware wrapping them: the ones raised before dispatch
    # (``tools_unsupported``) are thrown by the adapter itself, so any call
    # site that awaits ``adapter.call()`` directly would otherwise land in the
    # catch-all and lose the typed code to a 500 internal_error.
    app.add_exception_handler(CodexLocalError, _handle)
