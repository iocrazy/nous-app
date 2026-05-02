"""context_window — small-model protection.

When a small-context model (4k-8k window) is paired with a heavy AGENT
spec (verbose IDENTITY+SOUL+AGENT.md), the system prompt eats most of
the window and there's no room for the user input. Without this guard
the LLM call returns truncated nonsense or fails outright.

Mirrors OpenClaw agents/context-window-guard.ts thresholds.
"""
from __future__ import annotations

import pytest

from app.agent_framework.context_window import (
    ContextWindowError,
    ContextWindowWarning,
    check_context_budget,
    estimate_tokens,
    model_window_size,
)


# ============================================================================
# token estimation
# ============================================================================

@pytest.mark.unit
def test_estimate_tokens_returns_int():
    assert isinstance(estimate_tokens("hello world"), int)


@pytest.mark.unit
def test_estimate_tokens_scales_with_length():
    short = estimate_tokens("hi")
    long = estimate_tokens("a" * 10000)
    assert long > short * 100


@pytest.mark.unit
def test_estimate_tokens_handles_chinese():
    """Chinese text returns a positive estimate. Our cheap chars/4
    estimator UNDERCOUNTS Chinese (real tokenizers ~1 token per char),
    but the budget check still works because we use the conservative
    side throughout."""
    text = "中文测试" * 100  # 400 chars → 100 tokens by our estimator
    n = estimate_tokens(text)
    assert n >= 100


@pytest.mark.unit
def test_estimate_tokens_empty_returns_zero():
    assert estimate_tokens("") == 0


# ============================================================================
# model window lookup
# ============================================================================

@pytest.mark.unit
@pytest.mark.parametrize(
    "model,expected_min",
    [
        ("gpt-4o", 100_000),
        ("gpt-4o-mini", 100_000),
        ("claude-sonnet-4-5", 100_000),
        ("claude-opus-4-7", 100_000),
        ("qwen-max", 30_000),
        ("doubao-pro-32k", 30_000),
        ("deepseek-chat", 30_000),
    ],
)
def test_known_model_windows(model: str, expected_min: int):
    """Known models have a registered window size at least the
    advertised value."""
    assert model_window_size(model) >= expected_min


@pytest.mark.unit
def test_unknown_model_uses_settings_default():
    """Unknown model name falls back to settings.LLM_MAX_CONTEXT_TOKENS."""
    from app.core.config import settings

    n = model_window_size("totally-made-up-model-name-xyz")
    assert n == settings.LLM_MAX_CONTEXT_TOKENS


# ============================================================================
# check_context_budget
# ============================================================================

@pytest.mark.unit
def test_check_passes_when_system_prompt_is_small():
    """Tiny system prompt + huge model window → no warning, no error."""
    check_context_budget(
        system_prompt="You are helpful.",
        model="gpt-4o",
    )  # no raise


@pytest.mark.unit
def test_check_warns_when_system_takes_over_warn_ratio():
    """When system_tokens > 0.5 * window, warn (caller decides to log).
    Exposed as ContextWindowWarning so caller can handle vs ignore."""
    huge_system = "X" * 100_000  # ≈ 100k tokens
    with pytest.warns(ContextWindowWarning):
        check_context_budget(
            system_prompt=huge_system,
            model="qwen-max",  # 32k window
        )


@pytest.mark.unit
def test_check_rejects_when_system_takes_over_error_ratio():
    """When system_tokens > 0.8 * window, RAISE — no room for user input."""
    huge_system = "X" * 200_000  # way too big for any small model
    with pytest.raises(ContextWindowError):
        check_context_budget(
            system_prompt=huge_system,
            model="qwen-max",
        )


@pytest.mark.unit
def test_check_includes_user_messages_in_budget():
    """User input also counts toward the budget. Cumulative check."""
    medium_system = "You are an agent. " + ("X" * 16_000)  # ~5k tokens
    medium_user = "Y" * 24_000  # ~8k tokens
    # qwen-max window is 32k. 5+8=13k < 32k * 0.5 = 16k. Should pass.
    check_context_budget(
        system_prompt=medium_system,
        user_messages=[{"role": "user", "content": medium_user}],
        model="qwen-max",
    )

    big_user = "Y" * 100_000  # 33k tokens — pushes total over window
    with pytest.raises(ContextWindowError):
        check_context_budget(
            system_prompt=medium_system,
            user_messages=[{"role": "user", "content": big_user}],
            model="qwen-max",
        )


@pytest.mark.unit
def test_check_handles_multimodal_user_messages():
    """user_messages with list content (image blocks) — count text only.
    Image bytes are tokenized separately by the model and not counted here."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:..."}},
                {"type": "text", "text": "What's in this image?"},
            ],
        }
    ]
    check_context_budget(
        system_prompt="You see images.",
        user_messages=msgs,
        model="gpt-4o",
    )  # no raise


@pytest.mark.unit
def test_check_handles_no_user_messages():
    check_context_budget(
        system_prompt="hello",
        user_messages=None,
        model="gpt-4o",
    )
