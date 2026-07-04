from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.mediahub_model_health import (
    probe_mediahub_model as _probe_mediahub_model,
)


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _Client:
    """Fake httpx.AsyncClient context manager returning a preset response."""

    _resp = _Resp()

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _Client.captured = {"url": url, "json": json, "headers": headers}
        return _Client._resp


def _patch(resp):
    _Client._resp = resp
    return patch("app.services.ai.mediahub_model_health.httpx.AsyncClient", _Client)


@pytest.mark.asyncio
async def test_llm_chat_ok() -> None:
    row = {
        "type": "llm",
        "actual_model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "k",
    }
    with _patch(_Resp(200, {"choices": [{"message": {"content": "hi"}}]})):
        out = await _probe_mediahub_model(row)
    assert out["ok"] is True
    assert _Client.captured["url"].endswith("/chat/completions")


@pytest.mark.asyncio
async def test_llm_http_error_surfaces() -> None:
    row = {
        "type": "llm",
        "actual_model": "doubao-seed-2-0-pro",
        "base_url": "https://ark/api/v3",
        "api_key": "k",
    }
    with _patch(_Resp(429, text='{"error":"SetLimitExceeded"}')):
        out = await _probe_mediahub_model(row)
    assert out["ok"] is False
    assert "429" in out["error"]


@pytest.mark.asyncio
async def test_embedding_multimodal_dims() -> None:
    row = {
        "type": "embedding",
        "actual_model": "doubao-embedding-vision-251215",
        "base_url": "https://ark/api/v3",
        "api_key": "k",
    }
    with _patch(_Resp(200, {"data": {"embedding": [0.1] * 2048}})):
        out = await _probe_mediahub_model(row)
    assert out["ok"] is True
    assert out["dims"] == 2048
    assert _Client.captured["url"].endswith("/embeddings/multimodal")
    assert _Client.captured["json"]["input"] == [{"type": "text", "text": "ping"}]


@pytest.mark.asyncio
async def test_embedding_openai_shape() -> None:
    row = {
        "type": "embedding",
        "actual_model": "text-embedding-3-small",
        "base_url": "https://api.openai.com/v1",
        "api_key": "k",
    }
    with _patch(_Resp(200, {"data": [{"embedding": [0.2] * 1536}]})):
        out = await _probe_mediahub_model(row)
    assert out["dims"] == 1536
    assert _Client.captured["url"].endswith("/embeddings")
    assert _Client.captured["json"]["input"] == "ping"


@pytest.mark.asyncio
async def test_asr_delegates_to_test_connection() -> None:
    row = {
        "type": "asr",
        "actual_provider": "volcengine",
        "actual_model": "seed-asr",
        "base_url": "",
        "api_key": "k",
    }
    with patch(
        "app.services.ai.mediahub_model_health.AIProviderFactory.test_connection",
        AsyncMock(
            return_value={"success": True, "models": ["seed-asr"], "error": None}
        ),
    ):
        out = await _probe_mediahub_model(row)
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_exception_is_caught() -> None:
    row = {
        "type": "llm",
        "actual_model": "m",
        "base_url": "https://x/v1",
        "api_key": "k",
    }
    with patch(
        "app.services.ai.mediahub_model_health.httpx.AsyncClient",
        side_effect=RuntimeError("boom"),
    ):
        out = await _probe_mediahub_model(row)
    assert out["ok"] is False
    assert "boom" in out["error"]
