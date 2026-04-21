"""OpenAI-compatible chat-completion adapter.

Base class for any provider that speaks the OpenAI /v1/chat/completions
API shape: Qwen (DashScope), DeepSeek, Doubao (Volcengine), etc.
Subclasses typically only override the constructor defaults (api_url,
default_model).
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx
from loguru import logger

from app.schemas.ai_library import ComposedSystemPrompt


def _ensure_chat_completions_suffix(url: str) -> str:
    """Accept either a base URL (legacy Phase 1 env style, e.g.
    ``http://host/v1``) or a full endpoint URL. Always return the full
    ``/chat/completions`` path so the adapter can POST directly.

    The previous Phase 1 ``QwenAdapter`` expected a base URL and appended
    the path inside ``call()``. The refactored base class takes a full
    endpoint URL so every provider works the same way, and existing
    ``LLM_API_URL`` values (``http://host/v1``) still work transparently.
    """
    trimmed = url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


class OpenAICompatibleAdapter:
    """Adapter for OpenAI-compatible chat-completion endpoints."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        default_model: str = "",
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_url = _ensure_chat_completions_suffix(api_url)
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds

    def _build_body(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": composed.model or self.default_model,
            "temperature": composed.temperature,
            "max_tokens": composed.max_tokens,
            "messages": [
                {"role": "system", "content": composed.system_message},
                *messages,
            ],
        }
        if composed.tools:
            body["tools"] = composed.tools
            body["tool_choice"] = "auto"
        return body

    def _build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def call(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        body = self._build_body(composed, messages)
        headers = self._build_headers()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(self.api_url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        logger.debug(
            f"[{type(self).__name__}] model={body['model']} "
            f"tool_calls={bool(data.get('choices', [{}])[0].get('message', {}).get('tool_calls'))}"
        )
        return data
