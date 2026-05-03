"""Token counter — model-aware estimator.

Wave 5a (A1). Replaces the ``chars // 4`` heuristic that
``llm_compactor.estimate_tokens`` has been using. The chars heuristic
under-counts Chinese (1 char ≈ 1-2 tokens) and dense JSON / code
(1 char ≈ 0.5 tokens), which has two practical consequences:

  - Compaction triggers at the wrong time: 100k chars-divided-by-4 may
    actually be 130k real tokens — past 128k window of qwen-plus.
  - Tail-budget calculations under-count so we keep too much / too little.

This module gives one entry point ``count_tokens(text, model)`` that:
  - Uses the appropriate provider tokenizer when its package is installed
    (``tiktoken`` for OpenAI-family, ``dashscope`` for Qwen).
  - Falls back to a smarter char heuristic when the package is missing
    (3 for Chinese-heavy text, 4 for ASCII-heavy).

Optional packages — missing them is not a failure, just degrades to
the heuristic. We deliberately don't ``raise ImportError`` so the
estimator path is always callable in CI / lightweight deploys.

Public surface:
  count_tokens(text, model="") -> int
  count_messages_tokens(messages, model="") -> int
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Any, Optional


# ─── Provider detection ───────────────────────────────────────────────


def _provider_for_model(model: str) -> str:
    """Map model name → provider key. Conservative — anything we can't
    classify falls into 'unknown' (uses heuristic)."""
    m = (model or "").lower()
    if not m:
        return "unknown"
    if m.startswith(("gpt-", "o1", "o3", "text-embedding-", "chatgpt")):
        return "openai"
    if m.startswith(("qwen", "qwq")):
        return "qwen"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("doubao"):
        return "doubao"
    if m.startswith(("claude-", "anthropic")):
        return "anthropic"
    return "unknown"


# ─── Optional: tiktoken (OpenAI) ─────────────────────────────────────


@lru_cache(maxsize=8)
def _try_tiktoken_encoding(model: str) -> Optional[Any]:
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        # Unknown model name — try the cl100k base which covers most
        # OpenAI chat completions.
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def _count_openai(text: str, model: str) -> Optional[int]:
    enc = _try_tiktoken_encoding(model)
    if enc is None:
        return None
    try:
        return len(enc.encode(text))
    except Exception:
        return None


# ─── Optional: dashscope (Qwen) ──────────────────────────────────────


@lru_cache(maxsize=4)
def _has_dashscope() -> bool:
    try:
        import dashscope  # type: ignore[import-not-found]  # noqa: F401
        return True
    except ImportError:
        return False


def _count_qwen(text: str, _model: str) -> Optional[int]:
    if not _has_dashscope():
        return None
    try:
        from dashscope import Tokenization  # type: ignore[import-not-found]
        # Dashscope's Tokenization API counts tokens for Qwen models.
        # In offline mode we don't actually want a network call — guard
        # against any HTTP attempt by catching all exceptions.
        result = Tokenization.call(model="qwen-turbo", messages=[{"role": "user", "content": text}])
        usage = getattr(result, "usage", None)
        if usage and "input_tokens" in usage:
            return int(usage["input_tokens"])
    except Exception:
        return None
    return None


# ─── Heuristic fallback — language-aware ──────────────────────────────


def _is_cjk(ch: str) -> bool:
    """True if ch is in CJK Unified Ideographs / Hiragana / Katakana /
    Hangul block. These are 1-token-each in most BPE tokenizers."""
    if not ch:
        return False
    cp = ord(ch)
    # CJK Unified Ideographs (extensions A, B, C, D, E, F, G all included)
    if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
        return True
    if 0x20000 <= cp <= 0x3134F:
        return True
    # Hiragana + Katakana
    if 0x3040 <= cp <= 0x30FF:
        return True
    # Hangul syllables
    if 0xAC00 <= cp <= 0xD7AF:
        return True
    return False


def _heuristic_count(text: str) -> int:
    """Mixed-language tokenizer guess.

    Strategy: count CJK chars as 1 token each; ASCII / latin chars as
    chars/4. This is dramatically more accurate than the original
    chars/4 for mixed Chinese-English content (which is most of mediahub).

    Empty string → 0. Non-string input → 0 (defensive)."""
    if not isinstance(text, str) or not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
        else:
            other += 1
    return cjk + (other // 4)


# ─── Public API ──────────────────────────────────────────────────────


def count_tokens(text: str, model: str = "") -> int:
    """Count tokens in ``text`` using the best available tokenizer for
    ``model``. Falls back to language-aware heuristic when no provider
    package is installed.

    ``model="" `` → straight to heuristic (no model-specific tokenizer).
    """
    if not isinstance(text, str) or not text:
        return 0

    provider = _provider_for_model(model)

    # Try provider-specific real tokenizer
    if provider == "openai":
        n = _count_openai(text, model)
        if n is not None:
            return n
    elif provider == "qwen":
        n = _count_qwen(text, model)
        if n is not None:
            return n
    # deepseek / doubao / anthropic: no offline tokenizer commonly
    # available; could add later. Use heuristic for now.

    return _heuristic_count(text)


def count_messages_tokens(messages: list[dict], model: str = "") -> int:
    """Count tokens in a chat-style messages list.

    Walks each message's ``content`` (string or multi-part list) plus
    ``tool_calls`` (function name + JSON args). Adds a small per-message
    overhead (4 tokens) to approximate role/control-token framing —
    matches OpenAI's documented overhead for cl100k models.
    """
    if not messages:
        return 0
    total = 0
    PER_MESSAGE_OVERHEAD = 4  # role + control tokens per message
    for msg in messages:
        total += PER_MESSAGE_OVERHEAD
        content = msg.get("content")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            # Anthropic multi-part: list of {"type": "text", "text": ...} etc
            for part in content:
                if isinstance(part, dict):
                    t = part.get("text") or part.get("content") or ""
                    if isinstance(t, str):
                        total += count_tokens(t, model)
                    else:
                        total += count_tokens(str(part), model)
                else:
                    total += count_tokens(str(part), model)
        # tool_calls: assistant role attaches function calls
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            total += count_tokens(str(fn.get("name") or ""), model)
            total += count_tokens(str(fn.get("arguments") or ""), model)
    return total


__all__ = [
    "count_messages_tokens",
    "count_tokens",
]
