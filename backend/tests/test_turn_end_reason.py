"""Every turn ends with exactly one typed reason in the transcript.

Before this, a run that hit the tool-loop ceiling, one the provider cut off,
and one that simply finished all read "completed". The runner's exits already
carried distinguishing markers; nothing collected them into one word.

The exhaustiveness guard at the bottom is the load-bearing test: it scans
the runner's source for every marker literal its exits emit and asserts the
classifier knows each one. A new exit shape that the classifier would
misfile as "completed" turns red there — not in production.
"""

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.services.ai.runner import turn_end as te
from app.services.ai.runner.turn_end import (
    TurnEndReason,
    classify_exception,
    classify_run_result,
    classify_stream_end,
)

pytestmark = pytest.mark.unit

RUNNER_SRC = Path("app/services/ai/runner/agent_runner.py")
EXIT_FUNCTIONS = (
    "_run_turn_inner",
    "_stream_turn_inner",
    "_preflight_compact_and_budget",
    "_aborted_response",
    "_awaiting_approval_response",
    "_stopped_response",
)


# ── run_turn result classification ───────────────────────────────────────


@pytest.mark.parametrize(
    "result, reason",
    [
        (
            {"content": "hi", "raw": {"choices": [{"finish_reason": "stop"}]}},
            "completed",
        ),
        (
            {"content": "hi", "raw": {"choices": [{"finish_reason": "length"}]}},
            "provider_length",
        ),
        (
            {
                "content": "",
                "raw": None,
                "error": "ctx",
                "error_code": "context_budget_exceeded",
            },
            "context_rejected",
        ),
        (
            {"content": "", "raw": None, "error": "max_tool_iterations_exceeded"},
            "max_iterations",
        ),
        ({"content": "", "raw": None, "cancelled": True}, "cancelled"),
        (
            {"content": "", "raw": None, "aborted": True, "abort_reason": "hook"},
            "cancelled",
        ),
        ({"content": "", "raw": None, "awaiting_approval": True}, "awaiting_approval"),
        (
            {"content": "", "raw": None, "error": "boom", "error_code": "run_timeout"},
            "error",
        ),
        ({"content": "", "raw": None, "error": "free text failure"}, "error"),
    ],
)
def test_run_result_classification(result, reason):
    got, _ = classify_run_result(result)
    assert got is TurnEndReason(reason)


def test_run_result_carries_finish_reason_and_tool_call_count():
    _, extra = classify_run_result(
        {
            "content": "x",
            "raw": {"choices": [{"finish_reason": "stop"}]},
            "tool_calls": [1, 2],
        }
    )
    assert extra == {"finish_reason": "stop", "tool_calls": 2}


# ── stream terminal-chunk classification ─────────────────────────────────


@pytest.mark.parametrize(
    "chunk, reason",
    [
        (StreamChunk(finish_reason="stop"), "completed"),
        (StreamChunk(finish_reason="length"), "provider_length"),
        (
            StreamChunk(
                finish_reason="length", usage={"error_code": "context_budget_exceeded"}
            ),
            "context_rejected",
        ),
        (
            StreamChunk(
                finish_reason="length",
                usage={"warning": "max_stream_iterations_exceeded"},
            ),
            "max_iterations",
        ),
        (
            StreamChunk(
                finish_reason="length", usage={"warning": "timeout_sec_exceeded"}
            ),
            "error",
        ),
        (
            StreamChunk(finish_reason="stop", usage={"hook_decision": "abort"}),
            "cancelled",
        ),
        (
            StreamChunk(
                finish_reason="stop", usage={"hook_decision": "await_approval"}
            ),
            "awaiting_approval",
        ),
        (None, "cancelled"),  # generator returned without a terminal chunk
    ],
)
def test_stream_end_classification(chunk, reason):
    got, _ = classify_stream_end(chunk)
    assert got is TurnEndReason(reason)


def test_stream_length_from_the_loop_ceiling_is_not_mistaken_for_the_provider():
    """Both exits say finish_reason=length; only the usage marker tells them
    apart. Misfiling the ceiling as a provider cut-off hides the real cause."""
    ceiling = StreamChunk(
        finish_reason="length", usage={"warning": "max_stream_iterations_exceeded"}
    )
    provider = StreamChunk(finish_reason="length", usage={"prompt_tokens": 1})
    assert classify_stream_end(ceiling)[0] is TurnEndReason.MAX_ITERATIONS
    assert classify_stream_end(provider)[0] is TurnEndReason.PROVIDER_LENGTH


def test_exceptions_split_cancel_from_error():
    assert classify_exception(asyncio.CancelledError())[0] is TurnEndReason.CANCELLED
    reason, extra = classify_exception(RuntimeError("kaboom"))
    assert reason is TurnEndReason.ERROR and "kaboom" in extra["error"]


# ── the wrappers actually emit ───────────────────────────────────────────


class _Rec:
    def __init__(self):
        self.events = []

    async def record_event(self, event_type, payload):
        self.events.append((event_type, payload))

    def record_usage(self, **k):
        pass

    async def heartbeat(self):
        pass

    async def check_cancelled(self):
        return False

    def record_skill(self, s):
        pass

    def turn_ends(self):
        return [p for t, p in self.events if t == "turn_end"]


def _composed():
    from uuid import UUID

    from app.schemas.ai_library import ComposedSystemPrompt

    return ComposedSystemPrompt(
        agent_id=UUID(int=1),
        agent_slug="t",
        model="m",
        temperature=0.0,
        max_tokens=16,
        system_message="sys",
        tools=[],
        skill_manifest=[],
        cache_fingerprint="f",
    )


def _runner(adapter):
    from app.services.ai.runner.agent_runner import AgentRunner

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(adapter=adapter, skill_tool=_Tool())


async def test_run_turn_emits_exactly_one_turn_end_on_success():
    adapter = AsyncMock()

    async def _call(composed, messages, **kw):
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    adapter.call = _call
    rec = _Rec()
    await _runner(adapter).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=rec
    )
    ends = rec.turn_ends()
    assert len(ends) == 1, rec.events
    assert ends[0]["reason"] == "completed"
    assert ends[0]["finish_reason"] == "stop"


async def test_run_turn_emits_error_and_reraises_when_the_turn_blows_up():
    adapter = AsyncMock()

    async def _call(composed, messages, **kw):
        raise RuntimeError("provider on fire")

    adapter.call = _call
    rec = _Rec()
    with pytest.raises(RuntimeError, match="provider on fire"):
        await _runner(adapter).run_turn(
            _composed(), [{"role": "user", "content": "q"}], recorder=rec
        )
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == "error"
    assert "provider on fire" in ends[0]["error"]


async def test_run_turn_without_a_recorder_is_silent():
    adapter = AsyncMock()

    async def _call(composed, messages, **kw):
        return {"choices": [{"message": {"content": "ok"}}]}

    adapter.call = _call
    out = await _runner(adapter).run_turn(
        _composed(), [{"role": "user", "content": "q"}]
    )
    assert out["content"] == "ok"


async def test_stream_turn_emits_turn_end_after_the_last_chunk():
    adapter = AsyncMock()

    async def _stream(composed, messages, **kw):
        yield StreamChunk(delta_text="he")
        yield StreamChunk(delta_text="llo")
        yield StreamChunk(finish_reason="stop", usage={"prompt_tokens": 1})

    adapter.stream = _stream
    rec = _Rec()
    order = []
    async for ch in _runner(adapter).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        order.append(("chunk", ch.finish_reason))
        order.append(("ends_so_far", len(rec.turn_ends())))
    assert order[-1] == (
        "ends_so_far",
        0,
    ), "turn_end must land after the terminal chunk, not before"
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == "completed"


async def test_stream_turn_marks_the_provider_cut_off():
    adapter = AsyncMock()

    async def _stream(composed, messages, **kw):
        yield StreamChunk(delta_text="x")
        yield StreamChunk(finish_reason="length")

    adapter.stream = _stream
    rec = _Rec()
    async for _ in _runner(adapter).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    assert rec.turn_ends()[0]["reason"] == "provider_length"


async def test_stream_turn_emits_error_when_the_stream_blows_up():
    adapter = AsyncMock()

    async def _stream(composed, messages, **kw):
        yield StreamChunk(delta_text="x")
        raise RuntimeError("socket died")

    adapter.stream = _stream
    rec = _Rec()
    with pytest.raises(RuntimeError, match="socket died"):
        async for _ in _runner(adapter).stream_turn(
            _composed(),
            [{"role": "user", "content": "q"}],
            recorder=rec,
            auto_recorder=False,
        ):
            pass
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == "error"


async def test_buffered_fallback_files_exactly_one_turn_end():
    """stream_turn without adapter.stream delegates to run_turn; two wrappers
    each filing an end would double-count every buffered turn."""
    adapter = AsyncMock(spec=["call"])

    async def _call(composed, messages, **kw):
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

    adapter.call = _call
    rec = _Rec()
    async for _ in _runner(adapter).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    ends = rec.turn_ends()
    assert len(ends) == 1, ends
    assert ends[0]["reason"] == "completed"


async def test_buffered_fallback_keeps_the_loop_ceiling_reason(monkeypatch):
    adapter = AsyncMock(spec=["call"])
    adapter.call = AsyncMock()
    runner = _runner(adapter)

    async def _inner(*a, **k):
        return {"content": "", "raw": None, "error": "max_tool_iterations_exceeded"}

    monkeypatch.setattr(runner, "_run_turn_inner", _inner)
    rec = _Rec()
    async for _ in runner.stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        pass
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == "max_iterations", ends


async def test_a_recorder_that_throws_on_turn_end_does_not_break_the_turn():
    adapter = AsyncMock()

    async def _call(composed, messages, **kw):
        return {"choices": [{"message": {"content": "ok"}}]}

    adapter.call = _call

    class _Bad(_Rec):
        async def record_event(self, event_type, payload):
            if event_type == "turn_end":
                raise RuntimeError("db down")

    out = await _runner(adapter).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=_Bad()
    )
    assert out["content"] == "ok"


# ── mirror into agent_runs.metadata_json ─────────────────────────────────


async def test_turn_end_is_mirrored_into_run_metadata(monkeypatch):
    """The event is the truth; metadata_json.turn_end_reason is the cache the
    Task Center reads off the agent_runs row it already has."""
    import contextlib

    from app.db import session as dbs
    from app.services.ai.runner import run_recorder as rr

    executed = []

    class _S:
        async def execute(self, stmt, *a, **k):
            c = stmt.compile()
            executed.append(str(c) + " " + repr(getattr(c, "params", {})))

    @contextlib.asynccontextmanager
    async def _ws():
        yield _S()

    monkeypatch.setattr(dbs, "write_scope", _ws)
    rec = rr.RunRecorder.__new__(rr.RunRecorder)
    rec.run_id = 7
    rec._event_seq = 0
    await rec.record_event("turn_end", {"reason": "max_iterations", "tool_calls": 3})
    mirrors = [e for e in executed if "jsonb_set" in e]
    assert len(mirrors) == 1, executed
    assert "agent_runs" in mirrors[0] and "turn_end_reason" in mirrors[0]


# ── exhaustiveness guard ─────────────────────────────────────────────────


def test_every_exit_marker_in_the_runner_source_is_known_to_the_classifier():
    """Scan the runner's exits for their marker literals. A new one that the
    classifier does not know would silently be filed as 'completed'."""
    assert RUNNER_SRC.exists(), "guard would pass vacuously on a moved file"
    whole = RUNNER_SRC.read_text(encoding="utf-8")
    # Only the turn's exit functions: tool-result dicts elsewhere in the file
    # carry their own error_code vocabulary that never ends a turn.
    src = ""
    for fn in EXIT_FUNCTIONS:
        m = re.search(
            rf"\n    (?:async )?def {fn}\(.*?(?=\n    (?:async )?def |\Z)", whole, re.S
        )
        assert m, f"exit function {fn} not found — guard would scan nothing"
        src += m.group(0)

    flags = set(
        re.findall(
            r'"(cancelled|aborted|awaiting_approval|awaiting_input)":\s*True', src
        )
    )
    error_codes = set(re.findall(r'"error_code":\s*"([a-z_]+)"', src))
    hook_decisions = set(re.findall(r'"hook_decision":\s*"([a-z_]+)"', src))
    warnings = set(re.findall(r'"warning":\s*"([a-z_]+)"', src))
    error_literals = set(re.findall(r'"error":\s*"([a-z_]+)"', src))

    # anti-vacuity: the scan must actually find the exits we know exist
    assert {"cancelled", "aborted", "awaiting_approval"} <= flags, flags
    assert "context_budget_exceeded" in error_codes, error_codes
    assert {"abort", "await_approval"} <= hook_decisions, hook_decisions
    assert "max_stream_iterations_exceeded" in warnings, warnings
    assert "max_tool_iterations_exceeded" in error_literals, error_literals

    assert flags <= set(te.FLAG_MARKERS), flags - set(te.FLAG_MARKERS)
    assert error_codes <= set(te.ERROR_CODE_MARKERS), error_codes - set(
        te.ERROR_CODE_MARKERS
    )
    assert hook_decisions <= set(te.HOOK_DECISION_MARKERS), hook_decisions - set(
        te.HOOK_DECISION_MARKERS
    )
    assert warnings <= set(te.WARNING_MARKERS), warnings - set(te.WARNING_MARKERS)
    assert error_literals <= set(te.ERROR_LITERAL_MARKERS), error_literals - set(
        te.ERROR_LITERAL_MARKERS
    )
