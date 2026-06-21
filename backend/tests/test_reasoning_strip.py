# backend/tests/test_reasoning_strip.py
"""Qwen3 <think> reasoning stripping — buffered + streaming."""

from app.services.ai.runner.reasoning import (
    ReasoningStreamFilter,
    model_uses_reasoning,
    strip_reasoning,
)


# ── strip_reasoning (buffered) ──────────────────────────────────────────────
def test_strip_keeps_answer_after_think_close():
    text = "Here's a thinking process: 1. analyze...\n</think>\n你好！答案是 4。"
    assert strip_reasoning(text) == "你好！答案是 4。"


def test_strip_noop_when_no_think_tag():
    assert strip_reasoning("plain answer, no thinking") == "plain answer, no thinking"


def test_strip_uses_last_close_tag():
    text = "a </think> b </think> final"
    assert strip_reasoning(text) == "final"


def test_strip_handles_empty_and_none():
    assert strip_reasoning("") == ""
    assert strip_reasoning(None) == ""


# ── model_uses_reasoning ────────────────────────────────────────────────────
def test_model_gate_qwen3_only():
    assert model_uses_reasoning("qwen3-6-35b") is True
    assert model_uses_reasoning("QWEN3-30B-A3B") is True
    assert model_uses_reasoning("gpt-4o") is False
    assert model_uses_reasoning("deepseek-chat") is False
    assert model_uses_reasoning(None) is False
    assert model_uses_reasoning("") is False


# ── ReasoningStreamFilter (streaming) ───────────────────────────────────────
def test_stream_filter_suppresses_then_emits_answer():
    f = ReasoningStreamFilter(enabled=True)
    # thinking deltas → suppressed (None)
    assert f.feed("Here's a ") is None
    assert f.feed("thinking process...") is None
    # the chunk that closes the block carries the start of the answer
    assert f.feed(" done.</think>\n你好") == "你好"
    # subsequent deltas pass through verbatim
    assert f.feed("，答案是 4") == "，答案是 4"


def test_stream_filter_close_split_across_deltas():
    f = ReasoningStreamFilter(enabled=True)
    assert f.feed("think</thi") is None
    assert f.feed("nk>answer") == "answer"


def test_stream_filter_strips_leading_newlines_after_close():
    # The answer's leading "\n\n" often arrives in a delta AFTER the close tag.
    f = ReasoningStreamFilter(enabled=True)
    assert f.feed("thinking</think>") is None  # nothing after close in this delta
    assert f.feed("\n\n你好") == "你好"  # leading whitespace trimmed
    assert f.feed(" 世界") == " 世界"  # subsequent text verbatim


def test_stream_filter_disabled_is_passthrough():
    f = ReasoningStreamFilter(enabled=False)
    assert f.feed("hello") == "hello"
    assert f.feed(" world") == " world"
    assert f.flush() == ""


def test_stream_filter_flush_surfaces_truncated_thinking():
    # Stream ended without ever closing the block (truncation) → surface buffer.
    f = ReasoningStreamFilter(enabled=True)
    assert f.feed("thinking but cut off") is None
    assert f.flush() == "thinking but cut off"


def test_stream_filter_no_flush_after_clean_close():
    f = ReasoningStreamFilter(enabled=True)
    f.feed("t</think>ans")
    assert f.flush() == ""
