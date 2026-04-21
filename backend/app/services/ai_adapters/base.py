"""Shared Protocol for AI Library provider adapters.

All adapters must expose an async `call(composed, messages) -> dict` method
that returns OpenAI-compatible response JSON (so AgentRunner can parse
`choices[0].message.tool_calls` the same way for any provider).
"""

from __future__ import annotations

from typing import Any, Dict, List, Protocol

from app.schemas.ai_library import ComposedSystemPrompt


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
