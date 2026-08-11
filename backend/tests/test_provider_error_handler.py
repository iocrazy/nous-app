"""Provider 异常的类型化 HTTP 面（spec §2）——handler 单测,直调不起 TestClient 也可,
但状态码/头/体要真实验证,故用 fastapi.testclient 起最小 app。"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.provider_errors import (
    provider_error_payload,
    register_provider_error_handlers,
)
from app.services.ai.llm.llm_fallback_chain import AllModelsFailed
from app.services.ai.llm.llm_retry_middleware import LLMCallError


def _app_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    register_provider_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def _rate_limited_all_failed() -> AllModelsFailed:
    # 真实形态:httpx 429 埋在 __cause__ 链深处(fallback 链 raise ... from last_exc)
    request = httpx.Request("POST", "https://ark.example/api")
    response = httpx.Response(429, request=request)
    inner = httpx.HTTPStatusError("429", request=request, response=response)
    failed = AllModelsFailed("primary + 0 fallback(s) exhausted")
    failed.__cause__ = inner
    return failed


def test_rate_limit_maps_to_503_with_retry_after():
    client = _app_raising(_rate_limited_all_failed())
    resp = client.get("/boom")
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "provider_rate_limit"
    assert "rate-limiting" in body["error"]
    assert resp.headers.get("retry-after") == "60"


def test_unclassifiable_provider_exception_maps_to_500_internal():
    client = _app_raising(AllModelsFailed("primary + 0 fallback(s) exhausted"))
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["code"] == "internal_error"


def test_llm_call_error_auth_maps_to_502():
    request = httpx.Request("POST", "https://ark.example/api")
    response = httpx.Response(401, request=request)
    inner = httpx.HTTPStatusError("401", request=request, response=response)
    exc = LLMCallError("auth failed")
    exc.__cause__ = inner
    client = _app_raising(exc)
    resp = client.get("/boom")
    assert resp.status_code == 502
    assert resp.json()["code"] == "provider_auth"


def test_payload_helper_matches_handler_mapping():
    status, code, message = provider_error_payload(_rate_limited_all_failed())
    assert (status, code) == (503, "provider_rate_limit")
    assert message  # 面向用户的一句话,非异常串


def test_non_provider_exception_untouched():
    # 未注册类型仍走 FastAPI 默认(本测试 app 无兜底 handler → 500 纯文本),
    # 证明 handler 只精确命中两类异常
    client = _app_raising(RuntimeError("boom"))
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert "provider" not in resp.text
