"""ClaudeAdapter — Anthropic Messages API via the official SDK.

Claude's tool_use / tool_result shape differs from OpenAI's tool_calls /
tool role. We normalize at the adapter boundary so AgentRunner stays
provider-agnostic. Prompt-caching (cache_control) is deferred to a
follow-up PR — the fingerprint field is accepted but not yet honored.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from anthropic import AsyncAnthropic
from loguru import logger

from app.schemas.ai_library import ComposedSystemPrompt
from app.services.ai.adapters._model_routing import resolve_wire_model
from app.services.ai.provider_contract import normalize_envelope

# Anthropic ``stop_reason`` → OpenAI ``finish_reason``. Everything used to
# collapse to "stop", so a ``max_tokens`` cut looked like a complete answer and
# a ``refusal`` looked like an empty one (fh4 T5). ``pause_turn`` (server-tool
# pause) has no OpenAI twin; it is an unfinished turn, closest to ``length``.
_STOP_REASON_TO_FINISH: Dict[str, str] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "pause_turn": "length",
    "refusal": "content_filter",
}


def _finish_reason(stop_reason: Any, has_tool_calls: bool) -> str:
    mapped = _STOP_REASON_TO_FINISH.get(str(stop_reason or ""), "stop")
    if mapped == "stop" and has_tool_calls:
        return "tool_calls"
    return mapped


def _tokens(usage: Any, name: str) -> int:
    value = getattr(usage, name, None)
    return value if isinstance(value, int) else 0


def _usage(usage: Any) -> Optional[Dict[str, Any]]:
    """Anthropic ``usage`` → the OpenAI shape RunRecorder bills from. It used
    to be dropped, so every Claude turn was recorded as free."""
    if usage is None:
        return None
    prompt = _tokens(usage, "input_tokens")
    completion = _tokens(usage, "output_tokens")
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {
            "cached_tokens": _tokens(usage, "cache_read_input_tokens")
        },
    }


class ClaudeAdapter:
    """Anthropic Messages API adapter with OpenAI-shape output."""

    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-opus-4-5",
        timeout_seconds: float = 60.0,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)
        self.default_model = default_model

    # ── Tool schema conversion ────────────────────────────────────────

    def _convert_tools(
        self, openai_tools: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert OpenAI tools ``[{type:function, function:{name,parameters,...}}]``
        into Anthropic tools ``[{name, description, input_schema}]``."""
        out: List[Dict[str, Any]] = []
        for t in openai_tools:
            fn = t.get("function", {})
            out.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object"}),
                }
            )
        return out

    # ── Message conversion (OpenAI → Anthropic) ───────────────────────

    def _convert_messages(
        self, openai_messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert OpenAI-shape messages into Anthropic shape.

        - OpenAI assistant turn with tool_calls → Anthropic assistant with
          content = [{type:text,...}, {type:tool_use, id, name, input}]
        - OpenAI role=tool → Anthropic role=user with
          content = [{type:tool_result, tool_use_id, content}]
        - System message is passed separately (not a message role).
        """
        anthropic_msgs: List[Dict[str, Any]] = []
        for m in openai_messages:
            role = m.get("role")
            if role == "user":
                anthropic_msgs.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                blocks: List[Dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    fn = tc["function"]
                    try:
                        args = json.loads(fn["arguments"])
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": fn["name"],
                            "input": args,
                        }
                    )
                anthropic_msgs.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                anthropic_msgs.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m["tool_call_id"],
                                "content": m["content"],
                            }
                        ],
                    }
                )
        return anthropic_msgs

    # ── Response normalization (Anthropic → OpenAI) ───────────────────

    def _normalize_response(self, resp: Any) -> Dict[str, Any]:
        """Convert Anthropic Messages API response into OpenAI
        chat-completion shape."""
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        for block in resp.content or []:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            # OpenAI contract: arguments is a JSON string
                            "arguments": json.dumps(block.input),
                        },
                    }
                )

        message: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts) if text_parts else None,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls

        envelope: Dict[str, Any] = {
            "choices": [
                {
                    "message": message,
                    "finish_reason": _finish_reason(
                        getattr(resp, "stop_reason", None), bool(tool_calls)
                    ),
                }
            ]
        }
        usage = _usage(getattr(resp, "usage", None))
        if usage is not None:
            envelope["usage"] = usage
        return envelope

    # ── Public API ────────────────────────────────────────────────────

    async def call(
        self,
        composed: ComposedSystemPrompt,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # Audit #8 (fix C): ClaudeAdapter builds the request itself (Anthropic
        # Messages format, no _build_body), so it needs the same route-authoritative
        # guard as OpenAICompatibleAdapter — otherwise a misrouted composed.model
        # would be sent to a Claude endpoint resolved for a different model.
        model = resolve_wire_model(composed.model, self.default_model)
        anthropic_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(composed.tools or [])

        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": composed.max_tokens,
            "temperature": composed.temperature,
            "system": composed.system_message,
            "messages": anthropic_messages,
        }
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        resp = await self._client.messages.create(**kwargs)
        logger.debug(
            f"[ClaudeAdapter] model={model} "
            f"stop_reason={getattr(resp, 'stop_reason', 'unknown')}"
        )
        return normalize_envelope(self._normalize_response(resp), model=model)
