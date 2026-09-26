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

from app.boundary.system_note import render_system_note
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
    # Anthropic's ``input_tokens`` EXCLUDES cache reads and cache writes;
    # the OpenAI shape (and ``usage_cached``) treats cached tokens as a SUBSET
    # of ``prompt_tokens``. So prompt = uncached + cache read + cache write,
    # and ``cached_tokens`` = the read part (fh4 T5 review M1).
    cached = _tokens(usage, "cache_read_input_tokens")
    prompt = (
        _tokens(usage, "input_tokens")
        + cached
        + _tokens(usage, "cache_creation_input_tokens")
    )
    completion = _tokens(usage, "output_tokens")
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
    }


def _tool_use_block(tc: Dict[str, Any]) -> Dict[str, Any]:
    fn = tc["function"]
    try:
        args = json.loads(fn["arguments"])
    except (json.JSONDecodeError, TypeError):
        args = {}
    return {"type": "tool_use", "id": tc["id"], "name": fn["name"], "input": args}


def _system_text(content: Any) -> str:
    """A system message's text. List content keeps only its text parts."""
    if isinstance(content, list):
        parts = [
            p if isinstance(p, str) else str(p.get("text") or "")
            for p in content
            if isinstance(p, str) or (isinstance(p, dict) and p.get("type") == "text")
        ]
        return "\n".join(p for p in parts if p)
    return str(content or "")


def _convert_one(m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    role = m.get("role")
    if role == "user":
        return {"role": "user", "content": m["content"]}
    if role == "system":
        note = render_system_note(_system_text(m.get("content")))
        return {"role": "user", "content": [{"type": "text", "text": note}]}
    if role == "assistant":
        blocks: List[Dict[str, Any]] = []
        if m.get("content"):
            blocks.append({"type": "text", "text": m["content"]})
        blocks.extend(_tool_use_block(tc) for tc in m.get("tool_calls") or [])
        return {"role": "assistant", "content": blocks}
    if role == "tool":
        block = {
            "type": "tool_result",
            "tool_use_id": m["tool_call_id"],
            "content": m["content"],
        }
        return {"role": "user", "content": [block]}
    logger.warning(f"[ClaudeAdapter] dropping message with unknown role={role!r}")
    return None


def _as_blocks(content: Any) -> List[Any]:
    if isinstance(content, list):
        return list(content)
    text = str(content or "")
    return [{"type": "text", "text": text}] if text else []


def _is_tool_result(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_result"


def _merge_adjacent(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fold each run of same-role turns into one turn with a block list.

    The runner can place a user-role message between two tool results of one
    assistant turn (a loop-guard note, a promoted ResourceFetch image). The
    API wants the ``tool_result`` blocks to lead the user turn that answers a
    ``tool_use`` turn, so in a merged user turn every ``tool_result`` block is
    hoisted to the front (relative order kept) and the rest follow in order.
    A turn that has no same-role neighbour is passed through untouched.
    """
    runs: List[List[Dict[str, Any]]] = []
    for m in messages:
        if runs and runs[-1][0]["role"] == m["role"]:
            runs[-1].append(m)
        else:
            runs.append([m])
    return [run[0] if len(run) == 1 else _merge_run(run) for run in runs]


def _merge_run(run: List[Dict[str, Any]]) -> Dict[str, Any]:
    role = run[0]["role"]
    blocks = [b for m in run for b in _as_blocks(m["content"])]
    if role == "user":
        blocks = [b for b in blocks if _is_tool_result(b)] + [
            b for b in blocks if not _is_tool_result(b)
        ]
    return {"role": role, "content": blocks}


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
        - Mid-list role=system (compaction summary, loop-guard warning, a
          fork's summary row) → a user turn at the SAME index whose text is
          ``render_system_note(...)``. Anthropic has no mid-list system role;
          the top-level system prompt is passed separately by ``call()``.
        - Adjacent same-role turns are then merged (see ``_merge_adjacent``).
        """
        converted = [_convert_one(m) for m in openai_messages]
        return _merge_adjacent([m for m in converted if m is not None])

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
