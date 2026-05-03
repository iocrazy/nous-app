"""Shared Protocol for AI Library provider adapters.

All adapters must expose an async `call(composed, messages) -> dict` method
that returns OpenAI-compatible response JSON (so AgentRunner can parse
`choices[0].message.tool_calls` the same way for any provider).

Wave H (B): adapters MAY also expose ``stream(composed, messages)`` →
``AsyncIterator[StreamChunk]`` for incremental output. Adapters that
don't implement streaming raise ``StreamingNotSupported``; caller falls
back to ``call()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol

from app.schemas.ai_library import ComposedSystemPrompt


class StreamingNotSupported(Exception):
    """Adapter doesn't implement stream(). Caller may fall back to call()."""


@dataclass(frozen=True)
class StreamChunk:
    """One delta from streaming response.

    Adapters normalize whatever their provider sends into this shape.
    On the FINAL chunk (when finish_reason is set), usage info SHOULD
    be populated if the provider supplies it. AgentRunner uses
    finish_reason='length' to drive auto-continue (Wave 5c C2).
    """

    delta_text: Optional[str] = None
    tool_call_delta: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class AIAdapter(Protocol):
    """Contract every provider adapter must satisfy.

    Inputs:
    - ``composed``: the system prompt bundle from PromptComposer carrying
      system-message text, tool schemas, model id, temperature, max_tokens,
      and an optional cache fingerprint.
    - ``messages``: user/assistant/tool conversation history as a list of
      ``{"role", "content"}`` dicts (plus ``{"tool_call_id", "name"}`` for
      tool replies).

    Output: an OpenAI-compatible chat-completion response dict with the
    shape AgentRunner expects::

        {
          "choices": [
            {
              "message": {
                "role": "assistant",
                "content": "text or null",
                "tool_calls": [  # optional
                  {"id": "...", "type": "function",
                   "function": {"name": "Skill", "arguments": "{...}"}}
                ]
              },
              "finish_reason": "stop" | "tool_calls"
            }
          ]
        }

    Non-OpenAI providers (e.g. Anthropic) MUST normalize their response
    into this shape inside their adapter — AgentRunner never sees the
    native format.
    """

    async def call(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]: ...


class StreamingAIAdapter(Protocol):
    """Optional protocol — adapter that supports incremental streaming.

    ``stream()`` MUST yield StreamChunk instances ending with one where
    finish_reason is set. Implementations should also satisfy the base
    AIAdapter protocol so callers can fall back to buffered ``call()``
    when streaming isn't desired.
    """

    async def call(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]: ...

    def stream(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
    ) -> AsyncIterator[StreamChunk]: ...
