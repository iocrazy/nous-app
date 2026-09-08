"""Phase 2a: ``StepContext.stop(reason)`` → ``TurnEndReason`` is one table.

Before this the non-stream path hardcoded ``"cancelled": True`` for every
hook STOP and the stream path returned bare (classified as cancelled), so a
pause or a typed question ended the turn labelled as a user cancel. The
exhaustiveness guard scans every ``ctx.stop("...")`` literal the hooks and
runner emit and asserts the table knows each one."""

import re
from pathlib import Path

import pytest

from app.services.ai.adapters.base import StreamChunk
from app.services.ai.runner import turn_end as te
from app.services.ai.runner.step_hooks import StepContext

pytestmark = pytest.mark.unit

RUNNER_DIR = Path("app/services/ai/runner")


def test_every_stop_literal_in_runner_and_hooks_is_mapped():
    files = [RUNNER_DIR / "step_hooks.py", RUNNER_DIR / "agent_runner.py"] + sorted(
        RUNNER_DIR.glob("*_hook.py")
    )
    src = "".join(p.read_text(encoding="utf-8") for p in files if p.exists())
    literals = set(re.findall(r'ctx\.stop\("([a-z_]+)"\)', src))
    assert "cancelled" in literals, "guard would pass vacuously"
    assert literals <= set(te.STOP_REASON_TO_TURN_END), literals - set(
        te.STOP_REASON_TO_TURN_END
    )


def test_stop_reason_table_covers_the_phase2a_vocabulary():
    assert te.STOP_REASON_TO_TURN_END == {
        "cancelled": te.TurnEndReason.CANCELLED,
        "paused": te.TurnEndReason.PAUSED,
        "awaiting_input": te.TurnEndReason.AWAITING_INPUT,
    }
    assert te.TurnEndReason.AWAITING_INPUT.value == "awaiting_input"


def test_classify_run_result_prefers_stop_reason():
    assert (
        te.classify_run_result({"stop_reason": "paused", "cancelled": False})[0]
        is te.TurnEndReason.PAUSED
    )
    assert (
        te.classify_run_result({"stop_reason": "awaiting_input"})[0]
        is te.TurnEndReason.AWAITING_INPUT
    )
    assert (
        te.classify_run_result({"stop_reason": "cancelled", "cancelled": True})[0]
        is te.TurnEndReason.CANCELLED
    )
    # legacy shape (no stop_reason) still classifies
    assert te.classify_run_result({"cancelled": True})[0] is te.TurnEndReason.CANCELLED


def test_classify_stream_end_reads_stop_reason_from_the_terminal_chunk():
    chunk = StreamChunk(finish_reason="stop", usage={"stop_reason": "paused"})
    assert te.classify_stream_end(chunk)[0] is te.TurnEndReason.PAUSED
    chunk = StreamChunk(finish_reason="stop", usage={"stop_reason": "awaiting_input"})
    assert te.classify_stream_end(chunk)[0] is te.TurnEndReason.AWAITING_INPUT
    # a bare return (no terminal chunk) is still a cancel
    assert te.classify_stream_end(None)[0] is te.TurnEndReason.CANCELLED


def test_step_context_rejects_unknown_stop_reason():
    with pytest.raises(ValueError):
        StepContext(turn=1, step=1).stop("nonsense")


async def test_unknown_stop_reason_escapes_the_chain_instead_of_continuing():
    """The chain contains flaky subscribers; a hook that stops with a word the
    table does not know is a bug and must not degrade into a silent CONTINUE."""
    from app.services.ai.runner.step_hooks import StepHookChain, UnknownStopReason

    class _Typo:
        name = "typo"

        async def before_llm_call(self, ctx):
            return ctx.stop("awaiting_inptu")

    with pytest.raises(UnknownStopReason):
        await StepHookChain([_Typo()]).run(StepContext(turn=1, step=1))
    ctx = StepContext(turn=1, step=1)
    ctx.stop("paused")
    assert ctx.stop_reason == "paused"


# ── the two runner paths file the hook's reason, not "cancelled" ─────────


class _Rec:
    def __init__(self):
        self.events = []
        self.views = {
            "view": {
                "question": {
                    "id": "q:1:1",
                    "kind": "user",
                    "prompt": "Which?",
                    "options": [{"label": "A", "description": None}],
                    "allow_free_text": False,
                    "asked_at": "2026-09-07T00:00:00Z",
                }
            }
        }

    async def record_event(self, event_type, payload, *, turn=None, step=None):
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


class _StopHook:
    name = "stopper"

    def __init__(self, reason):
        self._reason = reason

    async def before_llm_call(self, ctx):
        return ctx.stop(self._reason)


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


def _runner(adapter, reason):
    from app.services.ai.runner.agent_runner import AgentRunner
    from app.services.ai.runner.step_hooks import StepHookChain

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {}

    return AgentRunner(
        adapter=adapter,
        skill_tool=_Tool(),
        step_hooks=StepHookChain([_StopHook(reason)]),
    )


@pytest.mark.parametrize("reason", ["paused", "awaiting_input", "cancelled"])
async def test_run_turn_files_the_hook_stop_reason(reason):
    from unittest.mock import AsyncMock

    adapter = AsyncMock()
    adapter.call = AsyncMock(side_effect=AssertionError("must not call the model"))
    rec = _Rec()
    out = await _runner(adapter, reason).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=rec
    )
    assert out["stop_reason"] == reason
    assert out["cancelled"] is (reason == "cancelled")
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == reason, ends
    if reason == "awaiting_input":
        assert out["awaiting_input"] is True
        assert out["question"] == {
            "question_id": "q:1:1",
            "kind": "user",
            "prompt": "Which?",
            "options": [{"label": "A", "description": None}],
            "allow_free_text": False,
            "asked_at": "2026-09-07T00:00:00Z",
        }
    else:
        assert "awaiting_input" not in out


@pytest.mark.parametrize("reason", ["paused", "awaiting_input", "cancelled"])
async def test_stream_turn_files_the_hook_stop_reason(reason):
    from unittest.mock import AsyncMock

    adapter = AsyncMock()

    async def _stream(composed, messages, **kw):
        raise AssertionError("must not call the model")
        yield  # pragma: no cover — makes this an async generator

    adapter.stream = _stream
    rec = _Rec()
    chunks = []
    async for ch in _runner(adapter, reason).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        chunks.append(ch)
    assert chunks and chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage == {"stop_reason": reason}
    assert chunks[-1].tool_call_trace == []  # carried, like every terminal chunk
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == reason, ends
