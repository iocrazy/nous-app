"""A2 — per-message size cap."""
from __future__ import annotations

import pytest

from app.agent_framework.message_truncation import (
    DEFAULT_PER_MESSAGE_TOKEN_CAP,
    TRUNCATION_MARKER,
    cap_message_tokens,
    cap_messages_tokens,
)


# ─── No-op for under-cap messages ─────────────────────────────────────


@pytest.mark.unit
def test_short_message_passes_through_unchanged():
    msg = {"role": "user", "content": "hello"}
    out = cap_message_tokens(msg, cap=1000)
    assert out.truncated is False
    assert out.message is msg  # same object reference, no copy


@pytest.mark.unit
def test_empty_content_passes_through():
    msg = {"role": "user", "content": ""}
    out = cap_message_tokens(msg, cap=100)
    assert out.truncated is False


# ─── Truncation triggers ──────────────────────────────────────────────


@pytest.mark.unit
def test_oversized_string_content_truncated():
    """1000-token string with cap=200 → truncated, marker present."""
    msg = {"role": "user", "content": "a" * 4000}  # ~1000 tokens (chars/4)
    out = cap_message_tokens(msg, cap=200)
    assert out.truncated is True
    assert out.tokens_after < out.tokens_before
    assert "truncated for length" in out.message["content"]


@pytest.mark.unit
def test_truncation_marker_indicates_dropped_count():
    msg = {"role": "user", "content": "a" * 4000}
    out = cap_message_tokens(msg, cap=200)
    # Marker mentions some "tokens removed"
    assert "tokens removed" in out.message["content"]


@pytest.mark.unit
def test_original_message_not_mutated():
    """Caller's message dict must not be modified in place."""
    original_content = "a" * 4000
    msg = {"role": "user", "content": original_content}
    cap_message_tokens(msg, cap=200)
    assert msg["content"] == original_content


# ─── Multi-part content ───────────────────────────────────────────────


@pytest.mark.unit
def test_multipart_content_truncates_largest_part():
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "short"},
            {"type": "text", "text": "a" * 4000},  # the offender
        ],
    }
    out = cap_message_tokens(msg, cap=300)
    assert out.truncated is True
    parts = out.message["content"]
    # First part untouched
    assert parts[0]["text"] == "short"
    # Second part shrunk + marked
    assert "truncated" in parts[1]["text"]


# ─── Tool call args ───────────────────────────────────────────────────


@pytest.mark.unit
def test_oversized_tool_call_args_replaced_with_placeholder():
    """Tool call JSON args can't be safely truncated — they get replaced
    with a placeholder pointing at the original tool_call_id."""
    big_args = '{"x": "' + "a" * 4000 + '"}'
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "tc-42",
                "function": {"name": "foo", "arguments": big_args},
            }
        ],
    }
    out = cap_message_tokens(msg, cap=200)
    assert out.truncated is True
    new_args = out.message["tool_calls"][0]["function"]["arguments"]
    assert "tool_call replaced" in new_args
    assert "tc-42" in new_args  # tcid preserved for debugging


@pytest.mark.unit
def test_small_tool_call_args_preserved():
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "tc-1",
                "function": {"name": "foo", "arguments": '{"x": 1}'},
            }
        ],
    }
    out = cap_message_tokens(msg, cap=200)
    assert out.truncated is False
    assert out.message["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'


# ─── Batch ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cap_messages_returns_parallel_outcomes():
    msgs = [
        {"role": "user", "content": "short"},
        {"role": "user", "content": "a" * 4000},
        {"role": "user", "content": "another short"},
    ]
    outcomes = cap_messages_tokens(msgs, cap=200)
    assert len(outcomes) == 3
    assert outcomes[0].truncated is False
    assert outcomes[1].truncated is True
    assert outcomes[2].truncated is False


# ─── Default cap sanity ───────────────────────────────────────────────


@pytest.mark.unit
def test_default_cap_well_above_normal_use():
    """50k tokens ≈ 200KB — anything reasonable passes; only adversarial
    pastes / runaway tool results get truncated."""
    assert DEFAULT_PER_MESSAGE_TOKEN_CAP >= 10_000


@pytest.mark.unit
def test_truncation_marker_format_stable():
    """The marker text is part of the contract — agent prompts may match
    on it. Lock in the prefix."""
    assert "truncated" in TRUNCATION_MARKER
    assert "{dropped}" in TRUNCATION_MARKER
