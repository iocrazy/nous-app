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
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from loguru import logger

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters._model_routing import resolve_wire_model
from app.services.ai.adapters.base import StreamChunk
from app.services.ai.runner.reasoning import (
    MIN_REASONING_MAX_TOKENS,
    model_uses_reasoning,
)


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
        *,
        tool_choice: Optional[Any] = None,
    ) -> Dict[str, Any]:
        # Audit #8 (fix C): route-authoritative — the wire model must match
        # the model this adapter resolved its endpoint + key for.
        wire_model = resolve_wire_model(composed.model, self.default_model)
        max_tokens = composed.max_tokens
        # Reasoning models (Qwen3 <think>) spend 1-2k tokens thinking BEFORE the
        # answer; a small cap truncates mid-thought, leaving no answer at all.
        # Floor it so there's room for thinking + answer.
        if model_uses_reasoning(wire_model) and (
            max_tokens is None or max_tokens < MIN_REASONING_MAX_TOKENS
        ):
            max_tokens = MIN_REASONING_MAX_TOKENS
        body: Dict[str, Any] = {
            "model": wire_model,
            "temperature": composed.temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": composed.system_message},
                *messages,
            ],
        }
        if composed.tools:
            body["tools"] = composed.tools
            # FinishIssue forced-declaration fallback (issue lifecycle): a
            # caller that needs the model to emit ONE specific tool call
            # (e.g. {"type": "function", "function": {"name": "FinishIssue"}})
            # passes tool_choice explicitly. Every existing caller leaves it
            # None, so this is a pure default-unchanged addition — "auto"
            # still wins whenever tool_choice isn't given.
            body["tool_choice"] = tool_choice if tool_choice is not None else "auto"
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
        *,
        tool_choice: Optional[Any] = None,
    ) -> Dict[str, Any]:
        body = self._build_body(composed, messages, tool_choice=tool_choice)
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

        A1 (needs_input first-class, Task 5 fix-2): a provider with
        ``stream_options.include_usage=true`` (confirmed: Volcengine/Doubao)
        does NOT bundle usage onto the same chunk as ``finish_reason`` — it
        sends the terminal usage as its OWN trailing chunk with an EMPTY
        ``choices`` array, arriving AFTER the finish_reason chunk. The
        AgentRunner stream consumer stops iterating this generator the
        instant it sees a chunk with ``finish_reason`` set, so whichever
        usage value is on THAT chunk is final. We therefore hold the
        finish-bearing chunk back (``pending_finish``) instead of yielding
        it immediately, so a following usage-only line can still fill it in
        before the caller ever sees it — restoring the "usage populated on
        the final chunk" contract regardless of which shape the provider
        uses.
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
                pending_finish: StreamChunk | None = None

                async for raw in resp.aiter_lines():
                    if not raw or not raw.startswith("data:"):
                        continue
                    payload = raw[len("data:") :].strip()
                    if payload == "[DONE]":
                        if pending_finish is not None:
                            # No trailing usage chunk showed up before
                            # [DONE] — flush with whatever usage we have
                            # (possibly still None; provider genuinely
                            # never sent it).
                            yield StreamChunk(
                                delta_text=pending_finish.delta_text,
                                tool_call_delta=pending_finish.tool_call_delta,
                                finish_reason=pending_finish.finish_reason,
                                usage=final_usage,
                            )
                        elif final_finish is None:
                            # Emit terminal chunk if not already (some
                            # providers send finish_reason on a separate
                            # line; others bundle it with the [DONE] line).
                            yield StreamChunk(finish_reason="stop", usage=final_usage)
                        return
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    # Capture usage if present on this event. Per the
                    # two-chunk-tail shape, this often arrives on its OWN
                    # event (choices=[]) strictly after the finish_reason
                    # event — if we're holding a pending finish chunk, this
                    # is exactly what it was waiting for: flush it now.
                    usage = evt.get("usage")
                    if usage:
                        final_usage = usage
                        if pending_finish is not None:
                            yield StreamChunk(
                                delta_text=pending_finish.delta_text,
                                tool_call_delta=pending_finish.tool_call_delta,
                                finish_reason=pending_finish.finish_reason,
                                usage=final_usage,
                            )
                            return

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
                        # Don't yield yet — a trailing usage-only chunk may
                        # still be coming (see docstring). Flushed above on
                        # the next usage sighting, or at [DONE] otherwise —
                        # or, failing both, by the fallback below if the
                        # connection closes first.
                        pending_finish = StreamChunk(
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

                # A1 (Task 5 fix-3): ``aiter_lines()`` can exhaust with no
                # exception on a silent connection close (SSE/network
                # flake) — real, not hypothetical. If that happens right
                # after the finish_reason line but before either a usage
                # tail chunk or ``[DONE]`` shows up, the loop above exits
                # normally and — without this fallback — the held-back
                # ``pending_finish`` (its real finish_reason, e.g. 'length'
                # or 'tool_calls', plus any delta_text/tool_call_delta
                # riding it) would be silently dropped. Emit it as-is;
                # usage stays whatever final_usage holds (often still None
                # here — an honest "never arrived", not fabricated).
                if pending_finish is not None:
                    yield StreamChunk(
                        delta_text=pending_finish.delta_text,
                        tool_call_delta=pending_finish.tool_call_delta,
                        finish_reason=pending_finish.finish_reason,
                        usage=final_usage,
                    )
