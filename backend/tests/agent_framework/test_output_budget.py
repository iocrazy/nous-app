"""C2 — output token budget."""

from __future__ import annotations

import pytest

from app.agent_framework.output_budget import (
    DEFAULT_MIN_OUTPUT_TOKENS,
    PROVIDER_HARD_CAPS,
    derive_output_budget,
    should_auto_continue,
)


@pytest.mark.unit
def test_budget_uses_remaining_window_fraction():
    """qwen-max window=32768; consumed=10k → remaining=22768; 25%=5692
    clamped to provider hard cap 8192 → 5692"""
    b = derive_output_budget(model="qwen-max", consumed_input_tokens=10_000)
    assert b.remaining_for_output == 32_768 - 10_000
    assert b.max_tokens >= DEFAULT_MIN_OUTPUT_TOKENS
    assert b.max_tokens <= 8_192


@pytest.mark.unit
def test_budget_clamps_to_provider_hard_cap():
    """Plenty of remaining → still bounded by provider hard cap."""
    b = derive_output_budget(model="qwen-max", consumed_input_tokens=0)
    assert b.max_tokens <= PROVIDER_HARD_CAPS["qwen-max"]


@pytest.mark.unit
def test_budget_floor_when_window_almost_full():
    """consumed > window → remaining 0 → floor to min_output."""
    b = derive_output_budget(model="qwen-max", consumed_input_tokens=100_000)
    assert b.max_tokens == DEFAULT_MIN_OUTPUT_TOKENS
    assert b.remaining_for_output == 0


@pytest.mark.unit
def test_budget_unknown_model_uses_safe_default_window():
    """Unknown model → context_window default; budget still computable."""
    b = derive_output_budget(model="fictional-vNext", consumed_input_tokens=1000)
    assert b.max_tokens > 0


@pytest.mark.unit
def test_custom_fraction():
    b = derive_output_budget(model="qwen-plus", consumed_input_tokens=0, fraction=0.5)
    # 50% of 131k = 65k, clamped to provider cap 8192
    assert b.max_tokens == 8_192


@pytest.mark.unit
def test_should_auto_continue_on_length_finish():
    assert should_auto_continue("length") is True
    assert should_auto_continue("LENGTH") is True


@pytest.mark.unit
def test_should_not_auto_continue_on_stop():
    assert should_auto_continue("stop") is False
    assert should_auto_continue("tool_calls") is False
    assert should_auto_continue(None) is False
    assert should_auto_continue("") is False


@pytest.mark.unit
def test_max_tokens_never_below_min():
    """Even silly inputs don't return < min_output."""
    b = derive_output_budget(
        model="qwen-max",
        consumed_input_tokens=999_999,
        min_output=100,
    )
    assert b.max_tokens >= 100


@pytest.mark.unit
def test_provider_hard_caps_documented():
    """Sanity: known production models have explicit caps so we never
    accidentally send max_tokens above what the provider accepts."""
    for required in ("qwen-max", "qwen-plus"):
        assert required in PROVIDER_HARD_CAPS
        assert PROVIDER_HARD_CAPS[required] > 0
