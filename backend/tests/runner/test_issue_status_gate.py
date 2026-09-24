"""Hotfix-2 PR-3: ``IssueStatusGate`` reads ``issues.status`` before every
step of an issue run; a PREEMPT status stops the run exactly like CancelHook
(``stop_reason="cancelled"``) — ``issues.status`` is the authoritative cancel
signal, ``agent_runs.cancel_requested`` only an accelerator (ruling 1).

Driven through the production path: the adapter has NO ``stream`` attribute
(chat wiring hands the runner an LLMFallbackChain), so ``stream_turn`` takes
the buffered fallback — see tests/runner/test_turn_end_reasons.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.ai.runner.step_hooks import (
    IssueStatusGate,
    StepContext,
    StepDecision,
    StepHookChain,
)
from tests.runner.test_turn_end_reasons import _composed, _Rec

pytestmark = pytest.mark.unit
WIRING_SRC = Path("app/services/ai/chat/ai_library_chat_wiring.py")


def _scripted_status(statuses: list):
    seq = list(statuses)
    reads: list[int] = []

    async def _read(issue_id: int, **_kw):
        reads.append(issue_id)
        return seq[min(len(reads) - 1, len(seq) - 1)]

    return _read, reads


class _ToolThenTextAdapter:  # no ``stream`` attribute on purpose
    """Step 1 asks for a tool; any later step would answer with text."""

    def __init__(self):
        self.calls = 0

    async def call(self, composed, messages, **kw):
        self.calls += 1
        if self.calls == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "Skill",
                                        "arguments": '{"skill": "x"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "step 2 ran"},
                    "finish_reason": "stop",
                }
            ]
        }


def _runner(adapter, gate):
    from app.services.ai.runner.agent_runner import AgentRunner

    class _Tool:
        recorder = None

        async def execute(self, args):
            return {"ok": True}

    return AgentRunner(
        adapter=adapter, skill_tool=_Tool(), step_hooks=StepHookChain([gate])
    )


@pytest.mark.parametrize("status", ["cancelled", "done", "closed"])
async def test_issue_cancelled_before_step_two_stops_the_run(status):
    read, reads = _scripted_status(["in_progress", status])
    adapter = _ToolThenTextAdapter()
    rec = _Rec()
    chunks = []
    async for ch in _runner(adapter, IssueStatusGate(42, read_status=read)).stream_turn(
        _composed(),
        [{"role": "user", "content": "q"}],
        recorder=rec,
        auto_recorder=False,
    ):
        chunks.append(ch)
    assert adapter.calls == 1, "step 2 must not reach the model"
    assert reads == [42, 42]
    # ``delta_text`` — StreamChunk has no ``content``; this line used to read
    # one and passed only because the buffered path yielded no chunk at all.
    assert not any((c.delta_text or "") == "step 2 ran" for c in chunks)
    # The buffered path now ends on a terminal chunk naming the cancel
    # (framework hardening C4) — the issue workflow routes on it.
    assert chunks and (chunks[-1].usage or {}).get("stop_reason") == "cancelled"
    ends = rec.turn_ends()
    assert len(ends) == 1 and ends[0]["reason"] == "cancelled", ends


async def test_run_turn_path_files_cancelled_too():
    read, _ = _scripted_status(["in_progress", "cancelled"])
    adapter = _ToolThenTextAdapter()
    rec = _Rec()
    out = await _runner(adapter, IssueStatusGate(42, read_status=read)).run_turn(
        _composed(), [{"role": "user", "content": "q"}], recorder=rec
    )
    assert adapter.calls == 1
    assert out["stop_reason"] == "cancelled"
    assert out["cancelled"] is True


async def test_live_issue_continues():
    read, reads = _scripted_status(["in_progress"])
    ctx = StepContext(turn=1, step=2)
    gate = IssueStatusGate(7, read_status=read)
    assert await gate.before_llm_call(ctx) is StepDecision.CONTINUE
    assert ctx.stop_reason is None and reads == [7]


async def test_unreadable_status_continues():
    """Best-effort: ``None`` (read failed, row gone) is never a stop."""
    read, _ = _scripted_status([None])
    ctx = StepContext(turn=1, step=2)
    assert (
        await IssueStatusGate(7, read_status=read).before_llm_call(ctx)
        is StepDecision.CONTINUE
    )


def test_gate_default_reader_is_the_plain_status_read():
    from app.services.issues.issue_status_read import read_issue_status

    assert IssueStatusGate(1)._read_status is read_issue_status


def test_gate_uses_the_same_preempt_set_as_the_workflow():
    from app.services.issues.issue_status_read import PREEMPT_STATUSES
    from app.workflows.issue_lifecycle import PREEMPT_STATUSES as WORKFLOW_SET

    assert PREEMPT_STATUSES == WORKFLOW_SET


def test_wiring_hangs_the_gate_right_after_cancel_only_for_issue_runs():
    src = WIRING_SRC.read_text()
    assert re.search(
        r"issue_gate\s*=\s*\(?\s*\[IssueStatusGate\(issue_id\)\]\s*"
        r"if issue_id is not None\s*else \[\]",
        src,
    ), "the gate is built only when the run belongs to an issue"
    assert re.search(r"CancelHook\(\),\s*\*issue_gate,\s*PauseHook\(\)", src)
