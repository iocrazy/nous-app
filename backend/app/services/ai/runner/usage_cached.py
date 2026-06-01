"""Extract cached-input-token counts from a provider usage dict.

Providers expose prompt-cache hits under different keys; all treat cached
tokens as a SUBSET of ``prompt_tokens``:
- OpenAI / DashScope(Qwen): ``usage.prompt_tokens_details.cached_tokens``
- DeepSeek: ``usage.prompt_cache_hit_tokens``
"""

from __future__ import annotations

from typing import Any, Optional


def extract_cached_input_tokens(usage: Optional[dict[str, Any]]) -> int:
    """Return cached input tokens from a provider usage dict, or 0."""
    if not usage or not isinstance(usage, dict):
        return 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        c = details.get("cached_tokens")
        if isinstance(c, int) and c >= 0:
            return c
    hit = usage.get("prompt_cache_hit_tokens")
    if isinstance(hit, int) and hit >= 0:
        return hit
    return 0
