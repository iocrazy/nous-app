"""Output token budget — cap assistant generation per turn.

Wave 5c (C2). Without an enforced cap on `max_tokens`, an LLM can
produce a runaway 8k+ token reply that:
  - eats half the model window in a single turn
  - costs 4x the budget
  - may exceed the model's own output limit and 400

This module gives:
  - ``derive_output_budget(model, system_tokens, history_tokens)``:
    compute a sensible per-turn cap based on remaining window

It does NOT call the LLM — adapter layer enforces the budget by
passing it to provider's max_tokens param. This module just centralizes
the policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent_framework.context_window import model_window_size

# Defaults. Per-model overrides via DEFAULT_OUTPUT_BUDGET_BY_MODEL.
DEFAULT_OUTPUT_FRACTION = 0.25
DEFAULT_MIN_OUTPUT_TOKENS = 512
DEFAULT_MAX_OUTPUT_TOKENS = 8_192


# Per-model hard caps the provider enforces — we should never send
# max_tokens above this.
PROVIDER_HARD_CAPS: dict[str, int] = {
    "qwen-max": 8_192,
    "qwen-plus": 8_192,
    "qwen-turbo": 8_192,
    "gpt-4o": 16_384,
    "gpt-4o-mini": 16_384,
    "claude-sonnet-4-6": 64_000,  # Anthropic high
    "claude-opus-4-7": 64_000,
}


@dataclass(frozen=True)
class OutputBudget:
    """Computed per-turn output cap + diagnostic info."""

    max_tokens: int
    model: str
    window_size: int
    consumed_input: int
    remaining_for_output: int


def derive_output_budget(
    *,
    model: str,
    consumed_input_tokens: int,
    fraction: float = DEFAULT_OUTPUT_FRACTION,
    min_output: int = DEFAULT_MIN_OUTPUT_TOKENS,
    max_output: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> OutputBudget:
    """Compute a safe ``max_tokens`` for the next LLM call.

    Logic:
      - window = model_window_size(model) (defaults to 32k for unknown)
      - remaining = window - consumed_input
      - target = remaining * fraction
      - clamp to [min_output, min(max_output, provider_hard_cap)]
      - if remaining < min_output: model can't reply at all — caller
        should compact first; we still return min_output so a tiny
        reply might fit if consumed_input was over-counted.
    """
    window = model_window_size(model)
    remaining = max(0, window - consumed_input_tokens)
    target = int(remaining * fraction)

    hard_cap = PROVIDER_HARD_CAPS.get(model, max_output)
    upper = min(hard_cap, max_output)
    bounded = max(min_output, min(target, upper))
    return OutputBudget(
        max_tokens=bounded,
        model=model,
        window_size=window,
        consumed_input=consumed_input_tokens,
        remaining_for_output=remaining,
    )


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MIN_OUTPUT_TOKENS",
    "DEFAULT_OUTPUT_FRACTION",
    "OutputBudget",
    "PROVIDER_HARD_CAPS",
    "derive_output_budget",
]
