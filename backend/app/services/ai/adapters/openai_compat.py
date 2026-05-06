"""OpenAI-compatible chat-completion adapter.

Base class for any provider that speaks the OpenAI /v1/chat/completions
API shape: Qwen (DashScope), DeepSeek, Doubao (Volcengine), etc.
Subclasses typically only override the constructor defaults (api_url,
default_model).

Wave H (B): adds ``stream()`` for incremental output. Server-Sent
Events parsing per OpenAI spec (`data: <json>\\n\\n` lines, terminated
by `data: [DONE]`).
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List

import httpx
from loguru import logger

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters.base import StreamChunk


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

    async def stream(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
    ) -> AsyncIterator[StreamChunk]:
        """Wave H (B): Server-Sent Events streaming.

        OpenAI-compat providers stream as `data: <json>\\n\\n` lines
        terminated by `data: [DONE]`. Each delta JSON has shape:
          {"choices":[{"delta":{"content":"..."},
                       "finish_reason":null}]}

        Final chunk's ``usage`` (when provider supplies it — DashScope/
        OpenAI both do at finish_reason=stop) is attached to the LAST
        StreamChunk for the runner to record.
        """
        body = self._build_body(composed, messages)
        body["stream"] = True
        # Some providers (DashScope, OpenAI) gate usage emission behind
        # an opt-in flag. Always request it; harmless if ignored.
        body["stream_options"] = {"include_usage": True}
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream(
                "POST", self.api_url, json=body, headers=headers
            ) as resp:
                resp.raise_for_status()
                final_finish: str | None = None
                final_usage: Dict[str, Any] | None = None

                async for raw in resp.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    payload = raw[len("data:"):].strip()
                    if payload == "[DONE]":
                        # Emit terminal chunk if not already (some
                        # providers send finish_reason on a separate
                        # line; others bundle it with the [DONE] line).
                        if final_finish is None:
                            yield StreamChunk(
                                finish_reason="stop", usage=final_usage
                            )
                        return
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    # Capture usage if present on this event (final chunk
                    # in OpenAI-style streams).
                    usage = evt.get("usage")
                    if usage:
                        final_usage = usage

                    choices = evt.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    finish = choice.get("finish_reason")

                    delta_text = delta.get("content")
                    tool_call_delta = None
                    if delta.get("tool_calls"):
                        tool_call_delta = {"tool_calls": delta["tool_calls"]}

                    if finish:
                        final_finish = finish
                        yield StreamChunk(
                            delta_text=delta_text,
                            tool_call_delta=tool_call_delta,
                            finish_reason=finish,
                            usage=final_usage,
                        )
                    elif delta_text or tool_call_delta:
                        yield StreamChunk(
                            delta_text=delta_text,
                            tool_call_delta=tool_call_delta,
                        )
