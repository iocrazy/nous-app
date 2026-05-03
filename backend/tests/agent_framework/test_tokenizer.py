"""A1 — model-aware tokenizer with heuristic fallback."""
from __future__ import annotations

import pytest

from app.agent_framework.tokenizer import (
    count_messages_tokens,
    count_tokens,
)


# ─── Empty / edge cases ───────────────────────────────────────────────


@pytest.mark.unit
def test_empty_string_returns_zero():
    assert count_tokens("") == 0
    assert count_tokens("", "qwen-max") == 0


@pytest.mark.unit
def test_non_string_returns_zero_defensively():
    assert count_tokens(None) == 0  # type: ignore[arg-type]
    assert count_tokens(123) == 0  # type: ignore[arg-type]


# ─── Heuristic — language-aware ───────────────────────────────────────


@pytest.mark.unit
def test_ascii_uses_chars_div_4():
    """ASCII English: heuristic returns chars // 4."""
    text = "a" * 400
    n = count_tokens(text)
    # No tokenizer installed → heuristic
    assert n == 100


@pytest.mark.unit
def test_chinese_counts_one_token_per_char():
    """CJK characters are 1 token each (better than chars/4 which would
    massively under-count)."""
    text = "中文测试"  # 4 CJK chars
    n = count_tokens(text)
    assert n == 4


@pytest.mark.unit
def test_japanese_hiragana_katakana_counted():
    text = "あいうえおカタカナ"  # 5 hiragana + 4 katakana = 9
    n = count_tokens(text)
    assert n == 9


@pytest.mark.unit
def test_mixed_chinese_english():
    """200 ASCII (50 tokens) + 100 CJK (100 tokens) = 150 tokens."""
    text = "a" * 200 + "中" * 100
    n = count_tokens(text)
    assert n == 150


@pytest.mark.unit
def test_korean_hangul_counted():
    text = "안녕하세요"  # 5 hangul syllables
    n = count_tokens(text)
    assert n == 5


# ─── Model-routing fallback (no tokenizer installed) ──────────────────


@pytest.mark.unit
def test_unknown_model_falls_back_to_heuristic():
    """Model name we can't classify → heuristic, never crash."""
    text = "hello world"
    n = count_tokens(text, "fictional-model-vNext")
    assert n > 0
    # Should equal heuristic count
    assert n == count_tokens(text)


@pytest.mark.unit
def test_qwen_model_uses_heuristic_when_dashscope_missing():
    """dashscope not installed in test env → heuristic. Does not raise."""
    text = "中文 mixed with English"
    n = count_tokens(text, "qwen-max")
    assert n > 0


@pytest.mark.unit
def test_openai_model_uses_heuristic_when_tiktoken_missing():
    text = "hello world"
    n = count_tokens(text, "gpt-4o")
    assert n > 0


# ─── count_messages_tokens ────────────────────────────────────────────


@pytest.mark.unit
def test_messages_empty():
    assert count_messages_tokens([]) == 0


@pytest.mark.unit
def test_messages_includes_per_message_overhead():
    """4-token overhead per message — matches OpenAI's framing tokens."""
    msgs = [
        {"role": "user", "content": ""},
        {"role": "user", "content": ""},
    ]
    assert count_messages_tokens(msgs) == 8  # 2 * 4 overhead, empty content


@pytest.mark.unit
def test_messages_string_content():
    msgs = [{"role": "user", "content": "a" * 400}]
    # 4 overhead + 100 content tokens
    assert count_messages_tokens(msgs) == 104


@pytest.mark.unit
def test_messages_multipart_content():
    """Anthropic-style multipart list of dicts."""
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "a" * 400},
                {"type": "text", "text": "b" * 400},
            ],
        }
    ]
    # 4 overhead + 100 + 100
    assert count_messages_tokens(msgs) == 204


@pytest.mark.unit
def test_messages_tool_calls():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {
                        "name": "search",
                        "arguments": '{"q": "' + "a" * 400 + '"}',
                    },
                }
            ],
        }
    ]
    n = count_messages_tokens(msgs)
    # 4 overhead + 0 content + name(1-2) + args(>= 100)
    assert n > 100


@pytest.mark.unit
def test_messages_chinese_uses_better_estimate():
    """Critical: chars/4 would under-count Chinese 4x. Verify the new
    estimate is closer to truth (1 token per CJK char)."""
    msgs = [{"role": "user", "content": "中" * 1000}]
    # 4 overhead + 1000 CJK tokens
    n = count_messages_tokens(msgs)
    assert n == 1004
    # Old chars/4 would have returned 250 — confirm we beat it 4x
    assert n > 1000
