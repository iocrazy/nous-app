"""Context window guard — small-model protection.

When a small-context model (4k-8k window) is paired with a heavy AGENT
spec (verbose IDENTITY+SOUL+AGENT.md), the system prompt eats most of
the window with no room for user input. The LLM call returns truncated
nonsense or fails outright with cryptic provider errors.

This module does pre-flight check: count tokens of (system +
user_messages), compare against the model's known context window,
warn at 50% / reject at ``REJECT_RATIO`` (90%).

It counts with the compactor's ruler (``tokenizer.count_tokens`` /
``count_messages_tokens``: CJK = 1 token per char, tool_calls and
per-message framing included), and the rejection line is the compactor's
red threshold — one constant, imported there. Two rulers used to disagree:
chars/4 made the guard ~4x too lenient on Chinese history, and an emergency
cap targeting 0.80 left the turn only ~1% under the old 0.80 rejection line,
so whether it was rejected anyway came down to the tokenizer.

Usage from agent_runner:

    from app.agent_framework import check_context_budget, ContextWindowError

    try:
        check_context_budget(
            system_prompt=composed.system,
            user_messages=user_messages,
            model=composed.model,
            measured_tokens=stats.tokens_after,  # optional: compactor's count
        )
    except ContextWindowError as e:
        # Reject the run — return a structured error to the caller
        # instead of letting the LLM call fail mysteriously.
        raise
"""

from __future__ import annotations

import warnings
from typing import Any, Iterable

from app.agent_framework.catalog_windows import catalog_window
from app.agent_framework.tokenizer import count_messages_tokens, count_tokens
from app.core.config import settings

# Warn when system+user takes >= this fraction of the window.
WARN_RATIO: float = 0.5
# Reject when system+user takes >= this fraction of the window. This is ALSO
# the compactor's red tier (``CompactionThresholds.red_pct`` imports it), so
# the tier that compacts hardest and the line that rejects cannot drift
# apart. Order pinned by tests/agent_framework/test_budget_tokenizer_parity.py:
# emergency target 0.70 < orange 0.80 < red == reject 0.90; the last 10 % is
# left for the reply (``derive_output_budget``).
REJECT_RATIO: float = 0.90

# chars/4 for ``estimate_tokens`` only — a public quick estimate. The budget
# guard does NOT use it (see ``check_context_budget``).
_CHARS_PER_TOKEN: float = 4.0

# Known model windows — the SECOND layer. The provider catalog
# (``nous_models.context_window_tokens``, migration 500) is consulted first;
# this table answers only models the catalog has no value for. Conservative
# values — providers may publish higher caps but real-world usable budget is
# often less due to provider overhead. When neither knows the model, falls
# back to settings.LLM_MAX_CONTEXT_TOKENS.
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
    # Public docs list 256k for the Seed 2.0 family — UNVERIFIED against the
    # Volcengine console, so the conservative 128k until someone confirms it.
    # Migration 500 seeds the same value into the catalog; the admin may raise
    # it there, and the catalog wins over this line.
    "doubao-seed-2-0-lite-260428": 131_072,
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
    """Cheap token estimate. Char-count / 4 — a rough number for display.

    Not what ``check_context_budget`` counts with: it undercounts CJK ~4x.
    Budget decisions go through ``tokenizer.count_messages_tokens``.

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
    window, _ = resolve_model_window(model)
    return window


def resolve_model_window(model: str) -> tuple[int, bool]:
    """``(window, is_known)`` — the size, plus whether we actually knew it.

    ``model_window_size()`` answers every model, which makes the generic
    fallback indistinguishable from a real lookup at the call site. Callers
    that divide by this number (the compactor decides all four tiers by
    ``used / window``) need to be able to say whether the denominator was
    knowledge or a default, so they can report it instead of quietly acting
    on a guess.

    Production 2026-08-23: 12 of 20 configured agents run models missing from
    the table (``doubao-seed-2-0-lite-260428``, ``nous-qwen3-llm``), so their
    tier decisions have all been made against ``LLM_MAX_CONTEXT_TOKENS``.

    Lookup order: provider catalog (``nous_models.context_window_tokens``, by
    actual model id or catalog name) → ``_MODEL_WINDOWS`` → the fallback.
    Catalog and table hits are both "known"; only the fallback is not.
    """
    if not model:
        return settings.LLM_MAX_CONTEXT_TOKENS, False
    from_catalog = catalog_window(model)
    if from_catalog is not None:
        return from_catalog, True
    key = model.strip().lower()
    if key in _MODEL_WINDOWS:
        return _MODEL_WINDOWS[key], True
    # Try base model name (drop -date / -instruct / -chat suffix)
    base = key.split(":")[0].split("-instruct")[0].split("-chat")[0]
    if base in _MODEL_WINDOWS:
        return _MODEL_WINDOWS[base], True
    return settings.LLM_MAX_CONTEXT_TOKENS, False


def check_context_budget(
    *,
    system_prompt: str,
    user_messages: Iterable[dict[str, Any]] | None = None,
    model: str,
    measured_tokens: int | None = None,
) -> None:
    """Pre-flight check before sending to LLM. Raise or warn.

    Args:
        system_prompt: The composed system message (IDENTITY+SOUL+AGENT
            + skills + cache boundary etc.).
        user_messages: OpenAI-shape messages, counted with
            ``count_messages_tokens`` (text parts, tool_calls, per-message
            framing; image blocks are a separate budget and count 0).
        model: The model the request will go to.
        measured_tokens: system + messages already counted by the compactor
            (``CompactionStats.tokens_after``) with the same functions. When
            given it IS the count and the history is not scanned again;
            ``None`` means "not measured, count here".

    Raises:
        ContextWindowError: total tokens >= REJECT_RATIO * window.
            Reject the run; the LLM call will fail or truncate.
    """
    window = model_window_size(model)
    if measured_tokens is None:
        total_tokens = count_tokens(system_prompt or "", model) + count_messages_tokens(
            list(user_messages or ()), model
        )
    else:
        total_tokens = measured_tokens

    if total_tokens >= int(window * REJECT_RATIO):
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
