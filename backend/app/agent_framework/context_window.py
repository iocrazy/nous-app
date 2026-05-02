"""Context window guard — small-model protection.

When a small-context model (4k-8k window) is paired with a heavy AGENT
spec (verbose IDENTITY+SOUL+AGENT.md), the system prompt eats most of
the window with no room for user input. The LLM call returns truncated
nonsense or fails outright with cryptic provider errors.

This module does pre-flight check: estimate tokens of (system +
user_messages), compare against the model's known context window,
warn at 50% / reject at 80%.

Mirrors OpenClaw ``agents/context-window-guard.ts`` thresholds and
4-source pattern.

Usage from agent_runner:

    from app.agent_framework import check_context_budget, ContextWindowError

    try:
        check_context_budget(
            system_prompt=composed.system,
            user_messages=user_messages,
            model=composed.model,
        )
    except ContextWindowError as e:
        # Reject the run — return a structured error to the caller
        # instead of letting the LLM call fail mysteriously.
        raise
"""
from __future__ import annotations

import warnings
from typing import Any, Iterable, Optional

from app.core.config import settings

# Warn when system+user takes >= this fraction of the window.
WARN_RATIO: float = 0.5
# Reject when system+user takes >= this fraction of the window.
ERROR_RATIO: float = 0.8

# Approximation: ≈4 chars per token for English/code, ≈1.5 chars for
# Chinese (per OpenAI tokenizer norms). Use the conservative ENG figure
# of 4 — overcounts Chinese slightly which is OK (we'd rather warn early
# than underestimate).
_CHARS_PER_TOKEN: float = 4.0

# Known model windows. Conservative values — providers may publish
# higher caps but real-world usable budget is often less due to provider
# overhead. When unknown, falls back to settings.LLM_MAX_CONTEXT_TOKENS.
_MODEL_WINDOWS: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    # Anthropic
    "claude-opus-4-7": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-3-5-sonnet": 200_000,
    # Qwen
    "qwen-max": 32_768,
    "qwen-plus": 131_072,
    "qwen-turbo": 1_000_000,
    "qwen2.5-72b": 131_072,
    # Doubao
    "doubao-pro-32k": 32_768,
    "doubao-pro-128k": 131_072,
    "doubao-1-5-pro-32k": 32_768,
    "doubao-1-5-pro-128k": 131_072,
    # DeepSeek
    "deepseek-chat": 65_536,
    "deepseek-reasoner": 65_536,
    "deepseek-v3": 65_536,
    # Common small/local
    "llama-3-8b": 8_192,
    "llama-3.1-8b": 131_072,
    "llama-3.1-70b": 131_072,
    "mistral-7b": 32_768,
    "phi-3-mini": 4_096,
    "phi-3.5-mini": 131_072,
}


class ContextWindowError(Exception):
    """The composed prompt + user input exceeds the model's safe budget.

    Raised at pre-flight (before LLM call). Caller should reject the
    run with a structured error rather than letting the LLM call fail
    with a cryptic provider message.
    """


class ContextWindowWarning(UserWarning):
    """Warned when prompt size is high but not blocking. Caller can
    handle (log + downgrade to a larger model) or ignore."""


def estimate_tokens(text: str) -> int:
    """Cheap token estimate. Char-count / 4 — good enough for budgeting,
    not exact.

    For exact counts the caller should run the model's actual tokenizer
    (tiktoken for OpenAI, etc.). We deliberately don't depend on
    tiktoken here so this guard works for any provider.
    """
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def model_window_size(model: str) -> int:
    """Return the safe context window for ``model`` in tokens.

    Lookup is case-insensitive. Unknown models fall back to
    settings.LLM_MAX_CONTEXT_TOKENS so behaviour is predictable for
    user-configured custom models.
    """
    if not model:
        return settings.LLM_MAX_CONTEXT_TOKENS
    key = model.strip().lower()
    if key in _MODEL_WINDOWS:
        return _MODEL_WINDOWS[key]
    # Try base model name (drop -date / -instruct / -chat suffix)
    base = key.split(":")[0].split("-instruct")[0].split("-chat")[0]
    if base in _MODEL_WINDOWS:
        return _MODEL_WINDOWS[base]
    return settings.LLM_MAX_CONTEXT_TOKENS


def _extract_text_from_user_messages(
    user_messages: Optional[Iterable[dict[str, Any]]],
) -> str:
    """Pull plain text out of OpenAI-shape user_messages, ignoring
    multimodal image blocks (those are tokenized separately by the
    model and out of scope for this estimator)."""
    if not user_messages:
        return ""
    parts: list[str] = []
    for msg in user_messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            # Multimodal: take only text blocks
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str):
                        parts.append(text)
        # else: dict / None / other — skip
    return "\n".join(parts)


def check_context_budget(
    *,
    system_prompt: str,
    user_messages: Optional[Iterable[dict[str, Any]]] = None,
    model: str,
) -> None:
    """Pre-flight check before sending to LLM. Raise or warn.

    Args:
        system_prompt: The composed system message (IDENTITY+SOUL+AGENT
            + skills + cache boundary etc.).
        user_messages: OpenAI-shape user messages. Multimodal image
            blocks are skipped (image tokens are a separate budget).
        model: The model the request will go to.

    Raises:
        ContextWindowError: total tokens >= ERROR_RATIO * window.
            Reject the run; the LLM call will fail or truncate.
    """
    window = model_window_size(model)
    user_text = _extract_text_from_user_messages(user_messages)
    total_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_text)

    if total_tokens >= int(window * ERROR_RATIO):
        raise ContextWindowError(
            f"context budget exceeded: ~{total_tokens} tokens "
            f"vs window {window} (model={model!r}). "
            f"Use a larger-context model or trim the AGENT spec / user input."
        )

    if total_tokens >= int(window * WARN_RATIO):
        warnings.warn(
            f"context budget high: ~{total_tokens} tokens vs window "
            f"{window} (model={model!r}). Consider a larger-context model.",
            ContextWindowWarning,
            stacklevel=2,
        )
