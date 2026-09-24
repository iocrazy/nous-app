"""The budget guard and the compactor must measure with the same ruler.

Before 2026-09-23 ``check_context_budget`` counted ``chars / 4`` over the
text parts only (no ``tool_calls``, no per-message framing) while the
compactor counted with ``tokenizer.count_messages_tokens`` (CJK = 1 token per
char). On Chinese history the guard was ~4x too lenient, and on ASCII a
successful emergency cap (target 0.80) left the turn only ~1% under the old
0.80 rejection line — inside the two rulers' disagreement, so whether a
just-truncated turn was rejected anyway came down to the tokenizer.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework import context_compactor as cc
from app.agent_framework import context_window as cw
from app.agent_framework.context_compactor import (
    CompactionThresholds,
    CompactionTier,
    ContextCompactor,
)
from app.agent_framework.context_window import (
    ContextWindowError,
    check_context_budget,
    model_window_size,
)
from app.agent_framework.tokenizer import count_messages_tokens, count_tokens

pytestmark = pytest.mark.unit

# 4096-token window, heuristic tokenizer (no provider package for "phi").
_SMALL_MODEL = "phi-3-mini"


def test_cjk_history_is_counted_like_the_compactor_counts_it():
    """4k CJK chars: chars/4 said ~1k tokens (passes a 4k window); the
    compactor's tokenizer says ~4k (over the rejection line)."""
    msgs = [{"role": "user", "content": "中" * 4000}]
    assert 4000 // 4 < int(model_window_size(_SMALL_MODEL) * 0.8)  # old: passed
    with pytest.raises(ContextWindowError):
        check_context_budget(
            system_prompt="sys", user_messages=msgs, model=_SMALL_MODEL
        )


def test_tool_call_arguments_count_toward_the_budget():
    """An assistant turn whose weight is all in ``tool_calls`` args was
    invisible to the old text-only extractor."""
    msgs = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "Write", "arguments": "a" * 16_000},
                }
            ],
        }
    ]
    with pytest.raises(ContextWindowError):
        check_context_budget(
            system_prompt="sys", user_messages=msgs, model=_SMALL_MODEL
        )


def test_measured_tokens_is_the_count_when_given():
    """The runner hands over the compactor's ``tokens_after``; the guard must
    use it instead of scanning the history a second time."""
    window = model_window_size(_SMALL_MODEL)
    with patch.object(
        cw, "count_messages_tokens", side_effect=AssertionError("rescanned")
    ):
        check_context_budget(
            system_prompt="sys",
            user_messages=[{"role": "user", "content": "中" * 10_000}],
            model=_SMALL_MODEL,
            measured_tokens=int(window * 0.3),
        )
        with pytest.raises(ContextWindowError):
            check_context_budget(
                system_prompt="sys",
                user_messages=[{"role": "user", "content": "hi"}],
                model=_SMALL_MODEL,
                measured_tokens=window,
            )


def test_unmeasured_count_equals_the_compactor_formula():
    """Without ``measured_tokens`` the guard counts system + messages with the
    compactor's own functions — right at the line it rejects, one under it
    passes."""
    window = model_window_size(_SMALL_MODEL)
    line = int(window * cw.REJECT_RATIO)
    sys_prompt = "system prompt"
    base = count_tokens(sys_prompt, _SMALL_MODEL) + count_messages_tokens(
        [{"role": "user", "content": ""}], _SMALL_MODEL
    )
    under = [{"role": "user", "content": "中" * (line - base - 1)}]
    at = [{"role": "user", "content": "中" * (line - base)}]
    check_context_budget(
        system_prompt=sys_prompt, user_messages=under, model=_SMALL_MODEL
    )
    with pytest.raises(ContextWindowError):
        check_context_budget(
            system_prompt=sys_prompt, user_messages=at, model=_SMALL_MODEL
        )


async def test_emergency_cap_result_is_not_rejected():
    """Red-tier history, summarizer down → emergency cap. The capped history
    must clear the guard on the same turn (it used to land on 0.80 = reject)."""
    window = model_window_size(_SMALL_MODEL)
    head = [{"role": "assistant", "content": "x" * 1600} for _ in range(10)]
    tail = [{"role": "user", "content": "recent"}, {"role": "user", "content": "now"}]
    msgs = head + tail
    assert count_messages_tokens(msgs, _SMALL_MODEL) >= window * 0.9

    with patch(
        "app.agent_framework.summarizer.summarize",
        new=AsyncMock(side_effect=RuntimeError("summarizer 503")),
    ):
        out, stats = await ContextCompactor().maybe_compact(
            system_message="sys", user_messages=msgs, model=_SMALL_MODEL
        )

    assert stats.tier == CompactionTier.RED
    assert any("emergency-cap fallback" in n for n in stats.notes)
    # Headroom, not a coin toss: the old 0.80 target landed ~0.79 against a
    # 0.80 line. At least a tenth of the window must separate the two.
    assert window * cw.REJECT_RATIO - stats.tokens_after >= window * 0.1
    check_context_budget(
        system_prompt="sys",
        user_messages=out,
        model=_SMALL_MODEL,
        measured_tokens=stats.tokens_after,
    )
    check_context_budget(system_prompt="sys", user_messages=out, model=_SMALL_MODEL)


def test_thresholds_are_ordered_and_red_is_the_rejection_line():
    """emergency target < orange < red == reject, and red IS the guard's
    constant (imported, not re-typed), so the two cannot drift."""
    t = CompactionThresholds()
    assert ContextCompactor.EMERGENCY_TARGET_PCT == 0.70
    assert t.orange_pct == 0.80
    assert cw.REJECT_RATIO == 0.90
    assert ContextCompactor.EMERGENCY_TARGET_PCT < t.orange_pct < t.red_pct
    assert t.red_pct == cw.REJECT_RATIO
    assert cc.REJECT_RATIO is cw.REJECT_RATIO
    assert not hasattr(ContextCompactor, "EMERGENCY_FLOOR_PCT")
    assert not hasattr(cw, "ERROR_RATIO")


def test_rejection_line_is_defined_once():
    """No second literal for the red/reject line in the compactor source."""
    src = Path(cc.__file__).read_text(encoding="utf-8")
    assert not re.search(
        r"(?<![\d.])0\.9[05]?(?!\d)", src
    ), "red/reject line re-typed in the compactor"
