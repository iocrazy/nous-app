"""Compaction leaves a bracket in the transcript: start → summary → end.

The bracket exists so a crash mid-summary is *visible* — an orphan
``compaction_start`` with no ``compaction_end`` for the same run is the
crash scene. Two things follow that the tests pin separately:

* ``start`` lands **before** the summarizer is awaited (an event written after
  the fact cannot witness a crash that happened during it);
* ``end`` lands **even when the whole thing blows up**, carrying the error —
  and never claims a success it did not have.

Events are telemetry: no recorder → silence, a recorder that throws → warning,
never a failed turn. Green/yellow tiers never summarize, so they never bracket.
"""

import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from app.agent_framework.context_compactor import ContextCompactor
from app.agent_framework.tool_result_pruner import PruneStats
from tests.agent_framework.compaction_stubs import token_stub


def _stats(**kw):
    defaults = dict(duplicates_replaced=0, aged_results=0, chars_dropped=0)
    defaults.update(kw)
    return PruneStats(**defaults)


def _msgs(n=12):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"}
        for i in range(n)
    ]


class _Recorder:
    """Only ``record_event`` — the compactor must not need anything else."""

    def __init__(self, fail=False):
        self.events: list[tuple[str, dict]] = []
        self.fail = fail

    async def record_event(self, event_type, payload):
        if self.fail:
            raise RuntimeError("recorder down")
        self.events.append((event_type, dict(payload)))

    def types(self):
        return [t for t, _ in self.events]

    def payload(self, event_type):
        return next(p for t, p in self.events if t == event_type)


def _orange(msgs, *, summary_tokens, summarize=None, warm=None):
    """Enter the ORANGE tier with the pruner unable to rescue."""
    return (
        patch(
            "app.agent_framework.context_compactor.resolve_model_window",
            return_value=(1000, True),
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            new=token_stub([850, 840], summary_tokens=summary_tokens),
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _stats()),
        ),
        patch(
            "app.agent_framework.summarizer.summarize",
            new=summarize or AsyncMock(return_value="short summary"),
        ),
        patch(
            "app.agent_framework.summarizer.summarize_warm_prefix",
            new=warm or AsyncMock(side_effect=RuntimeError("no warm")),
        ),
    )


async def _run(compactor, msgs, recorder, **kw):
    return await compactor.maybe_compact(
        system_message="sys",
        user_messages=msgs,
        model="claude-sonnet-4-6",
        adapter=object(),
        recorder=recorder,
        **kw,
    )


@pytest.mark.unit
async def test_success_path_lands_all_three_events_in_order():
    rec = _Recorder()
    msgs = _msgs()
    with contextlib.ExitStack() as st:
        for p in _orange(msgs, summary_tokens=50):
            st.enter_context(p)
        out, stats = await _run(ContextCompactor(), msgs, rec)

    assert rec.types() == ["compaction_start", "compaction_summary", "compaction_end"]
    start = rec.payload("compaction_start")
    assert start["tier"] == "orange" and start["window"] == 1000
    assert start["tokens_before"] == 850
    summary = rec.payload("compaction_summary")
    assert summary["path"] == "legacy"
    assert summary["attempts"] == 1
    assert summary["summary_tokens"] == 50 and summary["head_tokens"] > 50
    # phase 2b-1: replay rebuilds post-compaction history from this text
    assert summary["summary"] == "short summary"
    end = rec.payload("compaction_end")
    assert end["tokens_saved"] == stats.tokens_saved > 0
    assert end["tokens_after"] == stats.tokens_after
    assert "error" not in end


@pytest.mark.unit
async def test_warm_prefix_success_is_reported_as_the_warm_path():
    rec = _Recorder()
    msgs = _msgs()
    with contextlib.ExitStack() as st:
        for p in _orange(
            msgs, summary_tokens=50, warm=AsyncMock(return_value="warm summary")
        ):
            st.enter_context(p)
        await _run(ContextCompactor(), msgs, rec)

    assert rec.payload("compaction_summary")["path"] == "warm"


@pytest.mark.unit
async def test_start_lands_before_the_summarizer_is_awaited():
    """A start written after the summary cannot witness a crash inside it."""
    order: list[str] = []

    class _OrderRecorder(_Recorder):
        async def record_event(self, event_type, payload):
            order.append(event_type)
            await super().record_event(event_type, payload)

    async def _summarize(_head):
        order.append("summarize")
        return "short summary"

    msgs = _msgs()
    with contextlib.ExitStack() as st:
        for p in _orange(msgs, summary_tokens=50, summarize=_summarize):
            st.enter_context(p)
        await _run(ContextCompactor(), msgs, _OrderRecorder())

    assert order[:2] == ["compaction_start", "summarize"], order


@pytest.mark.unit
async def test_rejected_summaries_end_in_the_emergency_cap_path():
    rec = _Recorder()
    msgs = _msgs()
    with contextlib.ExitStack() as st:
        # summary never shrinks → every draft rejected → emergency cap
        for p in _orange(msgs, summary_tokens=999):
            st.enter_context(p)
        out, stats = await _run(ContextCompactor(), msgs, rec)

    assert rec.types() == ["compaction_start", "compaction_summary", "compaction_end"]
    summary = rec.payload("compaction_summary")
    assert summary["path"] == "emergency_cap"
    assert summary["attempts"] == ContextCompactor.SUMMARY_ATTEMPTS
    assert "error" in summary
    end = rec.payload("compaction_end")
    assert end["tokens_after"] == stats.tokens_after
    assert "error" not in end, "the cap succeeded; end must not claim failure"


@pytest.mark.unit
async def test_end_still_lands_with_the_error_when_everything_blows_up():
    """try/finally: an orphan start is the crash scene, but a *present* end
    that hides the crash is worse."""
    rec = _Recorder()
    msgs = _msgs()
    with (
        contextlib.ExitStack() as st,
        patch.object(
            ContextCompactor,
            "_emergency_cap",
            side_effect=RuntimeError("cap exploded"),
        ),
    ):
        for p in _orange(msgs, summary_tokens=999):
            st.enter_context(p)
        with pytest.raises(RuntimeError, match="cap exploded"):
            await _run(ContextCompactor(), msgs, rec)

    assert rec.types()[0] == "compaction_start"
    assert rec.types()[-1] == "compaction_end"
    end = rec.payload("compaction_end")
    assert "cap exploded" in end["error"]
    assert "tokens_saved" not in end, "no shrink happened; do not invent one"


@pytest.mark.unit
async def test_no_recorder_is_silent_and_harmless():
    msgs = _msgs()
    with contextlib.ExitStack() as st:
        for p in _orange(msgs, summary_tokens=50):
            st.enter_context(p)
        out, stats = await ContextCompactor().maybe_compact(
            system_message="sys",
            user_messages=msgs,
            model="claude-sonnet-4-6",
            adapter=object(),
        )
    assert stats.tokens_saved > 0


@pytest.mark.unit
async def test_a_recorder_that_throws_never_breaks_compaction():
    msgs = _msgs()
    with contextlib.ExitStack() as st:
        for p in _orange(msgs, summary_tokens=50):
            st.enter_context(p)
        out, stats = await _run(ContextCompactor(), msgs, _Recorder(fail=True))
    assert stats.tokens_saved > 0


@pytest.mark.unit
async def test_green_and_yellow_tiers_never_bracket():
    rec = _Recorder()
    msgs = _msgs()
    with (
        patch(
            "app.agent_framework.context_compactor.resolve_model_window",
            return_value=(1000, True),
        ),
        patch("app.agent_framework.context_compactor.count_tokens", return_value=0),
        patch(
            "app.agent_framework.context_compactor.count_messages_tokens",
            new=token_stub([700, 650], summary_tokens=50),  # yellow: 70%
        ),
        patch(
            "app.agent_framework.context_compactor.prune",
            return_value=(msgs, _stats()),
        ),
    ):
        await _run(ContextCompactor(), msgs, rec)
    assert rec.events == []
