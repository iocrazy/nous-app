"""chat-stream 的 error event 必须带 code(spec §2)——provider 异常给类型码,
其他异常 code=internal_error 且 shape 兼容旧消费者。"""

import json

import httpx


def test_error_event_payload_for_provider_exception():
    from app.api.ai_library_router import _stream_error_payload
    from app.services.ai.llm.llm_fallback_chain import AllModelsFailed

    request = httpx.Request("POST", "https://ark.example/api")
    inner = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request)
    )
    exc = AllModelsFailed("primary + 0 fallback(s) exhausted")
    exc.__cause__ = inner
    payload = json.loads(_stream_error_payload(exc))
    assert payload["code"] == "provider_rate_limit"
    assert "rate-limiting" in payload["error"]


def test_error_event_payload_for_plain_exception():
    from app.api.ai_library_router import _stream_error_payload

    payload = json.loads(_stream_error_payload(RuntimeError("boom")))
    assert payload["code"] == "internal_error"
    assert "RuntimeError" in payload["error"]
